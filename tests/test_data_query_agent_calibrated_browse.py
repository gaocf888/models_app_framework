"""点站点人工清洗浏览：映射、宽表 SQL、data_source 开关。不连 PG。"""

from __future__ import annotations

import pytest

from app.data_query_agent.browse import BrowseRequestError, fetch_browse_list
from app.data_query_agent.calibrated_browse import (
    build_calibrated_page_sql,
    display_device_name,
    lookup_calibrated_table,
    parse_browse_data_source,
)
from app.models.data_query_agent import DataQueryAgentBrowseResponse


class _CalibExecutor:
    def __init__(
        self,
        *,
        columns: list[dict] | None = None,
        total: int = 2,
        latest: str = "2026-10-01 12:00:00",
        rows: list[dict] | None = None,
    ) -> None:
        self.calls: list[tuple[str, dict]] = []
        self._columns = columns or [
            {"column_name": "id"},
            {"column_name": "caijishijian"},
            {"column_name": "f1-1"},
            {"column_name": "f1-2"},
            {"column_name": "f1-7"},
        ]
        self._total = total
        self._latest = latest
        self._rows = rows or [
            {
                "data_time": "2026-10-01 12:00:00",
                "F1-1": -122.892,
                "F1-2": -422.526,
                "F1-7": -616.584,
            },
            {
                "data_time": "2026-10-01 00:00:00",
                "F1-1": -122.894,
                "F1-2": -422.526,
                "F1-7": -616.574,
            },
        ]

    async def execute(self, sql: str, params: dict | None = None) -> list[dict]:
        self.calls.append((sql, dict(params or {})))
        low = sql.lower()
        if "information_schema.columns" in low:
            return list(self._columns)
        if "count(*)" in low:
            return [{"total": self._total, "latest": self._latest}]
        return list(self._rows)


def test_parse_data_source_aliases() -> None:
    assert parse_browse_data_source(None) == "realtime"
    assert parse_browse_data_source("default") == "realtime"
    assert parse_browse_data_source("calibrated") == "calibrated"
    assert parse_browse_data_source("manual") == "calibrated"
    with pytest.raises(BrowseRequestError) as exc:
        parse_browse_data_source("wash")
    assert exc.value.status_code == 422
    assert exc.value.code == "invalid_data_source"


def test_lookup_fcb_and_missing_dxswj_f42() -> None:
    assert lookup_calibrated_table("fcb", "F1(王四营)") == "sheet_lcgoee"
    assert lookup_calibrated_table("fcb", "F9(后芮营)") == "sheet_wccriy"
    assert lookup_calibrated_table("dxswj", "F1(王四营)") == "sheet_tyxmeg"
    assert lookup_calibrated_table("kxsylj", "F1(王四营)") == "sheet_hzggii"
    assert lookup_calibrated_table("fcb", "F42(牌楼站)") == "sheet_savbyg"
    assert lookup_calibrated_table("dxswj", "F42(牌楼站)") is None
    assert lookup_calibrated_table("gnss", "F1(王四营)") is None


def test_display_device_name_normalizes_columns() -> None:
    assert display_device_name("f1-1", ()) == "F1-1"
    assert display_device_name("d1-1biaogao", ("biaogao",)) == "D1-1"
    assert display_device_name("k1-1kf", ()) == "K1-1kf"
    assert display_device_name("k1-1p", ()) == "K1-1p"


def test_calibrated_page_sql_quotes_hyphen_columns() -> None:
    sql, params = build_calibrated_page_sql(
        "sheet_lcgoee",
        "caijishijian",
        [("f1-1", "F1-1"), ("f1-7", "F1-7")],
        limit=20,
        offset=0,
    )
    assert '"sheet_lcgoee"' in sql
    assert '"f1-1"' in sql
    assert '"F1-1"' in sql
    assert "caijishijian" in sql
    assert "t_data_wash" not in sql.lower()
    assert params["limit"] == 20
    assert params["offset"] == 0


@pytest.mark.asyncio
async def test_calibrated_station_browse_wide_table() -> None:
    exe = _CalibExecutor()
    payload = await fetch_browse_list(
        library_id="fcb",
        station_id="F1(王四营)",
        data_source="calibrated",
        page=1,
        page_size=20,
        executor=exe,
    )
    assert payload["data_source"] == "calibrated"
    assert payload["calibrated_supported"] is True
    assert payload["result_grain"] == "device_time"
    assert payload["metric_title"] == "累计沉降(mm)"
    assert payload["hud_enabled"] is False
    assert [c["key"] for c in payload["columns"]] == ["data_time", "F1-1", "F1-2", "F1-7"]
    assert payload["list"][0]["F1-1"] == pytest.approx(-122.892)
    assert payload["list"][0]["F1-7"] == pytest.approx(-616.584)
    assert any("仅每日" in w for w in payload["warnings"])
    assert any("2026-10-01" in w for w in payload["warnings"])
    catalog_sql = next(sql for sql, _ in exe.calls if "information_schema.columns" in sql.lower())
    assert catalog_sql
    page_sql = next(sql for sql, p in exe.calls if p.get("limit") == 20)
    assert '"sheet_lcgoee"' in page_sql
    assert "t_data_wash_fcb" not in page_sql
    DataQueryAgentBrowseResponse(**payload)


