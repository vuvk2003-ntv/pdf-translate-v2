from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock

from pdf2zh.cache import clean_test_db, init_test_db
from pdf2zh.integrity import TranslationIntegrityError
from pdf2zh.routing import (
    RoutingMetadata,
    SelectedRoute,
    route_logical_unit,
    routing_features,
)
from pdf2zh.rules import TableTextCluster, group_translatable_line_clusters
from pdf2zh.translator import AutoTranslator
from scripts.translate_pdf import _parser, _validate_arguments


def _metadata(**kwargs: object) -> RoutingMetadata:
    values = {
        "logical_unit_id": "unit-1",
        "occurrence_id": "occ-1",
        "source_fragment_ids": ("fragment-1",),
    }
    values.update(kwargs)
    return RoutingMetadata(**values)


def _decision(text: str, **kwargs: object):
    return route_logical_unit(text, _metadata(**kwargs))


def _context(text: str, metadata: RoutingMetadata | None = None) -> dict[str, object]:
    metadata = metadata or _metadata()
    return {
        "routing_metadata": metadata,
        "routing_decision": route_logical_unit(text, metadata),
        "handoff_context": None,
    }


class RouterFixtures(unittest.TestCase):
    def test_01_simple_english_routes_google(self) -> None:
        self.assertEqual(
            _decision("Set the communication parameters.").selected_route,
            SelectedRoute.GOOGLE,
        )

    def test_02_simple_korean_routes_google(self) -> None:
        self.assertEqual(_decision("프로그램 종료").selected_route, SelectedRoute.GOOGLE)

    def test_03_simple_chinese_routes_google(self) -> None:
        self.assertEqual(_decision("设置通信参数。 ").selected_route, SelectedRoute.GOOGLE)

    def test_04_approved_technical_unit_routes_preserve(self) -> None:
        self.assertEqual(
            _decision("D100", preserve_eligible=True).selected_route,
            SelectedRoute.PRESERVE,
        )

    def test_05_mixed_korean_and_ui_context_routes_handoff(self) -> None:
        result = _decision("Servo Motor 위치 확인 후 Start 버튼을 누른다.")
        self.assertEqual(result.selected_route, SelectedRoute.HANDOFF)
        self.assertIn("contains_mixed_script", result.routing_flags)

    def test_06_structural_reconstruction_routes_handoff(self) -> None:
        result = _decision(
            "Connect the controller after checking the terminal assignment.",
            was_fragment_reconstructed=True,
            reconstruction_complexity="structural_merge",
        )
        self.assertEqual(result.selected_route, SelectedRoute.HANDOFF)

    def test_07_trivial_wrap_remains_google(self) -> None:
        result = _decision(
            "Refer to the following manual.",
            was_fragment_reconstructed=True,
            reconstruction_complexity="trivial_wrap",
        )
        self.assertEqual(result.selected_route, SelectedRoute.GOOGLE)

    def test_08_multiple_object_value_associations_route_handoff(self) -> None:
        text = "Set axis X speed to 100 mm/s and axis Y speed to 50 mm/s."
        self.assertEqual(_decision(text).selected_route, SelectedRoute.HANDOFF)
        self.assertEqual(routing_features(text, _metadata())["object_value_pair_count"], 2)

    def test_09_one_simple_condition_does_not_overroute(self) -> None:
        self.assertEqual(
            _decision("If the indicator is red, restart the controller.").selected_route,
            SelectedRoute.GOOGLE,
        )

    def test_10_one_ui_literal_does_not_overroute(self) -> None:
        self.assertEqual(
            _decision("Press the Start button.").selected_route,
            SelectedRoute.GOOGLE,
        )

    def test_11_callout_requires_combined_context_risk(self) -> None:
        self.assertEqual(
            _decision("Motor position", is_callout=True).selected_route,
            SelectedRoute.GOOGLE,
        )

    def test_12_confidentiality_marker_does_not_change_route(self) -> None:
        ordinary = _decision("Set the communication parameters.").selected_route
        marked = _decision("Set the communication parameters. CONFIDENTIAL").selected_route
        self.assertEqual(marked, ordinary)

    def test_13_explicit_google_bypasses_auto_routing(self) -> None:
        result = route_logical_unit(
            "Servo Motor 위치 확인 후 Start 버튼을 누른다.",
            _metadata(reconstruction_complexity="structural_merge"),
            requested_engine="google",
        )
        self.assertEqual(result.selected_route, SelectedRoute.GOOGLE)

    def test_14_explicit_handoff_bypasses_google_routing(self) -> None:
        result = route_logical_unit(
            "Set the communication parameters.",
            _metadata(),
            requested_engine="handoff",
        )
        self.assertEqual(result.selected_route, SelectedRoute.HANDOFF)

    def test_15_auto_cli_is_available(self) -> None:
        args = _parser().parse_args(["input.pdf", "--output-dir", "out", "--engine", "auto"])
        _validate_arguments(args)
        self.assertEqual(args.engine, "auto")

    def test_16_google_cli_rejects_handoff_queue_options(self) -> None:
        args = _parser().parse_args(
            ["input.pdf", "--output-dir", "out", "--engine", "google", "--emit-segments", "q.jsonl"]
        )
        with self.assertRaisesRegex(Exception, "handoff or auto"):
            _validate_arguments(args)

    def test_17_missing_reconstruction_metadata_is_not_recreated(self) -> None:
        result = _decision("Set the communication parameters.")
        self.assertEqual(result.selected_route, SelectedRoute.GOOGLE)
        self.assertFalse(result.routing_flags["was_fragment_reconstructed"])

    def test_18_patch_a_exposes_read_only_fragment_provenance(self) -> None:
        groups = group_translatable_line_clusters(
            [
                TableTextCluster(
                    (0.0, 0.0, 180.0, 10.0),
                    "Set the communication",
                    (),
                    ((0.0, 0.0, 180.0, 10.0),),
                    parent_id="paragraph-1",
                    fragment_id="first",
                ),
                TableTextCluster(
                    (0.0, 12.0, 100.0, 22.0),
                    "parameters.",
                    (),
                    ((0.0, 12.0, 100.0, 22.0),),
                    parent_id="paragraph-1",
                    fragment_id="second",
                ),
            ],
            page=0,
            region_id="paragraph-1",
        )
        self.assertEqual(len(groups), 1)
        self.assertEqual(len(groups[0].source_fragment_ids), 2)
        self.assertTrue(groups[0].logical_unit_id)
        self.assertTrue(groups[0].merge_reasons)


