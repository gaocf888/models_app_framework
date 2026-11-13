"""季报沉降趋势预测：月均 OLS、三系列折线图与 compose 挂接。"""

from __future__ import annotations

import app.analysis_agent.deterministics.subsidence_forecast as sf_mod
from app.analysis_agent.deterministics.subsidence_forecast import (
    _ols,
    subsidence_forecast,
)
from app.analysis_agent.renderers.charts_extra import chart_from_config
from app.analysis_agent.renderers.configured_viz import render_configured_chart
from app.analysis_agent.renderers.section_data import build_audit_facts


def test_ols_negative_slope_on_linear_sink() -> None:
    xs = [float(i) for i in range(12)]
    ys = [-1.0 * i for i in range(12)]
    _a, b, r2 = _ols(xs, ys)
    assert b < 0
    assert r2 > 0.99


def _daily_rows(
    *,
    project: str,
    station: str,
    field: str,
    start_ym: str = "2025-01",
    months: int = 12,
    value_fn,
) -> list[dict]:
    y, m = int(start_ym[:4]), int(start_ym[5:7])
    rows: list[dict] = []
    for i in range(months):
        mm = m + i
        yy = y + (mm - 1) // 12
        mo = ((mm - 1) % 12) + 1
        day = f"{yy:04d}-{mo:02d}-15"
        rows.append(
            {
                "project_name": project,
                "station_name": station,
                "data_time": day,
                field: value_fn(i),
            }
        )
    return rows


def test_subsidence_forecast_emits_three_series_and_meta(monkeypatch) -> None:
    monkeypatch.setattr(sf_mod, "in_typical_pick_sites", lambda name: True)
    monkeypatch.setattr(
        sf_mod,
        "in_typical_axis_sites",
        lambda name, axis: axis in ("rain", "pressure"),
    )

    project = "F21(西集)"
    mark = "F21-10"
    gathered = {
        "q_layer0": [{"project_name": project, "station_name": mark, "delta_mm": -12.0}],
        "q_forecast_fcb": _daily_rows(
            project=project,
            station=mark,
            field="total_settle",
            value_fn=lambda i: -1.0 * i,
        ),
        "q_forecast_dxswj": _daily_rows(
            project=project,
            station="D21-9",
            field="elevation",
            value_fn=lambda i: 20.0 - 0.1 * i,
        ),
        "q_forecast_qxz": _daily_rows(
            project="北京地面沉降监测",
            station="F21-气象站",
            field="real_time_rain",
            value_fn=lambda i: float(i % 3),
        ),
        "q_forecast_kxsylj": _daily_rows(
            project=project,
            station="K21-1",
            field="pressure",
            value_fn=lambda i: 100.0 - 0.5 * i,
        ),
    }
    rows = subsidence_forecast(gathered)
    chart_rows = [r for r in rows if r.get("row_kind") == "chart"]
    metas = [r for r in rows if r.get("row_kind") == "meta"]
    assert metas and metas[0].get("status") == "ok"
    assert metas[0]["pred_direction"] == "沉降"
    assert metas[0]["slope_mm_per_month"] < 0
    assert metas[0]["history_month_count"] == 12
    assert metas[0]["corr_pressure"] > 0
    assert "孔隙水" in metas[0]["pressure_note"]
    assert metas[0]["pressure_station_name"] == "K21-1"
    assert metas[0]["rain_station_name"] == "F21-气象站"
    assert "无降水" not in metas[0]["rain_note"]
    assert "相关" in metas[0]["rain_note"]

    series_names = {r["series"] for r in chart_rows}
    assert series_names == {"实测累计沉降", "拟合趋势", "预测沉降"}
    pred = [r for r in chart_rows if r["series"] == "预测沉降"]
    # 衔接末月 + 未来3月
    assert len(pred) == 4
    assert pred[-1]["x"] == "2026-03"

    ch = chart_from_config(
        chart_id="c_forecast_trend",
        chart_type="line",
        title="沉降趋势预测（实测-拟合-预测）",
        rows=chart_rows,
        x_field="x",
        y_field="y",
        series_field="series",
        max_points=60,
    )
    assert ch is not None
    assert ch["chart_type"] == "line"
    names = [s["name"] for s in ch["spec"]["series"]]
    assert set(names) == {"实测累计沉降", "拟合趋势", "预测沉降"}
    cats = ch["spec"]["categories"]
    assert cats[0] == "2025-01"
    assert cats[-1] == "2026-03"
    by_name = {s["name"]: s["data"] for s in ch["spec"]["series"]}
    # 各系列与横轴同长；预测点落在末观测月及之后，不得挤到前几个月
    for pts in by_name.values():
        assert [p["x"] for p in pts] == cats
    pred_pts = by_name["预测沉降"]
    assert pred_pts[-1]["y"] is not None and pred_pts[-1]["x"] == "2026-03"
    assert pred_pts[0]["y"] is None
    assert pred_pts[cats.index("2025-12")]["y"] is not None


