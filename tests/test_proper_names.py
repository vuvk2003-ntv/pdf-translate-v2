from __future__ import annotations

import json
import re
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from pdf2zh import terminology
from pdf2zh.cache import clean_test_db, init_test_db
from pdf2zh.converter import needs_model_translation
from pdf2zh.handoff import assess_handoff_translations
from pdf2zh.invariants import (
    TRANSLATION_RULES_VERSION,
    VERIFIED_PROPER_NAMES,
    VerifiedProperNameError,
    find_verified_proper_names,
    validate_verified_proper_names,
)
from pdf2zh.translator import (
    GoogleTranslator,
    HandoffTranslator,
    encode_formula_placeholders,
    restore_formula_placeholders,
    segment_identifier,
)


NAME = "Inovance Technology"
MIXED_SOURCE = f"Check the {NAME} robot controller."
MIXED_TARGET = f"Kiểm tra bộ điều khiển robot {NAME}."


def _write_jsonl(path: Path, records: list[dict]) -> Path:
    path.write_text(
        "".join(json.dumps(record, ensure_ascii=False) + "\n" for record in records),
        encoding="utf-8",
    )
    return path


def _proper_token(text: str) -> str:
    tokens = re.findall(r"<b\d+></b\d+>", text)
    if len(tokens) != 1:
        raise AssertionError(f"expected one proper-name token, found {tokens!r}")
    return tokens[0]


class MockGoogleTranslator(GoogleTranslator):
    name = "p1-google-test"

    def __init__(self, responder, *, terminology_map=None, ignore_cache=False):
        self.responder = responder
        self.provider_inputs: list[str] = []
        super().__init__(
            "auto",
            "vi",
            ignore_cache=ignore_cache,
            envs={"terminology": terminology_map or {}},
        )

    def do_translate(self, text: str) -> str:
        self.provider_inputs.append(text)
        return self.responder(text)


class ProperNameMatcherTests(unittest.TestCase):
    def test_registry_contains_only_the_reviewed_fixture(self):
        self.assertEqual(VERIFIED_PROPER_NAMES, (NAME,))

    def test_exact_name_matches_with_punctuation_and_possessive(self):
        for source in (
            NAME,
            f'"{NAME},"',
            f"({NAME})",
            f"{NAME}'s controller",
        ):
            with self.subTest(source=source):
                spans = find_verified_proper_names(source)
                self.assertEqual(len(spans), 1)
                self.assertEqual(source[spans[0].start : spans[0].end], NAME)

    def test_ascii_boundaries_prevent_substring_matches(self):
        for source in (
            "Inovance TechnologyX",
            "MyInovance Technology",
            "Inovance Technologies",
            "INOVANCE TECHNOLOGY",
            "innovation technology",
            "Technology",
        ):
            with self.subTest(source=source):
                self.assertEqual(find_verified_proper_names(source), ())

    def test_longest_overlapping_alias_wins(self):
        spans = find_verified_proper_names(
            NAME,
            names=("Inovance", NAME),
        )
        self.assertEqual([(span.name, span.start, span.end) for span in spans], [(NAME, 0, len(NAME))])


class ProperNameInvariantTests(unittest.TestCase):
    def test_missing_changed_and_duplicated_name_are_rejected(self):
        for target in (
            "Công nghệ đổi mới",
            "Inovance Technologies",
            f"{NAME} {NAME}",
        ):
            with self.subTest(target=target):
                with self.assertRaises(VerifiedProperNameError):
                    validate_verified_proper_names(NAME, target)

    def test_equal_repeated_source_and_target_counts_are_accepted(self):
        validate_verified_proper_names(NAME, NAME)
        validate_verified_proper_names(
            f"{NAME} works with {NAME}.",
            f"{NAME} hoạt động cùng {NAME}.",
        )

    def test_missing_or_invented_occurrence_is_rejected(self):
        with self.assertRaises(VerifiedProperNameError):
            validate_verified_proper_names(f"{NAME} and {NAME}", NAME)
        with self.assertRaises(VerifiedProperNameError):
            validate_verified_proper_names("Generic robot controller", NAME)

    def test_explicit_document_terminology_overrides_default_preservation(self):
        validate_verified_proper_names(
            MIXED_SOURCE,
            "Kiểm tra bộ điều khiển robot Inovance.",
            {NAME: "Inovance"},
        )


class ProperNameClassifierTests(unittest.TestCase):
    def test_standalone_name_is_preserved_but_mixed_prose_still_translates(self):
        self.assertFalse(needs_model_translation(NAME))
        self.assertFalse(needs_model_translation(f' "{NAME}," '))
        self.assertTrue(needs_model_translation(MIXED_SOURCE))
        self.assertTrue(needs_model_translation(NAME, {NAME: "Inovance"}))

    def test_generic_words_and_titles_remain_translatable(self):
        for source in (
            "technology",
            "industrial technology",
            "robot technology",
            "innovation",
            "electric system",
            "display settings",
            "Industrial Robot",
            "Safety Requirements",
            "Motion Instructions",
            "Revision History",
            "Industrial Robot Instructions Guide",
        ):
            with self.subTest(source=source):
                self.assertTrue(needs_model_translation(source))

    def test_existing_standalone_technical_policy_is_unchanged(self):
        self.assertFalse(needs_model_translation("Servo Motor"))
        self.assertFalse(needs_model_translation("SCARA Robot"))
        self.assertFalse(needs_model_translation("JOG"))


