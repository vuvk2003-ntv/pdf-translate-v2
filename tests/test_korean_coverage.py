from __future__ import annotations

import unittest

from pdf2zh.converter import needs_model_translation
from pdf2zh.rules import anchored_prose_bounds, anchored_translatable_lines, formula_regions, should_translate_table_cell


class KoreanCoverageTests(unittest.TestCase):
    def test_anchor_space_stops_at_grid_strokes_and_neighbor_midpoints(self):
        parent=(0,0,200,200)
        lines=[(120,40,140,50),(120,60,140,70),(40,40,90,50)]
        strokes=[(100,0,100,200),(180,0,180,200),(100,20,180,20)]
        upper=anchored_prose_bounds(lines[0],parent,lines,strokes)
        lower=anchored_prose_bounds(lines[1],parent,lines,strokes)
        self.assertEqual(upper,(105.,20.5,179.,55.))
        self.assertEqual(lower[1],55.)
        self.assertGreaterEqual(lower[0],101.)

    def test_graphic_prose_keeps_its_own_anchor(self):
        def line(text, bbox, direction=(1.,0.)):
            return {"dir":direction,"bbox":bbox,"spans":[{"text":text}]}
        blocks=[{"lines":[
            line("측면 위치",(10,20,60,32)),
            line("性能確認",(100,20,150,32)),
            line("Maker간",(100,40,150,52)),
            line("Servo Motor",(10,70,90,82)),
            line("90mm",(100,70,140,82)),
            line("검사 문서",(10,100,110,200),(.7,-.7)),
        ]}]
        anchors=anchored_translatable_lines(blocks)
        self.assertEqual([a.text for a in anchors],["측면 위치","性能確認","Maker간"])
        self.assertEqual([a.bbox for a in anchors],[(10.,20.,60.,32.),(100.,20.,150.,32.),(100.,40.,150.,52.)])

    def test_korean_prose_with_slashes_and_plus_is_not_a_formula(self):
        for text in (
            "정품 S/W 사용 -장비 특성상 다른 O/S 사용 가능함. (개별협의)",
            "기준) 공장 + 장비군 + 호기",
            "성능, 품질 연관 有 / 무급유 Type 有",
            "Maker간 성능 유의차 無 + 필요 時",
        ):
            with self.subTest(text=text):
                self.assertEqual(formula_regions([(10,20,100,40,text)],[]),[])

    def test_required_korean_labels_need_translation(self):
        for text in (
            "정품 S/W 사용", "장비 특성상 다른 O/S 사용 가능함.",
            "고정밀", "고하중", "안전사양", "공압 부품",
            "기준) 공장 + 장비군 + 호기", "후면", "전면", "지름90mm",
            "성능, 품질 연관 有", "무급유 Type 有", "Maker간 성능 유의차 無",
        ):
            with self.subTest(text=text):
                self.assertTrue(needs_model_translation(text))
                self.assertTrue(should_translate_table_cell(text))

    def test_existing_english_decisions_stay_unchanged(self):
        for text in ("Servo Motor", "Linear Motor", "PLC", "90mm", "300KN", "D100"):
            self.assertFalse(needs_model_translation(text),text)
        for text in ("Horizontal stroke 800mm", "Wait to next step", "Set D100 to 250."):
            self.assertTrue(needs_model_translation(text),text)

    def test_real_formulas_stay_protected(self):
        for text in ("F = m * a", "x = sin(y) + 2"):
            self.assertEqual(formula_regions([(10,20,100,40,text)],[]),[(10.,20.,100.,40.)])


if __name__ == "__main__":
    unittest.main()
