"""V2-P3：重点区 YAML JOIN。"""

from __future__ import annotations

from app.analysis_agent.deterministics.key_areas import join_key_areas, load_key_area_stations
from app.analysis_agent.renderers.section_data import build_audit_facts


def test_yaml_five_areas_thirteen_stations() -> None:
    stations = load_key_area_stations()
    assert len(stations) == 13
    areas = {s["area_name"] for s in stations}
    assert len(areas) == 5


def test_join_missing_compress_is_none() -> None:
    gathered = {
        "q_layer0": [
            {"project_name": "F17(沟渠庄)", "delta_mm": 1.2},
        ],
        "q_compress": [],
    }
    rows = join_key_areas(gathered)
    f17 = next(r for r in rows if r["station_code"] == "F17")
    assert f17["total_delta_mm"] == 1.2
    assert f17["c01"] is None
    assert f17["c34"] is None
    f3 = next(r for r in rows if r["station_code"] == "F3")
    # YAML 全角括号应对齐到覆盖名单 F3(天竺)
    assert f3["project_name"] == "F3(天竺)"


def test_join_keeps_first_compress_layer_zero() -> None:
    gathered = {
        "q_layer0": [{"project_name": "F17(沟渠庄)", "delta_mm": -3.0}],
        "q_compress": [
            {"project_name": "F17(沟渠庄)", "layer_from": 0, "layer_to": 1, "compress_mm": -1.5},
            {"project_name": "F17(沟渠庄)", "layer_from": 1, "layer_to": 2, "compress_mm": -0.4},
        ],
    }
    rows = join_key_areas(gathered)
    f17 = next(r for r in rows if r["station_code"] == "F17")
    assert f17["c01"] == -1.5
    assert f17["c12"] == -0.4


def test_key_area_facts_area_summary_not_per_station_layers() -> None:
    rows = join_key_areas(
        {
            "q_layer0": [
                {"project_name": "F17(沟渠庄)", "delta_mm": 1.2},
                {"project_name": "F21(西集)", "delta_mm": 0.5},
                {"project_name": "F4(东坝)", "delta_mm": -4.74},
                {"project_name": "F38(东七仙)", "delta_mm": -7.94},
            ],
            "q_compress": [
                {
                    "project_name": "F4(东坝)",
                    "layer_from": 0,
                    "layer_to": 1,
                    "compress_mm": 0.5,
                },
                {
                    "project_name": "F38(东七仙)",
                    "layer_from": 0,
                    "layer_to": 1,
                    "compress_mm": -1.0,
                },
            ],
        }
    )
    facts = build_audit_facts({"q_key_area": rows}, "季报", slot_id="key_area")
    assert "重点监测区地面沉降" in facts
    assert "禁止逐站罗列" in facts
    assert "【城市副中心】" in facts or "城市副中心" in facts
    assert "最大/次之地面" in facts
    assert "分层监测概况" in facts
    assert "第一压缩层组沉降" in facts or "第一压缩层组" in facts
    # 不应再出现「总沉降回弹…；第一压缩层组…；第二压缩层组…」的逐站串
    assert "总沉降回弹" not in facts
    assert "待补充" in facts
    assert "F3" in facts or "首都国际机场" in facts


def test_compress_facts_filter_by_layer_not_total_rows() -> None:
    rows = [
        {"project_name": "F8(周村)", "layer_from": 0, "compress_mm": -1.2, "display_clause": "位于平谷区的F8监测站"},
        {"project_name": "F8(周村)", "layer_from": 1, "compress_mm": -0.5, "display_clause": "位于平谷区的F8监测站"},
        {"project_name": "A", "layer_from": 0, "compress_mm": -2.0, "display_clause": "位于朝阳区的A监测站"},
    ]
    # 故意混入大量其它层行，模拟全量 163 行场景
    rows.extend(
        {"project_name": f"X{i}", "layer_from": 2, "compress_mm": -0.1, "display_clause": f"站{i}"}
        for i in range(20)
    )
    facts = build_audit_facts({"q_compress": rows}, "季报", slot_id="layer_results__l01")
    assert "本层组2座" in facts
    assert "163" not in facts
    assert "第一压缩层组：2座" in facts
    conclusion = build_audit_facts({"q_compress": rows}, "季报", slot_id="conclusion")
    assert "禁止把四个层组行数相加" in conclusion or "禁止" in conclusion
    assert "第一压缩层组：2座" in conclusion
    assert "第三压缩层组：20座" in conclusion
