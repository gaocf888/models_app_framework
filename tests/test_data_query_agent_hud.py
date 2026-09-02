"""P2-2 GET /hud：单实体补拉，mock NL2SQL，不连 PG。"""

from __future__ import annotations

import pytest

from app.core.config import get_app_config
from app.data_query_agent.acquire import AcquireItemResult, AcquireResult
from app.data_query_agent.assemble import assemble_entity_hud
from app.data_query_agent.catalog import get_library_catalog
from app.data_query_agent.graph.runner import DataQueryAgentGraphRunner
from app.data_query_agent.hud import HudRequestError, fetch_entity_hud, normalize_hud_entity
from app.models.nl2sql import NL2SQLQueryResponse
from app.services.data_query_agent_service import DataQueryAgentService
from app.services.data_query_agent_stream_control import DataQueryAgentStreamControl


class _FakeNL2SQL:
    def __init__(self) -> None:
        self.calls: list = []

    async def query(self, req, record_conversation: bool = True, include_parsed_intent=None):
        self.calls.append(req)
        table = (req.forced_tables or ["t_data_wash_fcb"])[0]
        assert table == "t_data_wash_fcb"
        cs = req.confirmed_scope or {}
        sid = str(cs.get("station_id") or "JZGZ")
        area = str(cs.get("district") or "朝阳区")
        grain = "station"
        if not cs.get("station_id") and cs.get("district"):
            grain = "district"
        elif not cs.get("station_id") and not cs.get("district"):
            q = req.question or req.original_query or ""
            if "全市" in q:
                grain = "city"
        if req.plan_item_id == "q_hud_series":
            if grain == "city":
                return NL2SQLQueryResponse(
                    sql=f"SELECT data_time, total_settle FROM {table}",
                    rows=[{"data_time": "2026-01-01", "total_settle": -8.0}],
                )
            if grain == "district":
                return NL2SQLQueryResponse(
                    sql=f"SELECT area, data_time, total_settle FROM {table}",
                    rows=[
                        {"area": area, "data_time": "2025-01-01", "total_settle": -10.0},
                        {"area": area, "data_time": "2026-01-01", "total_settle": -12.0},
                    ],
                )
            return NL2SQLQueryResponse(
                sql=f"SELECT station_id, data_time, total_settle FROM {table}",
                rows=[
                    {"station_id": sid, "data_time": "2020-01-01", "total_settle": -10.2},
                    {"station_id": sid, "data_time": "2021-01-01", "total_settle": -90.0},
                ],
            )
        if grain == "city":
            return NL2SQLQueryResponse(
                sql=f"SELECT AVG(total_settle) FROM {table}",
                rows=[{"total_settle": -8.0, "station_count": 20}],
            )
        if grain == "district":
            return NL2SQLQueryResponse(
                sql=f"SELECT area FROM {table}",
                rows=[{"area": area, "total_settle": -12.0, "station_count": 3}],
            )
        return NL2SQLQueryResponse(
            sql=f"SELECT station_id FROM {table} JOIN t_station",
            rows=[
                {
                    "station_id": sid,
                    "station_name": "金盏公交",
                    "area": "朝阳区",
                    "total_settle": -418.5,
                    "data_time": "2026-08-01",
                }
            ],
        )


