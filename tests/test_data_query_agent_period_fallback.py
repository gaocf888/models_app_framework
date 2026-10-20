"""period 确定性失败后应回退 LLM，而不是直接终态失败。"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.data_query_agent.acquire import _query_one
from app.data_query_agent.catalog import get_library_catalog
from app.data_query_agent.scope_intent import resolve_scope_intent
from app.models.nl2sql import NL2SQLQueryResponse


@pytest.mark.asyncio
async def test_period_list_failure_falls_back_to_llm() -> None:
    lib = get_library_catalog().get("fcb")
    assert lib is not None
    scope = resolve_scope_intent("请帮我查询F2(望京)本月的沉降量", lib)
    assert scope.grain == "station"

    nl2sql = MagicMock()
    nl2sql.query = AsyncMock(
        return_value=NL2SQLQueryResponse(
            sql="SELECT 1 FROM t_data_wash_fcb",
            rows=[{"station_name": "F2(望京)", "total_settle": 1.0}],
        )
    )

    with patch(
        "app.data_query_agent.period_sql.fetch_period_list",
        new=AsyncMock(side_effect=RuntimeError("period_boom")),
    ):
        item = await _query_one(
            nl2sql=nl2sql,
            user_id="u1",
            session_id="s1",
            request_id="r1",
            query="请帮我查询F2(望京)本月的沉降量",
            library=lib,
            scope=scope,
            plan_item_id="q_list",
            grain="station",
            max_rows=50,
        )

    assert item.ok is True
    assert nl2sql.query.await_count == 1
    assert "period_list_failed" not in (item.error or "")
    assert "t_data_wash_fcb" in (item.sql or "").lower()
