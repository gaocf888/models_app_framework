"""V2-P4：典型曲线覆盖配对与 GNSS 关闭。"""

from __future__ import annotations

import app.analysis_agent.deterministics.typical_curve as tc_mod
from app.analysis_agent.deterministics.coverage import project_names


def test_no_dxswj_coverage_eliminates_station(monkeypatch) -> None:
    monkeypatch.setattr(tc_mod, "in_coverage", lambda name, dtype: False)
    monkeypatch.setattr(tc_mod, "project_names", lambda dtype: ())
    gathered = {
        "q_layer0": [{"project_name": "F21(西集)", "station_name": "F21-10", "delta_mm": -12}],
        "q_typical_fcb": [
            {"project_name": "F21(西集)", "station_name": "F21-10", "data_time": "2026-07-01", "total_settle": 1}
        ],
        "q_typical_dxswj": [
            {"project_name": "F21(西集)", "data_time": "2026-07-01", "elevation": 20}
        ],
    }
    assert tc_mod.typical_curve(gathered) == []


def test_qxz_out_of_coverage_no_rain_axis(monkeypatch) -> None:
    def _cov(name: str, dtype: str) -> bool:
        if dtype == "dxswj":
            return name == "F21(西集)"
        return False

    monkeypatch.setattr(tc_mod, "in_coverage", _cov)
    monkeypatch.setattr(tc_mod, "project_names", lambda dtype: ())
    gathered = {
        "q_layer0": [{"project_name": "F21(西集)", "station_name": "F21-10", "delta_mm": -12}],
        "q_typical_fcb": [
            {"project_name": "F21(西集)", "station_name": "F21-10", "data_time": "2026-07-01", "total_settle": 10},
            {"project_name": "F21(西集)", "station_name": "F21-10", "data_time": "2026-07-02", "total_settle": 9},
            {"project_name": "F21(西集)", "station_name": "F21-10", "data_time": "2026-07-03", "total_settle": 8},
        ],
        "q_typical_dxswj": [
            {"project_name": "F21(西集)", "data_time": "2026-07-01", "elevation": 20},
            {"project_name": "F21(西集)", "data_time": "2026-07-02", "elevation": 21},
            {"project_name": "F21(西集)", "data_time": "2026-07-03", "elevation": 22},
        ],
        "q_typical_qxz": [
            {"project_name": "F21(西集)", "data_time": "2026-07-01", "real_time_rain": 1.0},
        ],
    }
    rows = tc_mod.typical_curve(gathered)
    assert rows
    assert all(r.get("winner_kind") == "elevation" for r in rows)
    assert all(r.get("aux_kind") != "rain" for r in rows)


def test_gnss_empty_coverage_direction_closed() -> None:
    assert project_names("gnss") == ()
