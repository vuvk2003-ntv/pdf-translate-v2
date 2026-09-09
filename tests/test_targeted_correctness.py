from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from pdf2zh.cache import clean_test_db, init_test_db
from pdf2zh.converter import needs_model_translation
from pdf2zh.handoff import build_handoff_batches
from pdf2zh.invariants import TechnicalInvariantError, validate_technical_invariants
from pdf2zh.translator import HandoffTranslator, segment_identifier, validate_translation_result


class ClassifierRegressionTests(unittest.TestCase):
    def test_opcode_prefix_does_not_hide_same_line_prose(self):
        for source in ('SET the required pressure to 0.5 MPa.',
                       'MOV D100 D200; Adjust the supporting structure.',
                       'IF the guard is open, stop the machine.'):
            with self.subTest(source=source):
                self.assertTrue(needs_model_translation(source))

    def test_actual_code_and_standalone_terms_still_preserved(self):
        for source in ('MOV D100 D200', 'SET M100', 'IF ready THEN MOVJ P001',
                       'DFROM_M U0 K0 D100 K2', 'NWAIT', 'Servo Motor', 'PCW'):
            with self.subTest(source=source):
                self.assertFalse(needs_model_translation(source))

    def test_uppercase_english_set_can_be_translated_as_prose(self):
        validate_technical_invariants('SET the pressure to 0.5 MPa.', 'Đặt áp suất thành 0.5 MPa.')


class RelationRegressionTests(unittest.TestCase):
    def test_swapped_register_axis_and_parameter_values_are_rejected(self):
        pairs = [
            ('Set D100 to 10 and D200 to 20.', 'Đặt D100 thành 20 và D200 thành 10.'),
            ('D100=10; D200=20', 'D100=20; D200=10'),
            ('X axis: 10 mm; Y axis: 20 mm', 'Trục X: 20 mm; trục Y: 10 mm'),
            ('Parameter 1: 10; parameter 2: 20', 'Tham số 1: 20; tham số 2: 10'),
        ]
        for source, target in pairs:
            with self.subTest(source=source), self.assertRaises(TechnicalInvariantError):
                validate_technical_invariants(source, target)

    def test_reordered_clauses_preserve_association(self):
        validate_technical_invariants('Set D100 to 10 and D200 to 20.',
                                      'Đặt D200 thành 20 và D100 thành 10.')
        validate_technical_invariants('X axis: 10mm; Y axis: 20mm',
                                      'Trục Y: 20 mm; trục X: 10 mm')

    def test_reversed_assignment_wording_preserves_association(self):
        validate_technical_invariants('Set D100 to 10 and D200 to 20.',
                                      'Gán 10 cho D100 và 20 cho D200.')


class SemanticRegressionTests(unittest.TestCase):
    def test_supported_polarity_and_requirement_loss_is_rejected(self):
        pairs = [
            ('Do not enable Servo ON before the guard is closed.', 'Cho phép Servo ON trước khi cửa bảo vệ được đóng.'),
            ('The operator must close the guard.', 'Người vận hành nên đóng cửa bảo vệ.'),
            ('The operator should close the guard.', 'Người vận hành phải đóng cửa bảo vệ.'),
            ('운전하지 마십시오.', 'Hãy vận hành.'),
            ('禁止启动设备。', 'Khởi động thiết bị.'),
            ('必須完成原點復歸。', 'Nên hoàn tất về gốc.'),
            ('Set D100 to 250 before enabling Servo ON.', 'Đặt D100 thành 250 sau khi bật Servo ON.'),
        ]
        for source, target in pairs:
            with self.subTest(source=source), self.assertRaises(TechnicalInvariantError):
                validate_translation_result(source, target, target_language='vi')

    def test_correct_paraphrases_and_epistemic_may_pass(self):
        pairs = [
            ('Do not enable Servo ON.', 'Không được bật Servo ON.'),
            ('The operator must close the guard.', 'Người vận hành bắt buộc đóng cửa bảo vệ.'),
            ('The operator should close the guard.', 'Khuyến nghị người vận hành đóng cửa bảo vệ.'),
            ('It may be subject to change.', 'Thông tin này có thể thay đổi.'),
            ('It is not required to restart.', 'Không cần khởi động lại.'),
            ('Set D100 to 250 before enabling Servo ON.', 'Bật Servo ON sau khi đặt D100 thành 250.'),
            ('Set D100 to 250 before enabling Servo ON.', 'Trước khi bật Servo ON, đặt D100 thành 250.'),
            ('Before enabling Servo ON, set D100 to 250.', 'Đặt D100 thành 250 trước khi bật Servo ON.'),
        ]
        for source, target in pairs:
            with self.subTest(source=source):
                validate_translation_result(source, target, target_language='vi')

    def test_vietnamese_cues_do_not_restrict_other_targets(self):
        validate_translation_result('Do not start.', 'Nicht starten.', target_language='de')


