"""Exact structural-boundary fixtures for Patch A reconstruction."""

from __future__ import annotations

import unittest

from pdf2zh.logical_units import (
    LogicalFragment,
    ReconstructionMetrics,
    build_logical_units,
    reconstruction_health_review,
)


def fragment(
    identifier: str,
    text: str,
    bbox: tuple[float, float, float, float],
    order: int,
    **metadata: object,
) -> LogicalFragment:
    defaults: dict[str, object] = {
        "page": 0,
        "paragraph_id": "paragraph-1",
        "region_id": "body-1",
    }
    defaults.update(metadata)
    return LogicalFragment(identifier, text, bbox=bbox, reading_order=order, **defaults)


class LogicalUnitBoundaryFixtures(unittest.TestCase):
    def assert_conserved(self, *fragments: LogicalFragment) -> None:
        metrics = build_logical_units(fragments).metrics
        self.assertEqual(metrics.candidate_fragments, len(fragments))
        self.assertEqual(metrics.assigned_candidate_fragments, len(fragments))
        self.assertEqual(metrics.unassigned_candidate_fragments, 0)
        self.assertEqual(metrics.duplicate_fragment_assignments, 0)

    def test_01_inline_emphasis_split_merges_with_style_markers(self):
        fragments = (
            fragment("f1", "Do ", (10, 10, 28, 20), 0, style=0),
            fragment("f2", "NOT", (28, 10, 48, 20), 1, style=1),
            fragment(
                "f3",
                " turn off the power while writing.",
                (48, 10, 220, 20),
                2,
                style=0,
            ),
        )
        result = build_logical_units(fragments)
        self.assertEqual(len(result.units), 1)
        self.assertEqual(
            result.units[0].text,
            "Do <s1>NOT</s1> turn off the power while writing.",
        )
        self.assertIn("inline_emphasis", result.units[0].merge_reasons)

    def test_02_free_paragraph_wrapped_lines_merge(self):
        fragments = (
            fragment("f1", "Normal prose wrapped across", (10, 10, 180, 20), 0),
            fragment("f2", "multiple physical lines", (10, 22, 155, 32), 1),
            fragment("f3", "remains one paragraph.", (10, 34, 145, 44), 2),
        )
        result = build_logical_units(fragments)
        self.assertEqual(len(result.units), 1)
        self.assertEqual(len(result.units[0].fragments), 3)

    def test_03_same_table_cell_multiline_description_merges(self):
        fragments = (
            fragment(
                "f1",
                "Specifications of the hardware (CPU modules,",
                (10, 10, 240, 20),
                0,
                paragraph_id=None,
                cell_id="description-1",
            ),
            fragment(
                "f2",
                "power supply modules, base units, extension cables,",
                (10, 22, 255, 32),
                1,
                paragraph_id=None,
                cell_id="description-1",
            ),
            fragment(
                "f3",
                "memory cards and batteries)",
                (10, 34, 160, 44),
                2,
                paragraph_id=None,
                cell_id="description-1",
            ),
        )
        result = build_logical_units(fragments)
        self.assertEqual(len(result.units), 1)
        self.assertEqual(
            tuple(item.fragment_id for item in result.units[0].fragments),
            ("f1", "f2", "f3"),
        )
        self.assertEqual(
            result.units[0].provenance.merge_reason, "same_cell_continuation"
        )

    def test_04_adjacent_different_table_cells_never_merge(self):
        fragments = (
            fragment("f1", "QCPU User's Manual", (10, 10, 105, 20), 0, cell_id="A"),
            fragment(
                "f2",
                "Specifications of the hardware",
                (106, 10, 250, 20),
                1,
                cell_id="B",
            ),
        )
        result = build_logical_units(fragments)
        self.assertEqual(len(result.units), 2)
        self.assertEqual(result.metrics.rejected_cross_cell, 1)

    def test_05_cross_cell_wrap_boundary_never_merges(self):
        fragments = (
            fragment(
                "f1", "Hardware Design, Maintenance", (10, 10, 180, 20), 0, cell_id="A"
            ),
            fragment("f2", "Basic manual", (10, 22, 90, 32), 1, cell_id="B"),
        )
        self.assertEqual(len(build_logical_units(fragments).units), 2)

    def test_06_same_cell_inline_style_merges_and_preserves_marker(self):
        fragments = (
            fragment("f1", "Do ", (10, 10, 28, 20), 0, cell_id="A", style=0),
            fragment("f2", "NOT", (28, 10, 48, 20), 1, cell_id="A", style=1),
            fragment(
                "f3",
                " disconnect the module while power is on.",
                (48, 10, 260, 20),
                2,
                cell_id="A",
                style=0,
            ),
        )
        result = build_logical_units(fragments)
        self.assertEqual(len(result.units), 1)
        self.assertEqual(
            result.units[0].text,
            "Do <s1>NOT</s1> disconnect the module while power is on.",
        )

    def test_07_two_columns_never_merge(self):
        fragments = (
            fragment(
                "f1", "Left column continues", (10, 10, 120, 20), 0, column_id="left"
            ),
            fragment(
                "f2", "Right column starts", (130, 10, 240, 20), 1, column_id="right"
            ),
        )
        result = build_logical_units(fragments)
        self.assertEqual(len(result.units), 2)
        self.assertEqual(result.metrics.rejected_cross_column, 1)

    def test_08_heading_and_body_never_merge(self):
        fragments = (
            fragment(
                "f1",
                "Safety precautions",
                (10, 10, 130, 20),
                0,
                structural_role="heading",
            ),
            fragment("f2", "Disconnect the power before service", (10, 22, 210, 32), 1),
        )
        result = build_logical_units(fragments)
        self.assertEqual(len(result.units), 2)
        self.assertEqual(result.metrics.rejected_structural_role, 1)

    def test_09_two_bullets_never_merge(self):
        fragments = (
            fragment("f1", "• Install the module", (10, 10, 130, 20), 0),
            fragment("f2", "• Connect the cable", (10, 22, 130, 32), 1),
        )
        result = build_logical_units(fragments)
        self.assertEqual(len(result.units), 2)
        self.assertEqual(result.metrics.rejected_linguistic_boundary, 1)

    def test_10_two_callouts_never_merge(self):
        fragments = (
            fragment(
                "f1",
                "Warning: high voltage",
                (10, 10, 140, 20),
                0,
                callout_id="warning",
            ),
            fragment(
                "f2", "Note: keep this cover", (10, 22, 140, 32), 1, callout_id="note"
            ),
        )
        self.assertEqual(len(build_logical_units(fragments).units), 2)

    def test_11_page_boundary_never_merges_and_keeps_both_halves(self):
        fragments = (
            fragment(
                "f1", "ensure that the supplied voltage", (10, 700, 190, 710), 0, page=0
            ),
            fragment("f2", "does not exceed 24 V DC.", (10, 10, 160, 20), 1, page=1),
        )
        result = build_logical_units(fragments)
        self.assertEqual(len(result.units), 2)
        self.assertEqual(result.metrics.rejected_page_boundary, 1)
        self.assertEqual(
            [unit.text for unit in result.units], [item.text for item in fragments]
        )
        self.assert_conserved(*fragments)


