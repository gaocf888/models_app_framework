"""气象表 station_name=F21-气象站 按站码对齐 device_station_map 场地。"""

from __future__ import annotations

from app.analysis_agent.deterministics.coverage import site_code
from app.analysis_agent.deterministics.fcb_endpoints import resolved_sql_params
from app.analysis_agent.deterministics.typical_curve import _series_by_project


def test_site_code_from_qxz_station_name() -> None:
    assert site_code("F21-气象站") == "F21"
    assert site_code("F8-气象站") == "F8"
    assert site_code("F21(西集)") == "F21"


def test_qxz_series_maps_engineering_project_via_station_code() -> None:
    rows = [
        {
            "project_name": "北京地面沉降监测",
            "station_name": "F21-气象站",
            "data_time": "2026-08-28",
            "real_time_rain": 0.3,
        },
        {
            "project_name": "北京地面沉降监测",
            "station_name": "F21-气象站",
            "data_time": "2026-08-27",
            "real_time_rain": 0.0,
        },
    ]
    series = _series_by_project(
        rows,
        "real_time_rain",
        device_type="qxz",
        prefer_station_code=True,
    )
    assert "F21(西集)" in series
    assert "F21-气象站" in series["F21(西集)"]
    assert len(series["F21(西集)"]["F21-气象站"]) == 2


def test_resolved_sql_params_binds_qxz_codes() -> None:
    params = resolved_sql_params(
        {"t_start": "2026-07-01T00:00:00", "t_end": "2026-10-01T00:00:00"},
        sql_file="forecast_series_qxz.sql",
    )
    assert params.get("qxz_projects")
    codes = params.get("qxz_codes") or []
    assert "F21" in codes
    assert "F8" in codes
    assert all(isinstance(c, str) and c[:1] == "F" for c in codes)