def test_subsidence_forecast_insufficient_without_water(monkeypatch) -> None:
    monkeypatch.setattr(sf_mod, "in_typical_pick_sites", lambda name: True)
    gathered = {
        "q_layer0": [{"project_name": "F21(西集)", "station_name": "F21-10", "delta_mm": -5}],
        "q_forecast_fcb": _daily_rows(
            project="F21(西集)",
            station="F21-10",
            field="total_settle",
            value_fn=lambda i: float(i),
        ),
        "q_forecast_dxswj": [],
        "q_forecast_qxz": [],
        "q_forecast_kxsylj": [],
    }
    rows = subsidence_forecast(gathered)
    assert len(rows) == 1
    assert rows[0]["status"] == "insufficient"
    assert rows[0]["row_kind"] == "meta"


def test_configured_chart_filters_chart_rows(monkeypatch) -> None:
    monkeypatch.setattr(sf_mod, "in_typical_pick_sites", lambda name: True)
    monkeypatch.setattr(sf_mod, "in_typical_axis_sites", lambda *_a, **_k: False)
    project = "F21(西集)"
    mark = "F21-10"
    gathered = {
        "q_forecast": subsidence_forecast(
            {
                "q_layer0": [{"project_name": project, "station_name": mark, "delta_mm": -8}],
                "q_forecast_fcb": _daily_rows(
                    project=project,
                    station=mark,
                    field="total_settle",
                    value_fn=lambda i: -0.5 * i,
                ),
                "q_forecast_dxswj": _daily_rows(
                    project=project,
                    station="D21-9",
                    field="elevation",
                    value_fn=lambda i: 18.0,
                ),
                "q_forecast_qxz": [],
                "q_forecast_kxsylj": [],
            }
        )
    }
    ch = render_configured_chart(
        {
            "id": "c_forecast_trend",
            "title": "沉降趋势预测（实测-拟合-预测）",
            "chart_type": "line",
            "source_item_ids": ["q_forecast"],
            "x_field": "x",
            "y_field": "y",
            "series_field": "series",
            "row_filter": {"field": "row_kind", "equals": "chart"},
            "max_points": 60,
        },
        gathered_data=gathered,
    )
    assert ch is not None
    assert len(ch["spec"]["series"]) == 3


def test_forecast_audit_facts_use_meta_only(monkeypatch) -> None:
    monkeypatch.setattr(sf_mod, "in_typical_pick_sites", lambda name: True)
    monkeypatch.setattr(
        sf_mod,
        "in_typical_axis_sites",
        lambda name, axis: axis == "pressure",
    )
    project = "F21(西集)"
    mark = "F21-10"
    rows = subsidence_forecast(
        {
            "q_layer0": [{"project_name": project, "station_name": mark, "delta_mm": -8}],
            "q_forecast_fcb": _daily_rows(
                project=project,
                station=mark,
                field="total_settle",
                value_fn=lambda i: -0.5 * i,
            ),
            "q_forecast_dxswj": _daily_rows(
                project=project,
                station="D21-9",
                field="elevation",
                value_fn=lambda i: 18.0,
            ),
            "q_forecast_qxz": [],
            "q_forecast_kxsylj": _daily_rows(
                project=project,
                station="K21-1",
                field="pressure",
                value_fn=lambda i: 90.0 + i,
            ),
        }
    )
    facts = build_audit_facts({"q_forecast": rows}, "")
    assert "代表站" in facts
    assert "统计外推" in facts
    assert "孔隙水依据" in facts
    assert "实测累计沉降" not in facts