class ReconstructionInstrumentationTests(unittest.TestCase):
    def test_deliberately_under_merging_metrics_trigger_manual_review(self):
        metrics = ReconstructionMetrics(
            raw_text_spans=200,
            logical_units=180,
            singleton_unit_count=170,
            continuation_candidate_count=120,
            accepted_merge_count=20,
            rejected_merge_count=100,
        )
        self.assertEqual(
            reconstruction_health_review(metrics), "MANUAL_REVIEW_REQUIRED"
        )

    def test_candidate_identifier_must_be_unique(self):
        fragments = (
            fragment("duplicate", "first fragment", (10, 10, 90, 20), 0),
            fragment("duplicate", "second fragment", (10, 22, 100, 32), 1),
        )
        with self.assertRaisesRegex(RuntimeError, "candidate-fragment conservation"):
            build_logical_units(fragments)

    def test_duplicate_assignment_across_builder_calls_is_detected(self):
        candidate = fragment("stable-fragment", "source text", (10, 10, 90, 20), 0)
        aggregate = ReconstructionMetrics()
        aggregate.absorb(build_logical_units([candidate]).metrics)
        aggregate.absorb(build_logical_units([candidate]).metrics)
        with self.assertRaisesRegex(RuntimeError, "candidate-fragment conservation"):
            aggregate.assert_conservation()


if __name__ == "__main__":
    unittest.main()
