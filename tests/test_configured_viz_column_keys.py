"""报告声明式表 column_keys / 图 x_field 兼容。"""

from __future__ import annotations


def test_map_rows_by_column_keys() -> None:
    from app.analysis_agent.renderers.configured_viz import (
        _chart_xy_fields,
        _map_rows_by_column_keys,
        render_configured_table,
    )

    rows = [{"device_type": "GNSS", "online_rate": 98.5, "total": 10}]
    display = ["仪器类型", "在线率(%)", "总数"]
    keys = ["device_type", "online_rate", "total"]
    mapped = _map_rows_by_column_keys(rows, display_columns=display, column_keys=keys)
    assert mapped[0]["仪器类型"] == "GNSS"
    assert mapped[0]["在线率(%)"] == 98.5

    md, tbl = render_configured_table(
        {
            "id": "t1",
            "title": "测试",
            "source_item_ids": ["q1"],
            "columns": display,
            "column_keys": keys,
        },
        gathered_data={"q1": rows},
    )
    assert "GNSS" in md
    assert "98.5" in md or "98.5" in str(tbl)

    x, y, _ = _chart_xy_fields({"x_key": "device_type", "y_key": "online_rate"})
    assert x == "device_type"
    assert y == "online_rate"