class ProperNameGooglePathTests(unittest.TestCase):
    def setUp(self) -> None:
        self.test_db = init_test_db()

    def tearDown(self) -> None:
        clean_test_db(self.test_db)

    def test_mixed_prose_is_translated_while_provider_never_sees_name(self):
        def respond(masked: str) -> str:
            self.assertNotIn(NAME, masked)
            return f"Kiểm tra bộ điều khiển robot {_proper_token(masked)}."

        translator = MockGoogleTranslator(respond)
        self.assertEqual(translator.translate(MIXED_SOURCE), MIXED_TARGET)
        self.assertEqual(translator.metrics()["translation_requests"], 1)
        self.assertNotEqual(translator.translate(MIXED_SOURCE), MIXED_SOURCE)

    def test_required_document_title_fixture_translates_around_exact_name(self):
        source = (
            'The "Industrial Robot Instructions Guide" describes programming '
            f"{NAME} industrial robots."
        )
        expected = (
            '"Hướng dẫn Robot Công nghiệp" mô tả cách lập trình robot công nghiệp '
            f"{NAME}."
        )

        def respond(masked: str) -> str:
            self.assertNotIn(NAME, masked)
            return expected.replace(NAME, _proper_token(masked))

        translator = MockGoogleTranslator(respond)
        self.assertEqual(translator.translate(source), expected)

    def test_standalone_name_avoids_provider_request(self):
        translator = MockGoogleTranslator(
            lambda _text: self.fail("standalone proper name reached provider")
        )
        self.assertEqual(translator.translate(f"({NAME})"), f"({NAME})")
        self.assertEqual(translator.metrics()["translation_requests"], 0)
        self.assertEqual(translator.provider_inputs, [])

    def test_style_formula_and_technical_tokens_remain_independent(self):
        source = f"<s1>{NAME}</s1> Set D100 to {{v0}}."
        encoded = encode_formula_placeholders(source)

        def respond(masked: str) -> str:
            self.assertNotIn(NAME, masked)
            proper = _proper_token(masked.replace("<b0></b0>", ""))
            return f"<s1>{proper}</s1> Đặt D100 thành <b0></b0>."

        translator = MockGoogleTranslator(respond)
        translated = translator.translate(encoded)
        restored = restore_formula_placeholders(source, translated)
        self.assertEqual(restored, f"<s1>{NAME}</s1> Đặt D100 thành {{v0}}.")
        self.assertNotRegex(restored, r"<b\d+>|</b\d+>")

    def test_damaged_or_duplicated_provider_name_token_never_enters_cache(self):
        for response in (
            "Kiểm tra bộ điều khiển robot Công nghệ đổi mới.",
            f"Kiểm tra {{token}} và {NAME}.",
        ):
            with self.subTest(response=response):
                def respond(masked: str, candidate=response) -> str:
                    return candidate.replace("{token}", _proper_token(masked))

                translator = MockGoogleTranslator(respond)
                with self.assertRaises(VerifiedProperNameError):
                    translator.translate(MIXED_SOURCE)
                self.assertIsNone(translator.cache.get(MIXED_SOURCE))

    def test_korean_and_chinese_mixed_prose_share_the_same_masking(self):
        fixtures = (
            (
                f"{NAME} 위치 확인 후 Start 버튼을 누른다.",
                f"Sau khi kiểm tra vị trí {NAME}, nhấn nút Start.",
            ),
            (
                f"检查 {NAME} 控制器。",
                f"Kiểm tra bộ điều khiển {NAME}.",
            ),
        )
        for source, expected in fixtures:
            with self.subTest(source=source):
                def respond(masked: str, result=expected) -> str:
                    tokens = re.findall(r"<b\d+></b\d+>", masked)
                    translated = result.replace(NAME, tokens[0])
                    if "Start" not in masked and "Start" in result:
                        translated = translated.replace("Start", tokens[1])
                    return translated

                translator = MockGoogleTranslator(
                    respond
                )
                self.assertEqual(translator.translate(source), expected)


