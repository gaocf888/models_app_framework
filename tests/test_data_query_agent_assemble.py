"""assemble grain / HUD 开关。"""

from __future__ import annotations

from app.data_query_agent.acquire import AcquireItemResult, AcquireResult
from app.data_query_agent.assemble import assemble_result
from app.data_query_agent.catalog import clear_library_catalog_cache, get_library_catalog
from app.data_query_agent.scope_intent import infer_result_grain, resolve_scope_intent


def test_infer_district_grain() -> None:
    assert infer_result_grain("各区平均沉降") == "district"
    assert infer_result_grain("朝阳区最新分层标") == "station"


def test_assemble_district_enables_entity_hud() -> None:
    cat = get_library_catalog()
    lib = cat.get("fcb")
    assert lib is not None
    scope = resolve_scope_intent("各区平均沉降", lib)
    assert scope.grain == "district"
    acquire = AcquireResult(
        ok=True,
        list_item=AcquireItemResult(
            plan_item_id="q_list",
            ok=True,
            sql="SELECT area FROM t_data_wash_fcb",
            rows=[{"area": "朝阳区", "total_settle": -12.0, "station_count": 3}],
        ),
        series_item=AcquireItemResult(
            plan_item_id="q_hud_series",
            ok=True,
            sql="SELECT area, data_time, total_settle FROM t_data_wash_fcb",
            rows=[
                {"area": "朝阳区", "data_time": "2025-01-01", "total_settle": -10.0},
                {"area": "朝阳区", "data_time": "2026-01-01", "total_settle": -12.0},
            ],
        ),
    )
    payload = assemble_result(
        library=lib,
        scope=scope,
        acquire=acquire,
        include_hud=True,
        expose_sql=False,
    )
    assert payload["result_grain"] == "district"
    assert payload["hud_enabled"] is True
    assert payload["hud_by_station"] == {}
    row = payload["list"][0]
    assert row["hud_entity_type"] == "district"
    assert row["hud_entity_id"] == "朝阳区"
    assert row["hud_available"] is True
    panel = payload["hud_by_entity"]["朝阳区"]
    assert panel["entity_type"] == "district"
    assert panel["series"]["agg"] == "avg"
    assert panel["blocks"]["geology"]["status"] == "unavailable"
    assert panel["blocks"]["strategy"]["status"] == "unavailable"
    assert len(panel["series"]["points"]) == 2
    # 禁止用站点序列冒充区 HUD
    assert "JZGZ" not in payload["hud_by_entity"]


def test_assemble_district_ignores_station_series() -> None:
    cat = get_library_catalog()
    lib = cat.get("fcb")
    assert lib is not None
    scope = resolve_scope_intent("各区平均沉降", lib)
    acquire = AcquireResult(
        ok=True,
        list_item=AcquireItemResult(
            plan_item_id="q_list",
            ok=True,
            sql="SELECT area FROM t_data_wash_fcb",
            rows=[{"area": "朝阳区", "total_settle": -12.0, "station_count": 3}],
        ),
        series_item=AcquireItemResult(
            plan_item_id="q_hud_series",
            ok=True,
            sql="SELECT station_id FROM t_data_wash_fcb",
            rows=[{"station_id": "JZGZ", "data_time": "2026-01-01", "total_settle": -99.0}],
        ),
    )
    payload = assemble_result(
        library=lib,
        scope=scope,
        acquire=acquire,
        include_hud=True,
        expose_sql=False,
    )
    assert payload["hud_by_entity"]["朝阳区"]["series"]["points"] == []


def test_assemble_lithology_warning() -> None:
    cat = get_library_catalog()
    lib = cat.get("fcb")
    assert lib is not None
    scope = resolve_scope_intent("朝阳区岩性第3层分层标", lib)
    assert scope.lithology_warning is True
    acquire = AcquireResult(
        ok=True,
        list_item=AcquireItemResult(
            plan_item_id="q_list",
            ok=True,
            sql="SELECT * FROM t_data_wash_fcb",
            rows=[{"station_id": "JZGZ", "station_name": "金盏公交", "area": "朝阳区", "total_settle": -1}],
        ),
        series_item=AcquireItemResult(
            plan_item_id="q_hud_series",
            ok=True,
            sql="SELECT station_id, data_time, total_settle FROM t_data_wash_fcb",
            rows=[{"station_id": "JZGZ", "data_time": "2026-01-01", "total_settle": -1}],
        ),
    )
    payload = assemble_result(
        library=lib,
        scope=scope,
        acquire=acquire,
        include_hud=True,
        expose_sql=False,
    )
    assert "lithology_unsupported" in payload["warnings"]
    assert "layer_unsupported" in payload["warnings"]
    assert payload["hud_by_entity"]["JZGZ"]["blocks"]["geology"]["status"] == "unavailable"