class AutoExecutionFixtures(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.test_db = init_test_db()

    @classmethod
    def tearDownClass(cls) -> None:
        clean_test_db(cls.test_db)

    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.queue = self.root / "pending.jsonl"

    def tearDown(self) -> None:
        self.temp.cleanup()

    def auto(self, *, queue: bool = True, table: Path | None = None) -> AutoTranslator:
        envs: dict[str, object] = {}
        if queue:
            envs["segments_out"] = str(self.queue)
        if table is not None:
            envs["segments_in"] = str(table)
        return AutoTranslator("auto", "vi", ignore_cache=False, envs=envs)

    def patch_google(self, auto: AutoTranslator, result: str | BaseException) -> Mock:
        if isinstance(result, BaseException):
            mock = Mock(side_effect=result)
        else:
            mock = Mock(return_value=result)
        auto.google.translate = mock
        return mock

    def test_18_google_pass_is_accepted_without_handoff(self) -> None:
        auto = self.auto()
        google = self.patch_google(auto, "Thiết lập các tham số truyền thông.")
        text = "Set the communication parameters."
        self.assertEqual(
            auto.translate_with_identity(text, "unit-1", context=_context(text)),
            "Thiết lập các tham số truyền thông.",
        )
        google.assert_called_once_with(text)
        self.assertTrue(auto.has_translation_for(text))
        self.assertEqual(auto.metrics()["handoff_misses"], 0)

    def test_19_google_integrity_failure_enqueues_same_unit(self) -> None:
        auto = self.auto()
        self.patch_google(auto, TranslationIntegrityError(("hangul_leak_failure",)))
        text = "프로그램 종료 안내"
        metadata = _metadata(source_fragment_ids=("f1", "f2"))
        result = auto.translate_with_identity(text, "unit-1", context=_context(text, metadata))
        self.assertEqual(result, text)
        record = json.loads(self.queue.read_text(encoding="utf-8").strip())
        self.assertEqual(record["segment_id"], "unit-1")
        self.assertEqual(record["logical_unit_id"], "unit-1")
        self.assertEqual(record["occurrence_id"], "occ-1")
        self.assertEqual(record["source_fragment_ids"], ["f1", "f2"])
        self.assertEqual(auto.unresolved_reason_for_identity("unit-1"), "PENDING_HANDOFF")

    def test_20_google_corrupt_literal_escalates_only_affected_unit(self) -> None:
        auto = self.auto()
        self.patch_google(auto, TranslationIntegrityError(("technical_invariant_failure",)))
        bad = "Open C:\\PLC\\project.gx3 before operation."
        auto.translate_with_identity(bad, "unit-bad", context=_context(bad, _metadata(logical_unit_id="unit-bad")))
        self.patch_google(auto, "Khởi động bộ điều khiển.")
        good = "Start the controller."
        auto.translate_with_identity(good, "unit-good", context=_context(good, _metadata(logical_unit_id="unit-good")))
        self.assertEqual(auto.metrics()["google_to_handoff_escalations"], 1)
        self.assertEqual(auto.metrics()["google_routed_units"], 2)

    def test_21_invalid_google_cache_escalates_without_provider_call(self) -> None:
        auto = self.auto()
        text = "Set the communication parameters for this controller."
        auto.google.cache.set(text, text)
        provider = self.patch_google(auto, "Không được gọi")
        auto.translate_with_identity(text, "unit-1", context=_context(text))
        provider.assert_not_called()
        self.assertEqual(auto.metrics()["google_validation_failures"], 1)

    def test_22_valid_google_cache_hit_avoids_provider(self) -> None:
        auto = self.auto()
        text = "Set the communication parameters for this controller."
        target = "Thiết lập các tham số truyền thông cho bộ điều khiển này."
        auto.google.cache.set(text, target)
        provider = self.patch_google(auto, "Không được gọi")
        self.assertEqual(auto.translate_with_identity(text, "unit-1", context=_context(text)), target)
        provider.assert_not_called()
        self.assertEqual(auto.metrics()["google_cache_hits"], 1)

    def test_23_high_risk_route_ignores_valid_google_cache(self) -> None:
        auto = self.auto()
        text = "Servo Motor 위치 확인 후 Start 버튼을 누른다."
        auto.google.cache.set(text, "Kiểm tra vị trí Servo Motor rồi nhấn Start.")
        auto.translate_with_identity(text, "unit-1", context=_context(text))
        self.assertEqual(auto.metrics()["google_cache_hits"], 0)
        self.assertEqual(auto.metrics()["handoff_direct_units"], 1)

    def test_24_valid_handoff_table_preempts_google_on_google_route(self) -> None:
        text = "Refer to the following installation manual for more information."
        target = "Tham khảo tài liệu hướng dẫn lắp đặt sau để biết thêm thông tin."
        table = self.root / "translated.jsonl"
        table.write_text(
            json.dumps({"segment_id": "unit-1", "src": text, "dst": target}, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        auto = self.auto(table=table)
        provider = self.patch_google(auto, "Không được gọi")
        self.assertEqual(auto.translate_with_identity(text, "unit-1", context=_context(text)), target)
        provider.assert_not_called()
        self.assertEqual(auto.routing_traces[0]["actual_provider"], "handoff")

    def test_25_google_timeout_enqueues_and_is_not_translated(self) -> None:
        auto = self.auto()
        self.patch_google(auto, TimeoutError("timeout"))
        text = "Set the communication parameters."
        self.assertEqual(auto.translate_with_identity(text, "unit-1", context=_context(text)), text)
        self.assertFalse(auto.has_translation_for_identity(text, "unit-1"))
        metrics = auto.metrics()
        self.assertEqual(metrics["google_provider_unavailable_units"], 1)
        self.assertEqual(metrics["pending_handoff_queue_after_run"], 1)

    def test_26_handoff_unavailable_is_explicit(self) -> None:
        auto = self.auto(queue=False)
        text = "Servo Motor 위치 확인 후 Start 버튼을 누른다."
        self.assertEqual(auto.translate_with_identity(text, "unit-1", context=_context(text)), text)
        self.assertEqual(auto.unresolved_reason_for_identity("unit-1"), "PROVIDER_UNAVAILABLE")
        self.assertEqual(auto.metrics()["handoff_provider_unavailable_units"], 1)

    def test_27_invalid_handoff_result_is_counted_then_unresolved(self) -> None:
        auto = self.auto()
        text = "Servo Motor 위치 확인 후 Start 버튼을 누른다."
        auto.handoff.translate_with_identity = Mock(
            side_effect=TranslationIntegrityError(("hangul_leak_failure",))
        )
        with self.assertRaises(TranslationIntegrityError):
            auto.translate_with_identity(text, "unit-1", context=_context(text))
        auto.record_unresolved_identity("unit-1", "TranslationIntegrityError")
        self.assertEqual(auto.metrics()["handoff_validation_failures"], 1)
        self.assertEqual(auto.metrics()["handoff_unresolved_units"], 1)

    def test_28_metrics_and_trace_keep_requested_route_and_provider(self) -> None:
        auto = self.auto()
        self.patch_google(auto, "Thiết lập các tham số truyền thông.")
        text = "Set the communication parameters."
        auto.translate_with_identity(text, "unit-1", context=_context(text))
        trace = auto.routing_traces[0]
        self.assertEqual(trace["requested_engine"], "auto")
        self.assertEqual(trace["selected_route"], "google")
        self.assertEqual(trace["actual_provider"], "google")
        self.assertIn("routing_reason", trace)
        self.assertIn("routing_flags", trace)
        self.assertGreaterEqual(auto.metrics()["routing_seconds"], 0.0)

    def test_29_direct_handoff_table_result_passes_without_google(self) -> None:
        text = "Servo Motor 위치 확인 후 Start 버튼을 누른다."
        target = "Kiểm tra vị trí Servo Motor rồi nhấn nút Start."
        table = self.root / "direct.jsonl"
        table.write_text(
            json.dumps(
                {"segment_id": "unit-1", "src": text, "dst": target},
                ensure_ascii=False,
            )
            + "\n",
            encoding="utf-8",
        )
        auto = self.auto(table=table)
        google = self.patch_google(auto, "Không được gọi")
        self.assertEqual(
            auto.translate_with_identity(text, "unit-1", context=_context(text)),
            target,
        )
        google.assert_not_called()
        self.assertEqual(auto.metrics()["handoff_direct_units"], 1)

    def test_30_preserve_route_records_no_provider_work(self) -> None:
        auto = self.auto()
        metadata = _metadata(preserve_eligible=True)
        decision = route_logical_unit("D100", metadata)
        auto.record_preserve_route("unit-1", decision, metadata)
        metrics = auto.metrics()
        self.assertEqual(metrics["preserve_routed_units"], 1)
        self.assertEqual(metrics["google_provider_requests"], 0)
        self.assertEqual(metrics["handoff_misses"], 0)

    def test_31_google_cache_precedes_existing_handoff_result(self) -> None:
        text = "Review the controller configuration before starting operation."
        google_target = "Kiểm tra cấu hình bộ điều khiển trước khi bắt đầu vận hành."
        handoff_target = "Xem lại cấu hình bộ điều khiển trước khi vận hành."
        table = self.root / "both.jsonl"
        table.write_text(
            json.dumps(
                {"segment_id": "unit-1", "src": text, "dst": handoff_target},
                ensure_ascii=False,
            )
            + "\n",
            encoding="utf-8",
        )
        auto = self.auto(table=table)
        auto.google.cache.set(text, google_target)
        provider = self.patch_google(auto, "Không được gọi")
        self.assertEqual(
            auto.translate_with_identity(text, "unit-1", context=_context(text)),
            google_target,
        )
        provider.assert_not_called()
        self.assertEqual(auto.metrics()["google_cache_hits"], 1)
        self.assertEqual(auto.metrics()["handoff_table_hits"], 0)

    def test_32_unchanged_required_prose_escalates_to_handoff(self) -> None:
        text = "Set the communication parameters."
        auto = self.auto()
        self.patch_google(
            auto, TranslationIntegrityError(("unchanged_prose_failure",))
        )
        self.assertEqual(
            auto.translate_with_identity(text, "unit-unchanged", context=_context(text)),
            text,
        )
        self.assertEqual(
            auto.unresolved_reason_for_identity("unit-unchanged"), "PENDING_HANDOFF"
        )
        self.assertEqual(auto.metrics()["google_to_handoff_escalations"], 1)

    def test_33_obvious_corruption_escalates_to_handoff(self) -> None:
        text = "Refer to the following manual."
        auto = self.auto()
        self.patch_google(
            auto, TranslationIntegrityError(("repeated_token_corruption_failure",))
        )
        self.assertEqual(
            auto.translate_with_identity(text, "unit-corrupt", context=_context(text)),
            text,
        )
        self.assertEqual(
            auto.unresolved_reason_for_identity("unit-corrupt"), "PENDING_HANDOFF"
        )
        self.assertEqual(auto.metrics()["google_to_handoff_escalations"], 1)


if __name__ == "__main__":
    unittest.main()