class IdentityAndCacheRegressionTests(unittest.TestCase):
    def setUp(self):
        self.db = init_test_db()
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()
        clean_test_db(self.db)

    def translator(self, records=None, terms=None):
        envs = {'terminology': terms or {}}
        if records is not None:
            path = self.root / 'table.jsonl'
            path.write_text(''.join(json.dumps(x, ensure_ascii=False) + '\n' for x in records), encoding='utf-8')
            envs['segments_in'] = str(path)
        return HandoffTranslator('auto', 'vi', envs=envs)

    def test_missing_or_mismatched_identity_never_uses_another_source_entry(self):
        translator = self.translator([{'segment_id': 'heading', 'src': 'Manual', 'dst': 'Sổ tay'}])
        self.assertEqual(translator.translate_with_identity('Manual', 'mode'), 'Manual')
        self.assertFalse(translator.has_translation_for_identity('Manual', 'mode'))
        self.assertEqual(translator.translate_with_identity('Different', 'heading'), 'Different')

    def test_rebuild_missing_identity_cannot_fall_back_to_cache(self):
        source, target = 'Disconnect power before maintenance.', 'Ngắt nguồn trước khi bảo trì.'
        cached = self.translator([{'src': source, 'dst': target}])
        cached.translate(source)
        rebuilt = self.translator([])
        identity = segment_identifier(source)
        self.assertEqual(rebuilt.translate_with_identity(source, identity), source)
        self.assertFalse(rebuilt.has_translation_for_identity(source, identity))

    def test_same_terms_reuse_cache_changed_terms_miss(self):
        source, target = 'Check the payload before operation.', 'Kiểm tra tải trọng trước khi vận hành.'
        identity = segment_identifier(source)
        first = self.translator([{'segment_id': identity, 'src': source, 'dst': target}], {'payload': 'tải trọng'})
        self.assertEqual(first.translate_with_identity(source, identity), target)
        same = self.translator(terms={'payload': 'tải trọng'})
        self.assertEqual(same.translate_with_identity(source, segment_identifier(source)), target)
        self.assertEqual(same.metrics()['cache_hits'], 1)
        changed = self.translator(terms={'payload': 'tải hữu ích'})
        self.assertEqual(changed.translate_with_identity(source, identity), source)
        self.assertEqual(changed.metrics()['cache_hits'], 0)
        rebuilt = self.translator([], {'payload': 'tải trọng'})
        self.assertEqual(rebuilt.translate_with_identity(source, identity), target)

    def test_unversioned_cache_is_not_reused(self):
        source = 'Repeated warning before operation.'
        translator = self.translator()
        legacy = type(translator.cache)('handoff', {'lang_in': 'auto', 'lang_out': 'vi', 'model': None})
        legacy.set(source, 'Cảnh báo cũ trước khi vận hành.')
        self.assertEqual(translator.translate(source), source)


