from __future__ import annotations

import unittest

from pdf2zh.handoff import (
    DuplicateTargetFragmentError,
    validate_no_duplicate_target_fragment,
)


class DuplicateSourceAccountingTests(unittest.TestCase):
    def test_faithful_repeated_source_sentence_is_accepted(self):
        source = "Tighten the bolt now. Tighten the bolt now."
        target = "Siết bu lông ngay. Siết bu lông ngay."
        validate_no_duplicate_target_fragment(source, target)

    def test_invented_full_target_repetition_is_rejected(self):
        with self.assertRaises(DuplicateTargetFragmentError):
            validate_no_duplicate_target_fragment(
                "Tighten the bolt now.",
                "Siết bu lông ngay. Siết bu lông ngay.",
            )

    def test_redundant_trailing_child_fragment_is_rejected(self):
        with self.assertRaises(DuplicateTargetFragmentError):
            validate_no_duplicate_target_fragment(
                "Check the robot position before operation.",
                "Kiểm tra vị trí robot trước khi vận hành. Kiểm tra vị trí robot.",
            )

    def test_source_backed_trailing_child_is_not_called_invented(self):
        validate_no_duplicate_target_fragment(
            "Check the robot position before operation. Check the robot position.",
            "Kiểm tra vị trí robot trước khi vận hành. Kiểm tra vị trí robot.",
        )

    def test_repeated_proper_name_is_not_sentence_duplication(self):
        validate_no_duplicate_target_fragment(
            "Inovance Technology Inovance Technology",
            "Inovance Technology Inovance Technology",
        )

    def test_repeated_technical_token_is_not_sentence_duplication(self):
        validate_no_duplicate_target_fragment("D100 D100", "D100 D100")


if __name__ == "__main__":
    unittest.main()
