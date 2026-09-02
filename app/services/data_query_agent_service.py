from __future__ import annotations

"""数据查询智能体 HTTP 门面：SSE 编码，不承载业务编排。"""

import json
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from fastapi.responses import StreamingResponse

from app.core.logging import get_logger
from app.data_query_agent.graph.runner import DataQueryAgentGraphRunner
from app.models.data_query_agent import (
    DataQueryAgentHudResponse,
    DataQueryAgentResumeRequest,
    DataQueryAgentRunRequest,
    DataQueryAgentStreamStopResponse,
    DataQueryAgentTraceListItem,
    DataQueryAgentTraceListResponse,
    DataQueryAgentTraceStatsResponse,
)
from app.services.data_query_agent_stream_control import DataQueryAgentStreamControl
from app.services.data_query_agent_trace_store import get_data_query_agent_trace_store

logger = get_logger(__name__)


def _sse_json_default(value: Any) -> Any:
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    return str(value)


def _encode_sse(event: dict[str, Any]) -> bytes:
    return f"data: {json.dumps(event, ensure_ascii=False, default=_sse_json_default)}\n\n".encode("utf-8")


class DataQueryAgentService:
    def __init__(
        self,
        runner: DataQueryAgentGraphRunner | None = None,
        stream_control: DataQueryAgentStreamControl | None = None,
    ) -> None:
        self._stream_ctrl = stream_control or DataQueryAgentStreamControl()
        # 懒加载 runner，避免 GET /libraries 时初始化 NL2SQL/RAG
        self._runner: DataQueryAgentGraphRunner | None = runner

    @property
    def _trace_store(self):
        return get_data_query_agent_trace_store()

    @property
    def _runner_or_create(self) -> DataQueryAgentGraphRunner:
        if self._runner is None:
            self._runner = DataQueryAgentGraphRunner(stream_control=self._stream_ctrl)
        return self._runner

    async def stop_stream(
        self, user_id: str, session_id: str, stream_id: str
    ) -> DataQueryAgentStreamStopResponse:
        await self._stream_ctrl.cancel_stream(user_id, session_id, stream_id)
        return DataQueryAgentStreamStopResponse(ok=True, stream_id=stream_id)

    async def run_stream(self, data: DataQueryAgentRunRequest) -> StreamingResponse:
        runner = self._runner_or_create

        async def gen():
            async for event in runner.run_stream(data):
                yield _encode_sse(event)

        return StreamingResponse(gen(), media_type="text/event-stream")

    async def resume_stream(self, data: DataQueryAgentResumeRequest) -> StreamingResponse:
        runner = self._runner_or_create

        async def gen():
            async for event in runner.resume_stream(data):
                yield _encode_sse(event)

        return StreamingResponse(gen(), media_type="text/event-stream")

    def get_trace(self, request_id: str) -> dict[str, Any] | None:
        return self._trace_store.get(request_id)

    def list_traces(
        self,
        *,
        limit: int,
        offset: int,
        library_id: str | None = None,
        user_id: str | None = None,
        request_id_like: str | None = None,
    ) -> DataQueryAgentTraceListResponse:
        rows, total = self._trace_store.list(
            limit=min(1000, max(limit + offset + 50, 50)),
            offset=0,
            library_id=library_id,
            user_id=user_id,
        )
        if request_id_like:
            rows = [x for x in rows if request_id_like in str(x.get("request_id") or "")]
            total = len(rows)
        page = rows[offset : offset + limit]
        items = [
            DataQueryAgentTraceListItem(
                request_id=str(x.get("request_id") or ""),
                library_id=str(x.get("library_id") or ""),
                status=str(x.get("status") or ""),
                user_id=str(x.get("user_id") or ""),
                result_grain=str(x.get("result_grain") or ""),
                hud_enabled=bool(x.get("hud_enabled")),
                created_at=str(x.get("finished_at") or x.get("started_at") or ""),
                warning_count=len(x.get("warnings") or []),
            )
            for x in page
        ]
        return DataQueryAgentTraceListResponse(
            ok=True, limit=limit, offset=offset, total=total, items=items
        )

    def get_trace_stats(
        self,
        *,
        library_id: str | None = None,
        user_id: str | None = None,
    ) -> DataQueryAgentTraceStatsResponse:
        rows, total = self._trace_store.list(
            limit=1000, offset=0, library_id=library_id, user_id=user_id
        )
        by_lib: dict[str, int] = {}
        by_status: dict[str, int] = {}
        warns: dict[str, int] = {}
        for x in rows:
            lid = str(x.get("library_id") or "")
            by_lib[lid] = by_lib.get(lid, 0) + 1
            st = str(x.get("status") or "success")
            by_status[st] = by_status.get(st, 0) + 1
            for w in x.get("warnings") or []:
                key = str(w)
                warns[key] = warns.get(key, 0) + 1
        return DataQueryAgentTraceStatsResponse(
            ok=True,
            total=total,
            by_library_id=by_lib,
            by_status=by_status,
            warnings=warns,
        )

    async def get_hud(
        self,
        *,
        library_id: str,
        entity_type: str,
        entity_id: str,
        user_id: str | None = None,
        session_id: str | None = None,
        expose_sql: bool = False,
    ) -> DataQueryAgentHudResponse:
        from app.data_query_agent.hud import fetch_entity_hud

        payload = await fetch_entity_hud(
            nl2sql=self._runner_or_create._nl2sql_or_create,
            library_id=library_id,
            entity_type=entity_type,
            entity_id=entity_id,
            user_id=user_id,
            session_id=session_id,
            expose_sql=expose_sql,
        )
        return DataQueryAgentHudResponse(**payload)
