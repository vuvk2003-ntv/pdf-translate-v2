from __future__ import annotations

import unittest

from pdf2zh.converter import needs_model_translation
from pdf2zh.rules import should_translate_table_cell


class TableLanguageConsistencyTests(unittest.TestCase):
    def test_english_korean_and_chinese_natural_language_is_eligible(self):
        for text in (
            "Pressure",
            "Safety requirements",
            "Set D100 to 250.",
            "Wait to next step",
            "Horizontal stroke 800mm",
            "안전사양",
            "安全要求",
        ):
            with self.subTest(text=text):
                self.assertTrue(needs_model_translation(text))
                self.assertTrue(should_translate_table_cell(text))

    def test_standalone_technical_and_numeric_data_stays_preserved(self):
        for text in (
            "D100",
            "R245",
            "0x2005",
            "IRCB50x",
            "Q03UDVCPU",
            "123",
            "250",
            "250 mm",
            "192.168.1.10",
            "ON",
            "OFF",
        ):
            with self.subTest(text=text):
                self.assertFalse(needs_model_translation(text))
                self.assertFalse(should_translate_table_cell(text))

    def test_existing_standalone_technical_terms_stay_preserved(self):
        for text in ("Servo Motor", "SCARA Robot", "JOG"):
            with self.subTest(text=text):
                self.assertFalse(needs_model_translation(text))
                self.assertFalse(should_translate_table_cell(text))

    def test_mixed_proper_name_cell_translates_around_p1_span(self):
        text = "Inovance Technology controller"
        self.assertTrue(needs_model_translation(text))
        self.assertTrue(should_translate_table_cell(text))

    def test_metadata_and_formula_cells_are_not_promoted_as_prose(self):
        for text in ("Rev. 12.0", "F = m * a"):
            with self.subTest(text=text):
                self.assertFalse(should_translate_table_cell(text))


if __name__ == "__main__":
    unittest.main()