@pytest.mark.asyncio
async def test_calibrated_dxswj_strips_biaogao_and_hints_elevation() -> None:
    exe = _CalibExecutor(
        columns=[
            {"column_name": "id"},
            {"column_name": "caijishijian"},
            {"column_name": "d1-1biaogao"},
            {"column_name": "d1-2biaogao"},
        ],
        rows=[
            {"data_time": "2026-10-01 00:00:00", "D1-1": 0.992, "D1-2": 1.826},
        ],
        total=1,
    )
    payload = await fetch_browse_list(
        library_id="dxswj",
        station_id="F1(王四营)",
        data_source="calibrated",
        page=1,
        page_size=20,
        executor=exe,
    )
    assert [c["key"] for c in payload["columns"][1:]] == ["D1-1", "D1-2"]
    assert payload["metric_title"] == "水位标高(m)"
    assert any("标高" in w for w in payload["warnings"])
    page_sql = next(sql for sql, p in exe.calls if p.get("limit") == 20)
    assert '"d1-1biaogao"' in page_sql
    assert '"D1-1"' in page_sql


@pytest.mark.asyncio
async def test_calibrated_without_station_is_422() -> None:
    with pytest.raises(BrowseRequestError) as exc:
        await fetch_browse_list(
            library_id="fcb",
            data_source="calibrated",
            page=1,
            page_size=20,
            executor=_CalibExecutor(),
        )
    assert exc.value.status_code == 422
    assert exc.value.code == "calibrated_requires_station"


@pytest.mark.asyncio
async def test_calibrated_unsupported_library_is_422() -> None:
    with pytest.raises(BrowseRequestError) as exc:
        await fetch_browse_list(
            library_id="gnss",
            station_id="DAD01",
            data_source="calibrated",
            page=1,
            page_size=20,
            executor=_CalibExecutor(),
        )
    assert exc.value.status_code == 422
    assert exc.value.code == "calibrated_library_unsupported"


@pytest.mark.asyncio
async def test_calibrated_missing_station_returns_empty() -> None:
    exe = _CalibExecutor()
    payload = await fetch_browse_list(
        library_id="dxswj",
        station_id="F42(牌楼站)",
        data_source="calibrated",
        page=1,
        page_size=20,
        executor=exe,
    )
    assert payload["total"] == 0
    assert payload["list"] == []
    assert payload["calibrated_supported"] is False
    assert any("暂无人工清洗数据" in w for w in payload["warnings"])
    assert exe.calls == []


class _RealtimeExecutor:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []

    async def execute(self, sql: str, params: dict | None = None) -> list[dict]:
        self.calls.append((sql, dict(params or {})))
        low = sql.lower()
        if "count(*)" in low:
            return [{"total": 2}]
        if "distinct w.station_name" in low:
            return [{"station_name": "F9-1"}, {"station_name": "F9-2"}]
        if "distinct w.data_time" in low:
            return [{"data_time": "2025-11-30 23:00:00"}]
        if "w.data_time = any(:times)" in low:
            return [
                {"data_time": "2025-11-30 23:00:00", "station_name": "F9-1", "v": 0.0},
                {"data_time": "2025-11-30 23:00:00", "station_name": "F9-2", "v": 1.0},
            ]
        return []


@pytest.mark.asyncio
async def test_realtime_station_click_flags_calibrated_supported() -> None:
    exe = _RealtimeExecutor()
    payload = await fetch_browse_list(
        library_id="fcb",
        station_id="F9(后芮营)",
        page=1,
        page_size=20,
        executor=exe,
    )
    assert payload["data_source"] == "realtime"
    assert payload["calibrated_supported"] is True
    assert payload["result_grain"] == "device_time"
    assert any("t_data_wash_fcb" in sql.lower() for sql, _ in exe.calls)


@pytest.mark.asyncio
async def test_browse_response_model_keeps_calibrated_fields() -> None:
    payload = await fetch_browse_list(
        library_id="fcb",
        station_id="F1(王四营)",
        data_source="calibrated",
        executor=_CalibExecutor(),
    )
    resp = DataQueryAgentBrowseResponse(**payload)
    assert resp.data_source == "calibrated"
    dumped = resp.model_dump(by_alias=True)
    assert dumped["list"][0]["F1-7"] == pytest.approx(-616.584)