def test_assemble_district_truncation(monkeypatch) -> None:
    from app.core.config import get_app_config

    get_app_config.cache_clear()
    monkeypatch.setattr(get_app_config().data_query_agent, "hud_max_districts", 1)
    cat = get_library_catalog()
    lib = cat.get("fcb")
    assert lib is not None
    scope = resolve_scope_intent("各区平均沉降", lib)
    acquire = AcquireResult(
        ok=True,
        list_item=AcquireItemResult(
            plan_item_id="q_list",
            ok=True,
            sql="SELECT area FROM t_data_wash_fcb",
            rows=[
                {"area": "朝阳区", "total_settle": -12.0, "station_count": 3},
                {"area": "大兴区", "total_settle": -8.0, "station_count": 2},
            ],
        ),
        series_item=AcquireItemResult(
            plan_item_id="q_hud_series",
            ok=True,
            sql="SELECT area, data_time, total_settle FROM t_data_wash_fcb",
            rows=[
                {"area": "朝阳区", "data_time": "2026-01-01", "total_settle": -12.0},
                {"area": "大兴区", "data_time": "2026-01-01", "total_settle": -8.0},
            ],
        ),
    )
    payload = assemble_result(
        library=lib,
        scope=scope,
        acquire=acquire,
        include_hud=True,
        expose_sql=False,
    )
    assert "hud_series_truncated" in payload["warnings"]
    assert payload["list"][0]["hud_available"] is True
    assert payload["list"][1]["hud_available"] is False
    assert "朝阳区" in payload["hud_by_entity"]
    assert "大兴区" not in payload["hud_by_entity"]


def test_assemble_city_entity() -> None:
    cat = get_library_catalog()
    lib = cat.get("fcb")
    assert lib is not None
    scope = resolve_scope_intent("全市平均沉降", lib)
    assert scope.grain == "city"
    acquire = AcquireResult(
        ok=True,
        list_item=AcquireItemResult(
            plan_item_id="q_list",
            ok=True,
            sql="SELECT AVG(total_settle)",
            rows=[{"total_settle": -8.0, "station_count": 20}],
        ),
        series_item=AcquireItemResult(
            plan_item_id="q_hud_series",
            ok=True,
            sql="SELECT data_time, total_settle",
            rows=[{"data_time": "2026-01-01", "total_settle": -8.0}],
        ),
    )
    payload = assemble_result(
        library=lib,
        scope=scope,
        acquire=acquire,
        include_hud=True,
        expose_sql=False,
    )
    assert payload["hud_enabled"] is True
    assert payload["list"][0]["hud_entity_id"] == "beijing"
    assert payload["list"][0]["area"] == "全市"
    assert payload["hud_by_entity"]["beijing"]["series"]["agg"] == "avg"
    assert payload["hud_by_station"] == {}


def test_assemble_station_enables_hud() -> None:
    cat = get_library_catalog()
    lib = cat.get("fcb")
    assert lib is not None
    scope = resolve_scope_intent("朝阳区最新分层标", lib)
    scope.annual_window = {
        "start": "2020-01-01 00:00:00",
        "end": "2026-12-31 23:59:59",
        "source": "intent",
        "tag": "",
    }
    acquire = AcquireResult(
        ok=True,
        list_item=AcquireItemResult(
            plan_item_id="q_list",
            ok=True,
            sql="SELECT * FROM t_data_wash_fcb",
            rows=[
                {
                    "station_id": "JZGZ",
                    "station_name": "金盏公交",
                    "area": "朝阳区",
                    "total_settle": -418.5,
                    "data_time": "2026-08-01",
                }
            ],
        ),
        series_item=AcquireItemResult(
            plan_item_id="q_hud_series",
            ok=True,
            sql="SELECT * FROM t_data_wash_fcb",
            rows=[
                {"station_id": "JZGZ", "data_time": "2020-01-01", "total_settle": -10.2},
                {"station_id": "JZGZ", "data_time": "2021-01-01", "total_settle": -90.0},
            ],
        ),
    )
    payload = assemble_result(
        library=lib,
        scope=scope,
        acquire=acquire,
        include_hud=True,
        expose_sql=True,
    )
    assert payload["hud_enabled"] is True
    assert payload["list"][0]["hud_available"] is True
    assert payload["list"][0]["hud_entity_type"] == "station"
    assert payload["list"][0]["hud_entity_id"] == "JZGZ"
    assert "JZGZ" in payload["hud_by_station"]
    assert payload["hud_by_entity"]["JZGZ"]["blocks"]["geology"]["status"] == "unavailable"
    assert payload["sql"]["q_list"]
    assert payload["list"][0]["annual_settle_mm"] == -79.8