class ProperNameHandoffTests(unittest.TestCase):
    def setUp(self) -> None:
        self.test_db = init_test_db()
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)

    def tearDown(self) -> None:
        self.temporary.cleanup()
        clean_test_db(self.test_db)

    def test_bad_handoff_pair_is_skipped_and_reemitted_for_retry(self):
        table = _write_jsonl(
            self.root / "bad.jsonl",
            [{"src": MIXED_SOURCE, "dst": "Kiểm tra bộ điều khiển robot Công nghệ đổi mới."}],
        )
        misses = self.root / "misses.jsonl"
        translator = HandoffTranslator(
            "en",
            "vi",
            ignore_cache=True,
            envs={"segments_in": str(table), "segments_out": str(misses)},
        )
        self.assertEqual(translator.translate(MIXED_SOURCE), MIXED_SOURCE)
        records = [json.loads(line) for line in misses.read_text(encoding="utf-8").splitlines()]
        self.assertEqual(records[0]["src"], MIXED_SOURCE)
        self.assertEqual(translator.metrics()["handoff_table_hits"], 0)

    def test_correct_handoff_pair_is_accepted(self):
        table = _write_jsonl(
            self.root / "good.jsonl",
            [{"src": MIXED_SOURCE, "dst": MIXED_TARGET}],
        )
        translator = HandoffTranslator(
            "en", "vi", ignore_cache=True, envs={"segments_in": str(table)}
        )
        self.assertEqual(translator.translate(MIXED_SOURCE), MIXED_TARGET)
        self.assertEqual(translator.metrics()["handoff_table_hits"], 1)

    def test_handoff_assessment_uses_distinct_proper_name_retry_category(self):
        identifier = segment_identifier(MIXED_SOURCE)
        sources = [{"segment_id": identifier, "src": MIXED_SOURCE}]
        bad = _write_jsonl(
            self.root / "assessment.jsonl",
            [{"segment_id": identifier, "src": MIXED_SOURCE, "dst": "Công nghệ đổi mới"}],
        )
        assessment = assess_handoff_translations(sources, bad)
        self.assertEqual(assessment.accepted, ())
        self.assertEqual(assessment.retry[0]["retry_reason"], "VerifiedProperNameError")

    def test_explicit_terminology_mapping_is_accepted_by_handoff(self):
        target = "Kiểm tra bộ điều khiển robot Inovance."
        table = _write_jsonl(
            self.root / "mapped.jsonl",
            [{"src": MIXED_SOURCE, "dst": target}],
        )
        translator = HandoffTranslator(
            "en",
            "vi",
            ignore_cache=True,
            envs={"segments_in": str(table), "terminology": {NAME: "Inovance"}},
        )
        self.assertEqual(translator.translate(MIXED_SOURCE), target)


class ProperNameCacheTests(unittest.TestCase):
    def setUp(self) -> None:
        self.test_db = init_test_db()

    def tearDown(self) -> None:
        clean_test_db(self.test_db)

    def _translator(self, response: str, terminology_map=None) -> MockGoogleTranslator:
        return MockGoogleTranslator(
            lambda masked: response.replace(NAME, _proper_token(masked))
            if NAME not in masked
            else response,
            terminology_map=terminology_map,
        )

    def test_rule_revision_moves_old_bad_cache_out_of_namespace(self):
        self.assertEqual(TRANSLATION_RULES_VERSION, "code4life-translation-v5")
        with mock.patch.object(
            terminology, "TRANSLATION_RULES_VERSION", "code4life-translation-v3"
        ):
            old = self._translator("Công nghệ đổi mới")
            old.cache.set(MIXED_SOURCE, "Công nghệ đổi mới")

        current = self._translator(MIXED_TARGET)
        self.assertEqual(current.translate(MIXED_SOURCE), MIXED_TARGET)
        self.assertEqual(current.metrics()["cache_hits"], 0)
        self.assertEqual(current.metrics()["translation_requests"], 1)

    def test_new_valid_result_caches_normally(self):
        first = self._translator(MIXED_TARGET)
        self.assertEqual(first.translate(MIXED_SOURCE), MIXED_TARGET)
        second = self._translator("should not be used")
        self.assertEqual(second.translate(MIXED_SOURCE), MIXED_TARGET)
        self.assertEqual(second.metrics()["cache_hits"], 1)
        self.assertEqual(second.metrics()["translation_requests"], 0)

    def test_terminology_fingerprint_keeps_same_map_and_separates_changed_map(self):
        first_terms = {NAME: "Inovance"}
        changed_terms = {NAME: "Công ty Inovance"}
        first_target = "Kiểm tra bộ điều khiển robot Inovance."
        changed_target = "Kiểm tra bộ điều khiển robot Công ty Inovance."
        first = self._translator(first_target, first_terms)
        self.assertEqual(first.translate(MIXED_SOURCE), first_target)
        same = self._translator("unused", first_terms)
        self.assertEqual(same.translate(MIXED_SOURCE), first_target)
        self.assertEqual(same.metrics()["cache_hits"], 1)
        changed = self._translator(changed_target, changed_terms)
        self.assertEqual(changed.translate(MIXED_SOURCE), changed_target)
        self.assertEqual(changed.metrics()["cache_hits"], 0)


if __name__ == "__main__":
    unittest.main()
