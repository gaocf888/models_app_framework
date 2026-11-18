"""fcb / jyb / gnss 问句模板烟测：锁表列、禁止 NULL 占位、GNSS 不用 total_settle。"""

from __future__ import annotations

from app.data_query_agent.acquire import _render_question, clear_plan_cache
from app.data_query_agent.catalog import get_library_catalog
from app.data_query_agent.scope_intent import resolve_scope_intent


def test_templates_fcb_jyb_gnss_no_null_placeholder() -> None:
    clear_plan_cache()
    cat = get_library_catalog()
    cases = [
        ("fcb", "t_data_wash_fcb", "total_settle", None),
        ("jyb", "t_data_wash_jyb", "total_settle", None),
        ("gnss", "t_data_wash_gnss", "displacement_3d", "total_settle"),
    ]
    for lid, table, core, forbidden in cases:
        lib = cat.get(lid)
        assert lib is not None
        scope = resolve_scope_intent("朝阳区最新监测点", lib)
        q_list = _render_question(
            plan_item_id="q_list",
            grain="station",
            library=lib,
            scope=scope,
            max_rows=50,
        )
        q_hud = _render_question(
            plan_item_id="q_hud_series",
            grain="station",
            library=lib,
            scope=scope,
            max_rows=50,
        )
        for q in (q_list, q_hud):
            assert table in q
            assert core in q
            assert "NULL AS" not in q.upper().replace("  ", " ")
            if forbidden:
                assert forbidden not in q
        assert "ORDER BY ABS(" in q_list
        assert f"ABS({core})" in q_list
        assert "DISTINCT ON (station_name)" in q_list
        assert "禁止 DISTINCT ON (project_name)" in q_list
        if lid == "fcb":
            assert "全部测点" in q_list
            assert "禁止用监测层位=0" in q_list
        if lid != "qxz":
            assert "末日值" in q_list or "end_value" in q_list.lower() or "年度变化" in q_list


def test_templates_district_hud_is_area_avg() -> None:
    clear_plan_cache()
    cat = get_library_catalog()
    lib = cat.get("fcb")
    assert lib is not None
    scope = resolve_scope_intent("各区平均沉降", lib)
    q = _render_question(
        plan_item_id="q_hud_series",
        grain="district",
        library=lib,
        scope=scope,
        max_rows=50,
    )
    assert "t_data_wash_fcb" in q
    assert "AVG(" in q
    assert "area" in q
    assert "禁止输出单个 station_id" in q


def test_templates_gnss_qxz_forbid_project_name_site_filter() -> None:
    clear_plan_cache()
    cat = get_library_catalog()
    for lid, needle in (("gnss", "F15-GNSS"), ("qxz", "F33-气象站")):
        lib = cat.get(lid)
        assert lib is not None
        scope = resolve_scope_intent("朝阳区最新监测点", lib)
        q = _render_question(
            plan_item_id="q_list",
            grain="station",
            library=lib,
            scope=scope,
            max_rows=50,
        )
        assert "禁止用 project_name" in q
        assert needle.split("-")[0] in q or "station_name" in q
        assert ".project_name = t_station.name" not in q


def test_templates_qxz_list_selects_all_weather_metrics() -> None:
    clear_plan_cache()
    cat = get_library_catalog()
    lib = cat.get("qxz")
    assert lib is not None
    scope = resolve_scope_intent("周村最新的气象站监测数据", lib)
    q_list = _render_question(
        plan_item_id="q_list",
        grain="station",
        library=lib,
        scope=scope,
        max_rows=50,
    )
    q_hud = _render_question(
        plan_item_id="q_hud_series",
        grain="station",
        library=lib,
        scope=scope,
        max_rows=50,
    )
    for col in ("temp", "real_time_rain", "humidity", "pressure", "wind_speed", "wind_dirt"):
        assert col in q_list
        assert col in q_hud


def test_templates_qxz_dual_series_select() -> None:
    clear_plan_cache()
    cat = get_library_catalog()
    lib = cat.get("qxz")
    assert lib is not None
    scope = resolve_scope_intent("朝阳区气象", lib)
    q = _render_question(
        plan_item_id="q_hud_series",
        grain="station",
        library=lib,
        scope=scope,
        max_rows=50,
    )
    assert "temp" in q
    assert "real_time_rain" in q


def test_templates_period_station_uses_window_delta_not_latest_row() -> None:
    clear_plan_cache()
    cat = get_library_catalog()
    lib = cat.get("fcb")
    assert lib is not None
    scope = resolve_scope_intent("请帮我查询F2(望京)的最近一个月的沉降量", lib)
    assert scope.time_snapshot.get("time_window_tag") == "recent_1_months"
    q = _render_question(
        plan_item_id="q_list",
        grain="station",
        library=lib,
        scope=scope,
        max_rows=50,
    )
    assert "周期变化量" in q or "周期沉降" in q
    assert "最新一条记录" not in q
    assert "QUALIFY" in q
    assert "ROW_NUMBER()" in q
    assert "层位=0" in q or "层位 0" in q
    assert "全部测点" not in q
    assert "F2(望京)" in q
    assert "recent_1_months" in q
    assert "2025-10-19" not in q


def test_templates_kxsylj_period_expands_all_devices() -> None:
    clear_plan_cache()
    cat = get_library_catalog()
    lib = cat.get("kxsylj")
    assert lib is not None
    scope = resolve_scope_intent("请帮我查询F8(周村)最近一个月的孔隙水压力变化量", lib)
    q = _render_question(
        plan_item_id="q_list",
        grain="station",
        library=lib,
        scope=scope,
        max_rows=50,
    )
    assert "一行一设备" in q
    assert "层位=0 的代表标" not in q
    assert "禁止用层位0" in q


def test_templates_city_hud_is_daily_avg() -> None:
    clear_plan_cache()
    cat = get_library_catalog()
    lib = cat.get("fcb")
    assert lib is not None
    scope = resolve_scope_intent("全市平均沉降", lib)
    q = _render_question(
        plan_item_id="q_hud_series",
        grain="city",
        library=lib,
        scope=scope,
        max_rows=50,
    )
    assert "t_data_wash_fcb" in q
    assert "AVG(" in q
    assert "不要按站或按区拆行" in q
