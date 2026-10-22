"""V2-P4：典型曲线覆盖配对、公共集与设备选取。"""

from __future__ import annotations

import app.analysis_agent.deterministics.typical_curve as tc_mod
from app.analysis_agent.deterministics.coverage import project_names
from app.analysis_agent.deterministics.typical_sites import (
    compute_typical_curve_sites,
    load_typical_curve_sites,
)
from app.analysis_agent.renderers.charts_extra import chart_from_config


def test_typical_curve_sites_yaml_matches_map() -> None:
    loaded = load_typical_curve_sites()
    computed = compute_typical_curve_sites()
    assert loaded["pick_sites"] == computed["pick_sites"]
    assert "F42(牌楼站)" not in loaded["pick_sites"]
    assert "F10(洼里)" in loaded["pick_sites"]
    assert "F10(洼里)" in loaded["rain"]
    assert "F1(王四营)" not in loaded["rain"]


def test_no_pick_sites_eliminates_station(monkeypatch) -> None:
    monkeypatch.setattr(tc_mod, "in_typical_pick_sites", lambda name: False)
    gathered = {
        "q_layer0": [{"project_name": "F21(西集)", "station_name": "F21-10", "delta_mm": -12}],
        "q_typical_fcb": [
            {
                "project_name": "F21(西集)",
                "station_name": "F21-10",
                "data_time": "2026-07-01",
                "total_settle": 1,
            }
        ],
        "q_typical_dxswj": [
            {"project_name": "F21(西集)", "station_name": "D21-9", "data_time": "2026-07-01", "elevation": 20}
        ],
    }
    assert tc_mod.typical_curve(gathered) == []


def test_aux_uses_station_name_desc_first() -> None:
    gathered = {
        "q_layer0": [{"project_name": "F21(西集)", "station_name": "F21-10", "delta_mm": -12}],
        "q_typical_fcb": [
            {
                "project_name": "F21(西集)",
                "station_name": "F21-10",
                "data_time": day,
                "total_settle": 10 - i,
            }
            for i, day in enumerate(["2026-07-01", "2026-07-02", "2026-07-03"])
        ],
        "q_typical_dxswj": [
            {"project_name": "F21(西集)", "station_name": "D21-1", "data_time": "2026-07-01", "elevation": 1},
            {"project_name": "F21(西集)", "station_name": "D21-1", "data_time": "2026-07-02", "elevation": 1},
            {"project_name": "F21(西集)", "station_name": "D21-1", "data_time": "2026-07-03", "elevation": 1},
            {"project_name": "F21(西集)", "station_name": "D21-9", "data_time": "2026-07-01", "elevation": 20},
            {"project_name": "F21(西集)", "station_name": "D21-9", "data_time": "2026-07-02", "elevation": 21},
            {"project_name": "F21(西集)", "station_name": "D21-9", "data_time": "2026-07-03", "elevation": 22},
        ],
    }
    rows = tc_mod.typical_curve(gathered)
    assert rows
    assert all(r.get("aux_station_name") == "D21-9" for r in rows)
    assert all(r.get("settle_station_name") == "F21-10" for r in rows)
    assert all(r.get("aux_name") == "地下水标高" for r in rows)
    assert all(r.get("left_legend") == "F21-10分层标" for r in rows)
    assert all(r.get("right_legend") == "D21-9地下水" for r in rows)


def test_layer0_missing_skips_site_no_other_mark() -> None:
    gathered = {
        "q_layer0": [{"project_name": "F21(西集)", "station_name": "F21-10", "delta_mm": -12}],
        "q_typical_fcb": [
            {
                "project_name": "F21(西集)",
                "station_name": "F21-1",
                "data_time": "2026-07-01",
                "total_settle": 1,
            }
        ],
        "q_typical_dxswj": [
            {"project_name": "F21(西集)", "station_name": "D21-9", "data_time": "2026-07-01", "elevation": 20}
        ],
    }
    assert tc_mod.typical_curve(gathered) == []


def test_qxz_out_of_axis_sites_no_rain(monkeypatch) -> None:
    monkeypatch.setattr(tc_mod, "in_typical_axis_sites", lambda name, kind: kind == "elevation")
    gathered = {
        "q_layer0": [{"project_name": "F21(西集)", "station_name": "F21-10", "delta_mm": -12}],
        "q_typical_fcb": [
            {
                "project_name": "F21(西集)",
                "station_name": "F21-10",
                "data_time": day,
                "total_settle": 10 - i,
            }
            for i, day in enumerate(["2026-07-01", "2026-07-02", "2026-07-03"])
        ],
        "q_typical_dxswj": [
            {"project_name": "F21(西集)", "station_name": "D21-9", "data_time": day, "elevation": 20 + i}
            for i, day in enumerate(["2026-07-01", "2026-07-02", "2026-07-03"])
        ],
        "q_typical_qxz": [
            {"project_name": "F21(西集)", "station_name": "Q21-1", "data_time": "2026-07-01", "real_time_rain": 1.0},
        ],
    }
    rows = tc_mod.typical_curve(gathered)
    assert rows
    assert all(r.get("winner_kind") == "elevation" for r in rows)
    assert all(r.get("aux_kind") != "rain" for r in rows)


def test_gnss_empty_coverage_direction_closed() -> None:
    assert project_names("gnss") == ()


def test_dual_axis_chart_uses_aux_name_and_device_legend() -> None:
    rows = [
        {
            "x": "2026-07-01",
            "settle_mm": 1.0,
            "aux_value": 20.0,
            "aux_name": "地下水标高",
            "aux_unit": "m",
            "left_legend": "F21-10分层标",
            "right_legend": "D21-9地下水",
        }
    ]
    ch = chart_from_config(
        chart_id="c_typical_dual",
        chart_type="dual_axis",
        title="典型监测曲线",
        rows=rows,
        x_field="x",
        y_left={"field": "settle_mm", "name": "累计沉降", "unit": "mm"},
        y_right={"field": "aux_value", "name": "辅助序列", "unit": ""},
    )
    assert ch is not None
    spec = ch["spec"]
    assert spec["yRight"]["name"] == "地下水标高"
    assert spec["yRight"]["legend"] == "D21-9地下水"
    assert spec["yLeft"]["legend"] == "F21-10分层标"
