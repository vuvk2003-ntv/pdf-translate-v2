"""Run explicitly selected D2 regressions without PDFs or live HTTP."""
from __future__ import annotations

import logging
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

SKILL_ROOT = Path(__file__).resolve().parents[1]
if str(SKILL_ROOT) not in sys.path:
    sys.path.insert(0, str(SKILL_ROOT))

SUITES = (
    "tests.test_patch_d2_retry",
    "tests.test_patch_d_performance",
    "tests.test_logical_units",
    "tests.test_patch_b_integrity",
    "tests.test_patch_c_routing",
    "tests.test_handoff_workflow",
    "tests.test_handoff_translator",
    "tests.test_preservation_rules",
    "tests.test_technical_invariants",
    "tests.test_proper_names",
    "tests.test_terminology_boundaries",
    "tests.test_korean_targeted",
    "tests.test_korean_coverage",
    "tests.test_inline_formula_layout",
    "tests.test_targeted_correctness.ClassifierRegressionTests",
    "tests.test_targeted_correctness.RelationRegressionTests",
    "tests.test_targeted_correctness.SemanticRegressionTests",
    "tests.test_targeted_correctness.IdentityAndCacheRegressionTests",
    "tests.test_targeted_correctness.BatchCapRegressionTests",
    "tests.test_targeted_correctness.TerminologyPlumbingTests.test_fingerprint_is_order_independent_and_changes_with_rules",
    "tests.test_layout_qa.LayoutQATests.test_new_overlap_with_retained_text_is_reported",
    "tests.test_layout_qa.LayoutQATests.test_source_overlap_is_not_reported_as_new",
    "tests.test_layout_qa.LayoutQATests.test_cross_cell_spill_and_page_overflow_are_reported",
    "tests.test_layout_qa.LayoutQATests.test_large_empty_cell_with_half_size_text_is_suspicious",
    "tests.test_layout_qa.LayoutQATests.test_same_occurrence_drawn_twice_is_reported_by_text_and_geometry",
    "tests.test_final_targeted_patch.DuplicateOccurrenceTests",
    "tests.test_final_targeted_patch.MetadataProtectionTests.test_only_immutable_values_are_selected_from_mixed_korean_lines",
    "tests.test_final_targeted_patch.MetadataProtectionTests.test_explicit_proper_names_and_official_tokens_are_allowed",
)


def main() -> int:
    logging.disable(logging.ERROR)
    # Imports are inside the guards as well; no discovery of PDF-based suites.
    with (
        patch("pymupdf.open", side_effect=AssertionError("PDF processing forbidden")),
        patch("requests.sessions.Session.request", side_effect=AssertionError("live HTTP forbidden")),
    ):
        suite = unittest.defaultTestLoader.loadTestsFromNames(SUITES)
        result = unittest.TextTestRunner(verbosity=1).run(suite)
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    raise SystemExit(main())
