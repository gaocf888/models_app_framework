"""浏览路径 GET /tree、GET /browse：mock SQLExecutor，不连 PG。"""

from __future__ import annotations

import pytest

from app.core.config import get_app_config
from app.data_query_agent.browse import (
    BrowseRequestError,
    build_annual_sql,
    build_latest_sql,
    fetch_browse_list,
    fetch_perception_tree,
    normalize_page,
    resolve_browse_library,
)
from app.data_query_agent.catalog import get_library_catalog
from app.models.data_query_agent import DataQueryAgentBrowseResponse
from app.services.data_query_agent_service import DataQueryAgentService


class _FakeExecutor:
    def __init__(self, pages: list[list[dict]] | None = None, total: int = 2) -> None:
        self.calls: list[tuple[str, dict]] = []
        self._pages = pages or [
            [
                {
                    "station_id": "F28(金盏)",
                    "station_name": "F28(金盏)",
                    "area": "朝阳区",
                    "total_settle_mm": -84.2,
                    "total_settle": -84.2,
                    "data_time": "2026-08-01",
                },
                {
                    "station_id": "F2(望京)",
                    "station_name": "F2(望京)",
                    "area": "朝阳区",
                    "total_settle_mm": -76.5,
                    "total_settle": -76.5,
                    "data_time": "2026-08-01",
                },
            ]
        ]
        self._total = total
        self._idx = 0

    async def execute(self, sql: str, params: dict | None = None) -> list[dict]:
        self.calls.append((sql, dict(params or {})))
        low = sql.lower()
        if "count(*)" in low:
            return [{"total": self._total}]
        if "w.project_name = any(:names)" in low:
            return [
                {"station_id": "F28(金盏)", "t": "2025-08-01", "v": -70.0},
                {"station_id": "F28(金盏)", "t": "2026-08-01", "v": -84.2},
            ]
        rows = self._pages[min(self._idx, len(self._pages) - 1)]
        self._idx += 1
        return rows