@pytest.fixture(autouse=True)
def _mem_backend(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATA_QUERY_AGENT_CHECKPOINT_BACKEND", "memory")
    monkeypatch.setenv("DATA_QUERY_AGENT_SESSION_STORE_BACKEND", "memory")
    monkeypatch.setenv("DATA_QUERY_AGENT_TRACE_BACKEND", "memory")
    monkeypatch.setenv("DATA_QUERY_AGENT_LIBRARY_LLM_ENABLED", "false")
    get_app_config.cache_clear()
    yield
    get_app_config.cache_clear()


def test_normalize_hud_entity() -> None:
    assert normalize_hud_entity("station", "JZGZ") == ("station", "JZGZ")
    assert normalize_hud_entity("CITY", "beijing") == ("city", "beijing")
    assert normalize_hud_entity("city", "全市") == ("city", "beijing")
    with pytest.raises(HudRequestError) as exc:
        normalize_hud_entity("layer", "x:1")
    assert exc.value.code == "hud_layer_not_enabled"
    with pytest.raises(HudRequestError) as exc:
        normalize_hud_entity("pipe", "x")
    assert exc.value.status_code == 422
    with pytest.raises(HudRequestError) as exc:
        normalize_hud_entity("city", "shanghai")
    assert exc.value.code == "invalid_city_entity_id"


def test_assemble_entity_hud_station_shape() -> None:
    lib = get_library_catalog().get("fcb")
    assert lib is not None
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
                }
            ],
        ),
        series_item=AcquireItemResult(
            plan_item_id="q_hud_series",
            ok=True,
            sql="SELECT station_id, data_time, total_settle FROM t_data_wash_fcb",
            rows=[
                {"station_id": "JZGZ", "data_time": "2020-01-01", "total_settle": -10.2},
            ],
        ),
    )
    payload = assemble_entity_hud(
        library=lib,
        grain="station",
        entity_id="JZGZ",
        acquire=acquire,
        expose_sql=True,
    )
    assert payload["ok"] is True
    assert payload["found"] is True
    hud = payload["hud"]
    assert hud["entity_type"] == "station"
    assert hud["entity_id"] == "JZGZ"
    assert hud["library_id"] == "fcb"
    assert hud["series"]["agg"] is None
    assert len(hud["series"]["points"]) == 1
    assert hud["blocks"]["geology"]["status"] == "unavailable"
    assert payload["sql"]["q_list"]


@pytest.mark.asyncio
async def test_fetch_hud_station_forced_tables() -> None:
    fake = _FakeNL2SQL()
    payload = await fetch_entity_hud(
        nl2sql=fake,
        library_id="fcb",
        entity_type="station",
        entity_id="JZGZ",
        expose_sql=True,
    )
    assert payload["entity_type"] == "station"
    assert payload["hud"]["entity_id"] == "JZGZ"
    assert payload["hud"]["series"]["points"]
    assert all(c.forced_tables == ["t_data_wash_fcb"] for c in fake.calls)
    assert {c.plan_item_id for c in fake.calls} == {"q_list", "q_hud_series"}


@pytest.mark.asyncio
async def test_fetch_hud_district_avg_not_station() -> None:
    fake = _FakeNL2SQL()
    payload = await fetch_entity_hud(
        nl2sql=fake,
        library_id="fcb",
        entity_type="district",
        entity_id="朝阳区",
    )
    hud = payload["hud"]
    assert hud["entity_type"] == "district"
    assert hud["entity_id"] == "朝阳区"
    assert hud["series"]["agg"] == "avg"
    assert "JZGZ" not in (hud.get("identity") or {})
    assert len(hud["series"]["points"]) == 2


@pytest.mark.asyncio
async def test_fetch_hud_city_beijing() -> None:
    fake = _FakeNL2SQL()
    payload = await fetch_entity_hud(
        nl2sql=fake,
        library_id="fcb",
        entity_type="city",
        entity_id="全市",
    )
    assert payload["entity_id"] == "beijing"
    assert payload["hud"]["entity_type"] == "city"
    assert payload["hud"]["series"]["agg"] == "avg"


@pytest.mark.asyncio
async def test_fetch_hud_unknown_library() -> None:
    with pytest.raises(HudRequestError) as exc:
        await fetch_entity_hud(
            nl2sql=_FakeNL2SQL(),
            library_id="not-a-lib",
            entity_type="station",
            entity_id="JZGZ",
        )
    assert exc.value.status_code == 404


@pytest.mark.asyncio
async def test_service_get_hud() -> None:
    fake = _FakeNL2SQL()
    svc = DataQueryAgentService(
        runner=DataQueryAgentGraphRunner(nl2sql=fake, stream_control=DataQueryAgentStreamControl())
    )
    resp = await svc.get_hud(library_id="fcb", entity_type="station", entity_id="JZGZ")
    assert resp.ok is True
    assert resp.hud["library_id"] == "fcb"
    assert resp.hud["entity_id"] == "JZGZ"
