"""V2-P3：重点区 YAML JOIN。"""

from __future__ import annotations

from app.analysis_agent.deterministics.key_areas import join_key_areas, load_key_area_stations


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