@pytest.fixture(autouse=True)
def _mem_backend(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATA_QUERY_AGENT_CHECKPOINT_BACKEND", "memory")
    monkeypatch.setenv("DATA_QUERY_AGENT_SESSION_STORE_BACKEND", "memory")
    monkeypatch.setenv("DATA_QUERY_AGENT_TRACE_BACKEND", "memory")
    get_app_config.cache_clear()
    yield
    get_app_config.cache_clear()


def test_resolve_default_library_is_fcb() -> None:
    lib = resolve_browse_library(None, required=False)
    assert lib.id == "fcb"
    assert lib.table == "t_data_wash_fcb"
    with pytest.raises(BrowseRequestError) as exc:
        resolve_browse_library("not-a-lib", required=False)
    assert exc.value.status_code == 404


def test_normalize_page() -> None:
    assert normalize_page(1, 20) == (1, 20, 0)
    assert normalize_page(3, 10) == (3, 10, 20)
    with pytest.raises(BrowseRequestError):
        normalize_page(0, 20)
    with pytest.raises(BrowseRequestError):
        normalize_page(1, 0)
    with pytest.raises(BrowseRequestError):
        normalize_page(1, 101)


def test_latest_sql_projects_physical_not_display_alias() -> None:
    lib = get_library_catalog().get("fcb")
    assert lib is not None
    sql, _ = build_latest_sql(lib, limit=20, offset=0)
    assert "w.total_settle AS total_settle_mm" in sql
    assert "w.total_settle_mm AS" not in sql


def test_latest_sql_locks_one_wash_table_and_paginates() -> None:
    lib = get_library_catalog().get("fcb")
    assert lib is not None
    sql, params = build_latest_sql(lib, district="朝阳区", limit=20, offset=20)
    low = sql.lower()
    assert "t_data_wash_fcb" in low
    assert "t_data_wash_jyb" not in low
    assert "distinct on (w.project_name)" in low
    assert "left join t_station" in low
    assert "s.area = :district" in low
    assert "limit :limit offset :offset" in low
    assert params["district"] == "朝阳区"
    assert params["limit"] == 20
    assert params["offset"] == 20
    count_sql, count_params = build_latest_sql(lib, district="朝阳区", count_only=True)
    assert "count(*)" in count_sql.lower()
    assert count_params["district"] == "朝阳区"


def test_latest_sql_station_binds_project_name() -> None:
    lib = get_library_catalog().get("jyb")
    assert lib is not None
    sql, params = build_latest_sql(lib, station_id="F28(金盏)", limit=20, offset=0)
    assert "w.project_name = :station_id" in sql
    assert params["station_id"] == "F28(金盏)"
    assert "t_data_wash_jyb" in sql.lower()
    assert "t_data_wash_fcb" not in sql.lower()


def test_annual_sql_uses_any_names() -> None:
    lib = get_library_catalog().get("fcb")
    assert lib is not None
    sql = build_annual_sql(lib)
    assert "t_data_wash_fcb" in sql
    assert "ANY(:names)" in sql
    assert "total_settle" in sql


@pytest.mark.asyncio
async def test_browse_list_default_fcb_paginated() -> None:
    exe = _FakeExecutor(total=42)
    payload = await fetch_browse_list(page=1, page_size=20, executor=exe)
    assert payload["library_id"] == "fcb"
    assert payload["page"] == 1
    assert payload["page_size"] == 20
    assert payload["total"] == 42
    assert payload["has_more"] is True
    assert payload["result_grain"] == "station"
    assert payload["list"][0]["station_id"] == "F28(金盏)"
    assert payload["list"][0]["hud_entity_id"] == "F28(金盏)"
    assert payload["list"][0]["hud_available"] is True
    assert payload["list"][0]["annual_settle_mm"] == pytest.approx(-14.2)
    assert any(c["key"] == "total_settle_mm" for c in payload["columns"])
    assert all("t_data_wash_jyb" not in sql.lower() for sql, _ in exe.calls)


@pytest.mark.asyncio
async def test_browse_list_district_and_station_filters() -> None:
    exe = _FakeExecutor(total=1)
    payload = await fetch_browse_list(
        library_id="jyb",
        district="朝阳区",
        station_id="F28(金盏)",
        page=1,
        page_size=20,
        executor=exe,
    )
    assert payload["library_id"] == "jyb"
    assert payload["district"] == "朝阳区"
    assert payload["station_id"] == "F28(金盏)"
    assert payload["has_more"] is False
    list_sql = next(sql for sql, _ in exe.calls if "distinct on" in sql.lower())
    assert ":district" in list_sql
    assert ":station_id" in list_sql


@pytest.mark.asyncio
async def test_tree_groups_districts_and_counts() -> None:
    exe = _FakeExecutor()
    payload = await fetch_perception_tree(library_id="jyb", executor=exe)
    assert payload["default_library_id"] == "fcb"
    assert len(payload["libraries"]) == 1
    node = payload["libraries"][0]
    assert node["library_id"] == "jyb"
    assert node["station_count"] == 2
    assert node["districts"][0]["district"] == "朝阳区"
    assert node["districts"][0]["station_count"] == 2
    by_id = {s["station_id"]: s for s in node["districts"][0]["stations"]}
    assert by_id["F28(金盏)"]["latest_value"] == pytest.approx(-84.2)
    sql, _ = exe.calls[0]
    assert "t_data_wash_jyb" in sql.lower()
    assert "limit" not in sql.lower()


@pytest.mark.asyncio
async def test_tree_all_libraries_seven_queries() -> None:
    exe = _FakeExecutor()
    payload = await fetch_perception_tree(executor=exe)
    ids = [x["library_id"] for x in payload["libraries"]]
    assert ids == ["fcb", "jyb", "dxswj", "kxsylj", "gnss", "qxz", "gq"]
    assert len(exe.calls) == 7


@pytest.mark.asyncio
async def test_service_browse_and_tree(monkeypatch: pytest.MonkeyPatch) -> None:
    exe = _FakeExecutor(total=2)
    real_tree = fetch_perception_tree
    real_list = fetch_browse_list

    async def _tree(*, library_id=None, executor=None):
        return await real_tree(library_id=library_id, executor=exe)

    async def _list(**kwargs):
        kwargs["executor"] = exe
        return await real_list(**kwargs)

    monkeypatch.setattr("app.data_query_agent.browse.fetch_perception_tree", _tree)
    monkeypatch.setattr("app.data_query_agent.browse.fetch_browse_list", _list)
    svc = DataQueryAgentService()
    tree = await svc.get_tree(library_id="fcb")
    listed = await svc.browse_list(library_id="fcb", page=1, page_size=20)
    assert tree.libraries[0].library_id == "fcb"
    assert listed.ok is True
    assert listed.library_id == "fcb"
    assert listed.total == 2


def test_browse_response_json_uses_list_alias() -> None:
    body = DataQueryAgentBrowseResponse(
        library_id="fcb",
        page=1,
        page_size=20,
        total=1,
        list=[{"station_id": "F8(周村)"}],
    )
    dumped = body.model_dump(by_alias=True)
    assert "list" in dumped
    assert dumped["list"][0]["station_id"] == "F8(周村)"


@pytest.mark.asyncio
async def test_unknown_library_tree() -> None:
    with pytest.raises(BrowseRequestError) as exc:
        await fetch_perception_tree(library_id="nope", executor=_FakeExecutor())
    assert exc.value.status_code == 404
