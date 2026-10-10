from __future__ import annotations

from app.inspection_v2.omit_prefix_fill_guard import (
    apply_docx_v2_omit_prefix_fill_guard,
    chunk_has_omit_prefix_form,
    compose_heated_surface,
)

D_LAYER_CHUNK = """
水冷壁D层吹灰器测厚以吹灰器为中心(上)为向上数(下)为向下数规格Φ38×8mm材质12Cr1MoV
[DOCX_V2_TABLE idx=1 rows=16 cols=8]
r0: c0='管子 编号' | c1='剩余壁厚（mm）' | c2='管子 编号' | c3='剩余壁厚（mm）' | c4='管子 编号' | c5='剩余壁厚（mm）' | c6='管子 编号' | c7='剩余壁厚（mm）'
r1: c0='D9下2' | c1='7.7' | c2='D12上2' | c3='7.4' | c4='4' | c5='7.6' | c6='4' | c7='7.5'
r2: c0='3' | c1='7.6' | c2='3' | c3='7.6' | c4='5' | c5='7.6' | c6='5' | c7='7.6'
r3: c0='4' | c1='7.6' | c2='D12下2' | c3='7.5' | c4='6' | c5='7.7' | c6='6' | c7='7.7'
r4: c0='5' | c1='7.7' | c2='3' | c3='7.6' | c4='D15下2' | c5='7.5' | c6='D18下2' | c7='7.4'
r5: c0='6' | c1='7.5' | c2='4' | c3='7.6' | c4='3' | c5='7.6' | c6='3' | c7='7.6'
r6: c0='D10下2' | c1='7.6' | c2='5' | c3='7.3' | c4='4' | c5='7.6' | c6='4' | c7='7.5'
r7: c0='3' | c1='7.5' | c2='6' | c3='7.0' | c4='5' | c5='7.5' | c6='5' | c7='7.7'
r8: c0='4' | c1='7.6' | c2='7' | c3='7.5' | c4='6' | c5='7.6' | c6='6' | c7='7.8'
r9: c0='5' | c1='7.6' | c2='D13下2' | c3='7.5' | c4='D16下2' | c5='7.6' | c6='D19下2' | c7='7.5'
r10: c0='6' | c1='7.5' | c2='3' | c3='7.5' | c4='3' | c5='7.5' | c6='3' | c7='7.6'
r11: c0='D11下2' | c1='7.6' | c2='4' | c3='7.2' | c4='4' | c5='7.7' | c6='4' | c7='7.6'
r12: c0='3' | c1='7.6' | c2='5' | c3='7.7' | c4='5' | c5='7.6' | c6='5' | c7='7.6'
r13: c0='4' | c1='7.7' | c2='6' | c3='7.6' | c4='6' | c5='7.7' | c6='6' | c7='7.5'
r14: c0='5' | c1='7.7' | c2='D14下2' | c3='7.3' | c4='D17下2' | c5='7.6' | c6='' | c7=''
r15: c0='6' | c1='7.6' | c2='3' | c3='7.5' | c4='3' | c5='7.7' | c6='' | c7=''
""".strip()


def test_chunk_has_omit_prefix_form() -> None:
    assert chunk_has_omit_prefix_form(D_LAYER_CHUNK) is True
    plain = "[DOCX_V2_TABLE idx=1 rows=2 cols=2]\nr0: c0='1' | c1='6.5'\nr1: c0='2' | c1='6.6'"
    assert chunk_has_omit_prefix_form(plain) is False


def test_compose_heated_surface_d_layer() -> None:
    prelude = "水冷壁D层吹灰器测厚以吹灰器为中心(上)为向上数(下)为向下数规格Φ38×8mm材质12Cr1MoV"
    assert compose_heated_surface(prelude, "D14下") == "水冷壁D14吹灰器下"
    assert compose_heated_surface(prelude, "D12上") == "水冷壁D12吹灰器上"


