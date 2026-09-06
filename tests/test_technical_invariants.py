from __future__ import annotations

import unittest

from pdf2zh.invariants import TechnicalInvariantError, validate_technical_invariants


class TechnicalInvariantTests(unittest.TestCase):
    def assert_rejected(self, source: str, translated: str) -> None:
        with self.assertRaises(TechnicalInvariantError):
            validate_technical_invariants(source, translated)

    def test_changed_plc_value_is_rejected(self):
        self.assert_rejected("D100 = 250", "Đặt D100 = 200")

    def test_changed_engineering_value_is_rejected(self):
        self.assert_rejected("Pressure: 0.5 MPa", "Áp suất: 5 MPa")

    def test_harmless_value_unit_spacing_is_equivalent(self):
        validate_technical_invariants("Length: 2350mm", "Chiều dài: 2350 mm")

    def test_changed_attached_unit_value_is_rejected(self):
        self.assert_rejected("Length: 2350mm", "Chiều dài: 2530 mm")

    def test_changed_alarm_code_is_rejected(self):
        self.assert_rejected("Alarm 0x2005", "Cảnh báo 0x2006")

    def test_missing_placeholder_is_rejected(self):
        self.assert_rejected("Keep <b0></b0>", "Giữ nguyên")

    def test_executable_instruction_tokens_cannot_change(self):
        self.assert_rejected("Run MOV D100 D200", "Chạy BMOV D100 D200")

    def test_changed_standard_url_path_and_version_are_rejected(self):
        source = "IEC 60204-1 Rev.A https://example.com/a C:\\PLC\\setup.ini"
        for translated in (
            "IEC 60204-2 Rev.A https://example.com/a C:\\PLC\\setup.ini",
            "IEC 60204-1 Rev.B https://example.com/a C:\\PLC\\setup.ini",
            "IEC 60204-1 Rev.A https://example.com/b C:\\PLC\\setup.ini",
            "IEC 60204-1 Rev.A https://example.com/a C:\\PLC\\other.ini",
        ):
            with self.subTest(translated=translated):
                self.assert_rejected(source, translated)

    def test_changed_register_model_connector_and_endpoint_are_rejected(self):
        source = "Q03UDVCPU reads R[1] through CN1 at TCP/502 and 192.168.1.10."
        translated = "Q03UDVCPU đọc R[2] qua CN1 tại TCP/502 và 192.168.1.10."
        self.assert_rejected(source, translated)

        self.assert_rejected(
            "Install IR-S10-80Z20S-INT at P[10].",
            "Lắp IR-S10-80Z30S-INT tại P[10].",
        )
        self.assert_rejected(
            "PCW Interlock 설정",
            "Thiết lập Interlock",
        )
        self.assert_rejected(
            "Move to OK#1 Place Position",
            "Di chuyển đến vị trí đặt NG#1",
        )

    def test_balanced_tags_may_move_but_must_keep_identity_and_nesting(self):
        validate_technical_invariants(
            "<s1>Servo ON</s1> then <s2>ready</s2>",
            "<s2>sẵn sàng</s2>, rồi <s1>Servo ON</s1>",
        )
        self.assert_rejected(
            "<s1><s2>text</s2></s1>",
            "<s1><s2>văn bản</s1></s2>",
        )

    def test_valid_korean_technical_translation_passes(self):
        validate_technical_invariants(
            "원점 복귀 후 D100 값을 250으로 설정하고 Servo ON 하십시오.",
            "Sau khi hoàn tất về gốc, hãy đặt D100 thành 250 và bật Servo ON.",
        )

    def test_identifiers_next_to_korean_particles_are_still_protected(self):
        self.assert_rejected(
            "IEC 60204-1에 따라 Servo ON하십시오.",
            "Theo IEC 60204-2, hãy bật Servo ON.",
        )


if __name__ == "__main__":
    unittest.main()
