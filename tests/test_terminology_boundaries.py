from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from pdf2zh import terminology
from pdf2zh.handoff import assess_handoff_translations
from pdf2zh.terminology import TerminologyConsistencyError, validate_confirmed_terminology


class TerminologyBoundaryTests(unittest.TestCase):
    def test_standalone_ascii_term_requires_its_translation(self):
        for source in ("pin", "PIN", "pin number", "connector pin", "(pin)"):
            with self.subTest(source=source):
                with self.assertRaises(TerminologyConsistencyError):
                    validate_confirmed_terminology(source, "thiếu thuật ngữ", {"pin": "chân cắm"})
                validate_confirmed_terminology(source, "chân cắm", {"pin": "chân cắm"})

    def test_ascii_substrings_do_not_activate_terminology(self):
        for source in ("shipping", "spindle", "mapping", "pin_number", "pin2", "Remove the shipping label."):
            with self.subTest(source=source):
                validate_confirmed_terminology(source, "Gỡ nhãn vận chuyển.", {"pin": "chân cắm"})

    def test_ascii_target_term_also_requires_a_boundary(self):
        with self.assertRaises(TerminologyConsistencyError):
            validate_confirmed_terminology("connector", "shipping", {"connector": "pin"})
        validate_confirmed_terminology("connector", "PIN", {"connector": "pin"})

    def test_cjk_phrases_and_punctuation_keep_existing_matching(self):
        pairs = [
            ("검수전", "trước nghiệm thu", {"검수": "nghiệm thu"}),
            ("完成原点回归后启动", "khởi động sau khi về gốc", {"原点回归": "về gốc"}),
            ("Check Servo Motor status", "kiểm tra động cơ servo", {"Servo Motor": "động cơ servo"}),
            ("check I/O", "kiểm tra vào/ra", {"I/O": "vào/ra"}),
        ]
        for source, target, terms in pairs:
            with self.subTest(source=source):
                validate_confirmed_terminology(source, target, terms)
                with self.assertRaises(TerminologyConsistencyError):
                    validate_confirmed_terminology(source, "thiếu thuật ngữ", terms)

    def test_correct_shipping_translation_is_accepted_without_retry(self):
        source = {"segment_id": "shipping-label", "src": "Remove the shipping label."}
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "translations.jsonl"
            path.write_text(json.dumps({**source, "dst": "Gỡ nhãn vận chuyển."}, ensure_ascii=False) + "\n", encoding="utf-8")
            assessment = assess_handoff_translations([source], path, terminology={"pin": "chân cắm"})
            self.assertEqual(len(assessment.accepted), 1)
            self.assertEqual(assessment.retry, ())

    def test_matcher_revision_only_invalidates_terminology_scoped_cache(self):
        without_terms = terminology.terminology_fingerprint({})
        with_terms = terminology.terminology_fingerprint({"pin": "chân cắm"})
        with mock.patch.object(terminology, "TERMINOLOGY_MATCHER_VERSION", "different-matcher"):
            self.assertEqual(without_terms, terminology.terminology_fingerprint({}))
            self.assertNotEqual(with_terms, terminology.terminology_fingerprint({"pin": "chân cắm"}))


if __name__ == "__main__":
    unittest.main()