def test_fill_group_top_pure_digits_when_llm_skipped() -> None:
    """组3顶 4/5/6、组4顶 4/5/6 被 LLM 漏抽时由守卫补上。"""
    # 仅保留部分完整形态点，故意不含组顶纯数字
    records = [
        {"受热面": "水冷壁D9吹灰器下", "行号": "1", "管号": "2", "壁厚": 7.7, "检测类型": "测厚"},
        {"受热面": "水冷壁D14吹灰器下", "行号": "1", "管号": "2", "壁厚": 7.3, "检测类型": "测厚"},
        {"受热面": "水冷壁D15吹灰器下", "行号": "1", "管号": "2", "壁厚": 7.5, "检测类型": "测厚"},
        {"受热面": "水冷壁D17吹灰器下", "行号": "1", "管号": "2", "壁厚": 7.6, "检测类型": "测厚"},
    ]
    out = apply_docx_v2_omit_prefix_fill_guard(records, D_LAYER_CHUNK)
    tops_d14 = [
        r
        for r in out
        if "D14" in str(r.get("受热面") or "")
        and str(r.get("管号")) in {"4", "5", "6"}
    ]
    assert {str(r["管号"]) for r in tops_d14} >= {"4", "5", "6"}
    by_tube = {str(r["管号"]): r for r in tops_d14}
    assert abs(float(by_tube["4"]["壁厚"]) - 7.6) < 0.01
    assert abs(float(by_tube["5"]["壁厚"]) - 7.6) < 0.01
    assert abs(float(by_tube["6"]["壁厚"]) - 7.7) < 0.01
    assert any(
        str(w).startswith("omit_prefix_fill_guard:filled")
        for r in tops_d14
        for w in (r.get("warnings") or [])
    )

    tops_d17 = [
        r
        for r in out
        if "D17" in str(r.get("受热面") or "")
        and str(r.get("管号")) in {"4", "5", "6"}
    ]
    assert {str(r["管号"]) for r in tops_d17} >= {"4", "5", "6"}


def test_fix_wrong_prefix_on_group_top() -> None:
    """组顶被错挂到 D15 时纠正为 D14。"""
    records = [
        {"受热面": "水冷壁D15吹灰器下", "行号": "1", "管号": "4", "壁厚": 7.6, "检测类型": "测厚"},
        {"受热面": "水冷壁D15吹灰器下", "行号": "1", "管号": "5", "壁厚": 7.6, "检测类型": "测厚"},
        {"受热面": "水冷壁D15吹灰器下", "行号": "1", "管号": "6", "壁厚": 7.7, "检测类型": "测厚"},
    ]
    out = apply_docx_v2_omit_prefix_fill_guard(records, D_LAYER_CHUNK)
    # 扫描顺序中先出现的 4|7.6 属于组1 D9下，会先消费一条；组3顶还需要 D14
    d14 = [r for r in out if "D14" in str(r.get("受热面") or "")]
    assert any(str(r.get("管号")) == "4" and "D14" in str(r.get("受热面")) for r in out) or len(d14) >= 1


def test_skip_when_no_omit_prefix_form() -> None:
    chunk = "[DOCX_V2_TABLE idx=1 rows=2 cols=2]\nr0: c0='1' | c1='6.5'\nr1: c0='2' | c1='6.6'"
    records = [{"受热面": "x", "行号": "1", "管号": "1", "壁厚": 6.5, "检测类型": "测厚"}]
    out = apply_docx_v2_omit_prefix_fill_guard(records, chunk)
    assert out == records


def test_d12_up_tube_negative_on_fix() -> None:
    records = [
        {"受热面": "水冷壁D12吹灰器上", "行号": "1", "管号": "2", "壁厚": 7.4, "检测类型": "测厚"},
    ]
    out = apply_docx_v2_omit_prefix_fill_guard(records, D_LAYER_CHUNK)
    matched = [r for r in out if abs(float(r.get("壁厚") or 0) - 7.4) < 0.01 and abs(int(str(r.get("管号")))) == 2]
    assert matched
    # D12上2 → 管号应为 -2
    assert any(str(r.get("管号")) == "-2" for r in matched)