class BatchCapRegressionTests(unittest.TestCase):
    def test_oversized_source_is_reported_separately_without_truncation(self):
        from pdf2zh.handoff import OversizedSegmentError
        source = {'segment_id': 'large', 'src': 'a' * 12001}
        with self.assertRaises(OversizedSegmentError):
            build_handoff_batches([source])
        oversized = []
        batches = build_handoff_batches([source, {'segment_id': 'ok', 'src': 'Hello'}], oversized=oversized)
        self.assertEqual(oversized[0]['src'], source['src'])
        self.assertEqual([r['segment_id'] for b in batches for r in b['segments']], ['ok'])
        self.assertTrue(all(sum(len(r['src']) for r in b['segments']) <= 12000 for b in batches))

    def test_cli_keeps_oversized_sources_out_of_provider_batches(self):
        from scripts.prepare_handoff import main
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            sources = root / 'sources.jsonl'
            original = {'segment_id': 'large', 'src': 'a' * 12001}
            sources.write_text(json.dumps(original) + '\n' + json.dumps({'segment_id': 'ok', 'src': 'Hello'}), encoding='utf-8')
            batches = root / 'batches.jsonl'
            self.assertEqual(main([str(sources), '--output-batches', str(batches)]), 0)
            self.assertEqual(json.loads(batches.read_text())['segments'][0]['segment_id'], 'ok')
            oversized = json.loads((root / 'batches.oversized.jsonl').read_text())
            self.assertEqual(oversized['src'], original['src'])
            self.assertEqual(oversized['segment_id'], 'large')
            with self.assertRaises(SystemExit):
                main([str(sources), '--output-batches', str(batches), '--oversized', str(sources)])
            self.assertEqual(json.loads(sources.read_text().splitlines()[0]), original)


class TerminologyPlumbingTests(unittest.TestCase):
    def test_fingerprint_is_order_independent_and_changes_with_rules(self):
        from pdf2zh import terminology
        first = terminology.terminology_fingerprint({'x': 'a', 'y': 'b'})
        self.assertEqual(first, terminology.terminology_fingerprint({'y': 'b', 'x': 'a'}))
        with mock.patch.object(terminology, 'TRANSLATION_RULES_VERSION', 'changed-test-rules'):
            self.assertNotEqual(first, terminology.terminology_fingerprint({'x': 'a', 'y': 'b'}))

    def test_cli_passes_confirmed_terms_to_engine_without_disabling_cache(self):
        from scripts import translate_pdf
        from pdf2zh.high_level import TranslationReport
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source, terms = root / 'test.pdf', root / 'terms.json'
            import pymupdf
            document = pymupdf.open()
            document.new_page(width=300, height=400)
            document.save(source)
            document.close()
            terms.write_text(json.dumps({'payload': 'tải trọng'}, ensure_ascii=False), encoding='utf-8')
            with (mock.patch.object(translate_pdf, '_require_core'),
                  mock.patch.object(translate_pdf, '_detect_confidentiality_markers', return_value=()),
                  mock.patch.object(translate_pdf, '_run_engine', return_value=TranslationReport(translatable_segments=1)) as run):
                translate_pdf.translate_pdf(source, None, engine='handoff', emit_segments=root / 'out.jsonl', terminology=terms)
            self.assertFalse(run.call_args.args[6])
            self.assertEqual(run.call_args.args[8]['terminology'], {'payload': 'tải trọng'})

    def test_emitted_segments_cannot_overwrite_terminology(self):
        from scripts import translate_pdf
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source, terms = root / 'test.pdf', root / 'terms.json'
            import pymupdf
            document = pymupdf.open()
            document.new_page(width=300, height=400)
            document.save(source)
            document.close()
            terms.write_text('{}', encoding='utf-8')
            with mock.patch.object(translate_pdf, '_require_core'), self.assertRaises(translate_pdf.TranslationError):
                translate_pdf.translate_pdf(source, None, engine='handoff', emit_segments=terms, terminology=terms)
            self.assertEqual(terms.read_text(), '{}')


if __name__ == '__main__':
    unittest.main()
