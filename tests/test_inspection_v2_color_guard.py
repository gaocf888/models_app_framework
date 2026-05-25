from __future__ import annotations

from app.inspection_v2.detection_type_color_guard import apply_docx_v2_color_guard

_CHUNK = """
[DOCX_V2_TABLE idx=1 rows=14 cols=4]
r0: c0..c3='右墙B02吹灰器'[重复表题×4]
r1: c0-c1='上'[hmerge×2] | c2-c3='下'[hmerge×2]
r2: c0='5' | c1='6.6' | c2='2' | c3='5.6'
r5: c0='8' | c1='6.1' | c2='12' | c3='4.73'[颜色标注:高亮=red]
r6: c0='9' | c1='5.8' | c2='13' | c3='5.3'[颜色标注:高亮=red]
"""


def test_color_guard_downgrades_upper_row_without_highlight() -> None:
    records = [
        {
            "检测位置": "右墙B02吹灰器",
            "行号": "1",
            "管号": "-8",
            "壁厚": 6.1,
            "检测类型": "缺陷",
            "缺陷类型": "",
            "是否换管": "是",
        },
        {
            "检测位置": "右墙B02吹灰器",
            "行号": "1",
            "管号": "12",
            "壁厚": 4.73,
            "检测类型": "缺陷",
            "缺陷类型": "",
            "是否换管": "是",
        },
    ]
    out = apply_docx_v2_color_guard(records, _CHUNK)
    assert out[0]["检测类型"] == "测厚"
    assert out[0]["是否换管"] == "否"
    assert out[1]["检测类型"] == "缺陷"


def test_color_guard_upgrades_highlighted_lower_thickness() -> None:
    records = [
        {
            "检测位置": "右墙B02吹灰器",
            "行号": "1",
            "管号": "12",
            "壁厚": 4.73,
            "检测类型": "测厚",
            "缺陷类型": "",
            "是否换管": "否",
        },
    ]
    out = apply_docx_v2_color_guard(records, _CHUNK)
    assert out[0]["检测类型"] == "缺陷"
