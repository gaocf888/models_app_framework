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
    assert "周期沉降量" in q
    assert "最新一条记录" not in q
    assert "QUALIFY" in q
    assert "ROW_NUMBER()" in q
    assert "层位=0" in q or "层位 0" in q
    assert "F2(望京)" in q
    assert "recent_1_months" in q
    assert "2025-10-19" not in q


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