def test_assemble_hud_truncation(monkeypatch) -> None:
    from app.core.config import get_app_config

    get_app_config.cache_clear()
    monkeypatch.setattr(get_app_config().data_query_agent, "hud_max_stations", 1)
    cat = get_library_catalog()
    lib = cat.get("fcb")
    assert lib is not None
    scope = resolve_scope_intent("朝阳区最新分层标", lib)
    acquire = AcquireResult(
        ok=True,
        list_item=AcquireItemResult(
            plan_item_id="q_list",
            ok=True,
            sql="SELECT * FROM t_data_wash_fcb",
            rows=[
                {"station_id": "A", "station_name": "A站", "area": "朝阳区", "total_settle": -1},
                {"station_id": "B", "station_name": "B站", "area": "朝阳区", "total_settle": -2},
            ],
        ),
        series_item=AcquireItemResult(
            plan_item_id="q_hud_series",
            ok=True,
            sql="SELECT * FROM t_data_wash_fcb",
            rows=[
                {"station_id": "A", "data_time": "2026-01-01", "total_settle": -1},
                {"station_id": "B", "data_time": "2026-01-01", "total_settle": -2},
            ],
        ),
    )
    payload = assemble_result(
        library=lib,
        scope=scope,
        acquire=acquire,
        include_hud=True,
        expose_sql=False,
    )
    assert "hud_series_truncated" in payload["warnings"]
    assert payload["list"][0]["hud_available"] is True
    assert payload["list"][1]["hud_available"] is False
    assert "A" in payload["hud_by_station"]
    assert "B" not in payload["hud_by_station"]


def test_path1_scope_chaoyang() -> None:
    cat = get_library_catalog()
    lib = cat.get("fcb")
    assert lib is not None
    scope = resolve_scope_intent("朝阳区年沉降比较大的监测点", lib)
    assert scope.confirmed_scope.get("district") == "朝阳区"
    assert scope.confirmed_scope.get("device_type") == "fcb"


def test_path2_scope_daxing() -> None:
    cat = get_library_catalog()
    lib = cat.get("fcb")
    assert lib is not None
    scope = resolve_scope_intent("大兴区有哪些分层监测点？", lib)
    assert scope.confirmed_scope.get("district") == "大兴区"


def test_assemble_gnss_not_total_settle() -> None:
    cat = get_library_catalog()
    lib = cat.get("gnss")
    assert lib is not None
    keys = {c.key for c in lib.columns}
    assert "total_settle_mm" not in keys
    assert "displacement_3d" in keys


def test_assemble_qxz_series_list() -> None:
    clear_library_catalog_cache()
    cat = get_library_catalog()
    lib = cat.get("qxz")
    assert lib is not None
    assert [m.id for m in lib.series_metrics] == ["temp", "real_time_rain"]
    scope = resolve_scope_intent("朝阳区气象", lib)
    acquire = AcquireResult(
        ok=True,
        list_item=AcquireItemResult(
            plan_item_id="q_list",
            ok=True,
            sql="SELECT * FROM t_data_wash_qxz",
            rows=[{"station_id": "Q1", "station_name": "气象1", "area": "朝阳区", "temp": 12.0}],
        ),
        series_item=AcquireItemResult(
            plan_item_id="q_hud_series",
            ok=True,
            sql="SELECT station_id, data_time, temp, real_time_rain",
            rows=[
                {"station_id": "Q1", "data_time": "2026-01-01", "temp": 10.0, "real_time_rain": 1.2},
                {"station_id": "Q1", "data_time": "2026-01-02", "temp": 12.0, "real_time_rain": 0.0},
            ],
        ),
    )
    payload = assemble_result(
        library=lib, scope=scope, acquire=acquire, include_hud=True, expose_sql=False
    )
    panel = payload["hud_by_entity"]["Q1"]
    assert panel["series"]["metric"] == "temp"
    ids = [s["id"] for s in panel["series_list"]]
    assert ids == ["temp", "real_time_rain"]
    assert len(panel["series_list"][1]["points"]) == 2


def test_scope_explicit_district_overrides_nl() -> None:
    cat = get_library_catalog()
    lib = cat.get("fcb")
    assert lib is not None
    scope = resolve_scope_intent("朝阳区最新分层标", lib, district="大兴区")
    assert scope.confirmed_scope.get("district") == "大兴区"
    assert "scope_nl_overridden" in (scope.warnings or [])
