from __future__ import annotations

import hashlib
import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import pymupdf

import pdf2zh
from pdf2zh import high_level
from pdf2zh.high_level import TranslationReport
from pdf2zh.layout_qa import LayoutQAReport, LayoutWarning
from scripts import translate_pdf


def _hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_pdf(
    path: Path,
    pages: list[tuple[float, float, int, str]],
) -> None:
    document = pymupdf.open()
    for width, height, rotation, text in pages:
        page = document.new_page(width=width, height=height)
        page.insert_text((36, 72), text)
        page.set_rotation(rotation)
    document.save(path)
    document.close()


class P0DeliveryGateTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name).resolve()
        self.source = self.root / "guide.pdf"
        _write_pdf(
            self.source,
            [
                (300, 400, 0, "SOURCE PAGE ONE"),
                (300, 400, 0, "SOURCE PAGE TWO"),
            ],
        )
        self.output = self.root / "output"

    def tearDown(self) -> None:
        self.temporary.cleanup()

    @staticmethod
    def _report() -> TranslationReport:
        return TranslationReport(
            translatable_segments=1,
            pages_processed=2,
            total_segments=1,
            translated_segments=1,
        )

    def _engine(self, builder):
        def run(source, temp_output, *_args):
            tagged_source = Path(temp_output) / "fake-native-input.pdf"
            tagged_source.write_bytes(Path(source).read_bytes())
            with pymupdf.open(tagged_source) as document:
                for page, identity in zip(document, _args[6]["page_identity"]):
                    document.xref_set_key(
                        page.xref, translate_pdf.PAGE_ID_KEY, f"({identity})"
                    )
                document.saveIncr()
            candidate = Path(temp_output) / f"{Path(source).stem}-mono.pdf"
            builder(tagged_source, candidate)
            return self._report()

        return run

    @staticmethod
    def _copy_candidate(source: Path, candidate: Path) -> None:
        candidate.write_bytes(source.read_bytes())

    @staticmethod
    def _extra_page_candidate(source: Path, candidate: Path) -> None:
        with pymupdf.open(source) as document:
            document.new_page(width=300, height=400)
            document.save(candidate)

    def _translate(self, builder=None, **kwargs):
        builder = builder or self._copy_candidate
        with mock.patch.object(
            translate_pdf, "_run_engine", side_effect=self._engine(builder)
        ):
            return translate_pdf.translate_pdf(self.source, self.output, **kwargs)

    def test_native_core_unavailable_leaves_no_destination(self):
        with mock.patch.object(
            translate_pdf,
            "_require_core",
            side_effect=translate_pdf.TranslationError("native core unavailable"),
        ):
            with self.assertRaisesRegex(translate_pdf.TranslationError, "unavailable"):
                translate_pdf.translate_pdf(self.source, self.output)
        self.assertFalse((self.output / "guide-vi.pdf").exists())

    def test_bundled_mono_document_carries_runner_page_identities(self):
        identities = ("page-one", "page-two")
        with pymupdf.open(stream=self.source.read_bytes()) as document:
            high_level.install_page_identities(
                document, {"page_identity": identities}
            )
            candidate = self.root / "native-candidate.pdf"
            candidate.write_bytes(document.write())
        facts = translate_pdf._pdf_facts(candidate, role="test native candidate")
        self.assertEqual(high_level.PAGE_ID_KEY, translate_pdf.PAGE_ID_KEY)
        self.assertEqual(facts.page_ids, identities)

    def test_page_count_mismatch_is_not_published(self):
        with self.assertRaisesRegex(translate_pdf.TranslationError, "page-count"):
            self._translate(self._extra_page_candidate)
        self.assertFalse((self.output / "guide-vi.pdf").exists())

    def test_page_size_mismatch_is_not_published(self):
        def resize(source: Path, candidate: Path) -> None:
            with pymupdf.open(source) as document:
                document[0].set_mediabox(pymupdf.Rect(0, 0, 320, 400))
                document.save(candidate)

        with self.assertRaisesRegex(translate_pdf.TranslationError, "page-size"):
            self._translate(resize)
        self.assertFalse((self.output / "guide-vi.pdf").exists())

    def test_rotation_mismatch_is_not_published(self):
        def rotate(source: Path, candidate: Path) -> None:
            with pymupdf.open(source) as document:
                document[0].set_rotation(90)
                document.save(candidate)

        with self.assertRaisesRegex(translate_pdf.TranslationError, "rotation"):
            self._translate(rotate)
        self.assertFalse((self.output / "guide-vi.pdf").exists())

    def test_reversed_page_mapping_is_not_published(self):
        def reverse(source: Path, candidate: Path) -> None:
            with pymupdf.open(source) as document:
                document.select([1, 0])
                document.save(candidate)

        with self.assertRaisesRegex(translate_pdf.TranslationError, "page-mapping"):
            self._translate(reverse)
        self.assertFalse((self.output / "guide-vi.pdf").exists())

    def test_source_mutation_is_detected_before_publish(self):
        original = _hash(self.source)

        def mutate_source(engine_source: Path, candidate: Path) -> None:
            with pymupdf.open(self.source) as document:
                metadata = document.metadata
                metadata["subject"] = "mutated by test engine"
                document.set_metadata(metadata)
                document.saveIncr()
            candidate.write_bytes(engine_source.read_bytes())

        with self.assertRaisesRegex(translate_pdf.TranslationError, "Source PDF changed"):
            self._translate(mutate_source)
        self.assertNotEqual(_hash(self.source), original)
        self.assertFalse((self.output / "guide-vi.pdf").exists())

    def test_destination_cannot_alias_and_replace_the_source(self):
        self.output.mkdir()
        destination = self.output / "guide-vi.pdf"
        os.link(self.source, destination)
        original = self.source.read_bytes()
        with (
            mock.patch.object(translate_pdf, "_run_engine") as engine,
            self.assertRaisesRegex(translate_pdf.TranslationError, "replace the source"),
        ):
            translate_pdf.translate_pdf(
                self.source, self.output, overwrite=True, target_language="vi"
            )
        engine.assert_not_called()
        self.assertEqual(self.source.read_bytes(), original)
        self.assertEqual(destination.read_bytes(), original)

    def test_wrong_public_engine_is_rejected_before_core_or_publish(self):
        with (
            mock.patch.object(translate_pdf, "_require_core") as core,
            mock.patch.object(translate_pdf, "_run_engine") as engine,
            self.assertRaisesRegex(translate_pdf.TranslationError, "Unsupported"),
        ):
            translate_pdf.translate_pdf(self.source, self.output, engine="word")
        core.assert_not_called()
        engine.assert_not_called()
        self.assertFalse((self.output / "guide-vi.pdf").exists())

    def test_validator_execution_failure_is_not_published(self):
        with (
            mock.patch.object(
                translate_pdf,
                "_run_engine",
                side_effect=self._engine(self._copy_candidate),
            ),
            mock.patch(
                "pdf2zh.layout_qa.run_layout_qa",
                side_effect=RuntimeError("validator unavailable"),
            ),
            self.assertRaisesRegex(translate_pdf.TranslationError, "validator failed"),
        ):
            translate_pdf.translate_pdf(self.source, self.output)
        self.assertFalse((self.output / "guide-vi.pdf").exists())

    def test_non_structural_layout_warning_remains_nonfatal(self):
        warning = LayoutWarning("suspicious_micro_font", 1, (0, 0, 1, 1), "fixture")
        with (
            mock.patch.object(
                translate_pdf,
                "_run_engine",
                side_effect=self._engine(self._copy_candidate),
            ),
            mock.patch(
                "pdf2zh.layout_qa.run_layout_qa",
                return_value=LayoutQAReport(2, (warning,), 0.0),
            ),
        ):
            result = translate_pdf.translate_pdf(self.source, self.output)
        self.assertEqual(result.native_validator_status, "PASS")

    def test_stale_output_without_overwrite_remains_unchanged(self):
        self.output.mkdir()
        destination = self.output / "guide-vi.pdf"
        destination.write_bytes(b"old-valid-artifact")
        old_hash = _hash(destination)
        with self.assertRaisesRegex(translate_pdf.TranslationError, "already exists"):
            self._translate()
        self.assertEqual(_hash(destination), old_hash)

    def test_valid_candidate_atomically_replaces_stale_output_with_provenance(self):
        self.output.mkdir()
        destination = self.output / "guide-vi.pdf"
        destination.write_bytes(b"old-valid-artifact")
        old_hash = _hash(destination)
        source_hash = _hash(self.source)

        result = self._translate(overwrite=True, source_language="en")

        self.assertNotEqual(_hash(destination), old_hash)
        self.assertEqual(result.path, destination)
        self.assertEqual(result.output_file, destination)
        self.assertEqual(result.execution_backend, "bundled_pdf2zh")
        self.assertEqual(result.core_version, pdf2zh.__version__)
        self.assertEqual(result.ruleset, pdf2zh.__ruleset__)
        self.assertEqual(result.source_file, self.source)
        self.assertEqual(result.source_sha256, source_hash)
        self.assertEqual(result.output_sha256, _hash(destination))
        self.assertEqual(result.source_page_count, 2)
        self.assertEqual(result.output_page_count, 2)
        self.assertIs(result.page_geometry_match, True)
        self.assertIs(result.page_mapping_match, True)
        self.assertEqual(result.engine, "google")
        self.assertEqual(result.source_language, "en")
        self.assertEqual(result.target_language, "vi")
        self.assertEqual(result.native_validator_status, "PASS")

        facts = translate_pdf._pdf_facts(destination, role="test final")
        expected_ids = tuple(
            translate_pdf._page_identity(source_hash, page)
            for page in range(result.source_page_count)
        )
        self.assertEqual(facts.page_ids, expected_ids)

    def test_invalid_overwrite_keeps_the_previous_destination_byte_identical(self):
        self.output.mkdir()
        destination = self.output / "guide-vi.pdf"
        destination.write_bytes(b"old-valid-artifact")
        old_bytes = destination.read_bytes()
        with self.assertRaisesRegex(translate_pdf.TranslationError, "page-count"):
            self._translate(self._extra_page_candidate, overwrite=True)
        self.assertEqual(destination.read_bytes(), old_bytes)

    def test_staged_hash_mismatch_does_not_publish(self):
        real_copy = shutil.copyfile

        def corrupt_stage(source, target, *args, **kwargs):
            if Path(target).name.startswith(".guide-vi.pdf"):
                Path(target).write_bytes(b"corrupt staged bytes")
                return str(target)
            return real_copy(source, target, *args, **kwargs)

        with (
            mock.patch.object(
                translate_pdf,
                "_run_engine",
                side_effect=self._engine(self._copy_candidate),
            ),
            mock.patch.object(translate_pdf.shutil, "copyfile", side_effect=corrupt_stage),
            self.assertRaisesRegex(translate_pdf.TranslationError, "Staged PDF hash"),
        ):
            translate_pdf.translate_pdf(self.source, self.output)
        self.assertFalse((self.output / "guide-vi.pdf").exists())

    def test_partial_selection_keeps_full_document_topology_and_mapping(self):
        result = self._translate(pages="2")
        self.assertEqual(result.source_page_count, 2)
        self.assertEqual(result.output_page_count, 2)
        self.assertIs(result.page_geometry_match, True)
        self.assertIs(result.page_mapping_match, True)

    def test_handoff_extract_only_remains_functional_and_preserves_source(self):
        emitted = self.root / "segments.jsonl"
        source_hash = _hash(self.source)

        def extract(source, _temp_output, *_args):
            envs = _args[6]
            Path(envs["segments_out"]).write_text("", encoding="utf-8")
            return self._report()

        with mock.patch.object(translate_pdf, "_run_engine", side_effect=extract):
            result = translate_pdf.translate_pdf(
                self.source,
                None,
                engine="handoff",
                emit_segments=emitted,
            )
        self.assertIsNone(result.path)
        self.assertTrue(emitted.exists())
        self.assertEqual(_hash(self.source), source_hash)
        self.assertEqual(result.source_sha256, source_hash)
        self.assertEqual(result.execution_backend, "bundled_pdf2zh")
        self.assertIsNone(result.native_validator_status)


if __name__ == "__main__":
    unittest.main()
