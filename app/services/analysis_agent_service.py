from __future__ import annotations

import json
from datetime import date, datetime
from decimal import Decimal
from threading import Lock
from typing import Any

from fastapi.responses import StreamingResponse

from app.analysis_agent.graph.runner import AnalysisAgentGraphRunner
from app.core.config import get_app_config
from app.core.logging import get_logger
from app.models.analysis_agent import (
    AnalysisAgentResult,
    AnalysisAgentResumeRequest,
    AnalysisAgentRunRequest,
)

logger = get_logger(__name__)

_trace_lock = Lock()
_trace_store: dict[str, dict[str, Any]] = {}


def _sse_json_default(value: Any) -> Any:
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    return str(value)


def _encode_sse(event: dict[str, Any]) -> bytes:
    return f"data: {json.dumps(event, ensure_ascii=False, default=_sse_json_default)}\n\n".encode("utf-8")


class AnalysisAgentService:
    """综合分析智能体服务门面。"""

    def __init__(self, runner: AnalysisAgentGraphRunner | None = None) -> None:
        self._runner = runner or AnalysisAgentGraphRunner()
        self._cfg = get_app_config().analysis_agent

    def _save_trace(self, result: dict[str, Any]) -> None:
        rid = result.get("request_id")
        if not rid:
            return
        with _trace_lock:
            _trace_store[rid] = result
            if len(_trace_store) > self._cfg.trace_max_items:
                for key in list(_trace_store.keys())[: max(1, len(_trace_store) - self._cfg.trace_max_items)]:
                    _trace_store.pop(key, None)

    def get_trace(self, request_id: str) -> dict[str, Any] | None:
        with _trace_lock:
            return _trace_store.get(request_id)

    async def run_stream(self, data: AnalysisAgentRunRequest) -> StreamingResponse:
        opts = data.options.model_dump()

        async def on_complete(result: dict[str, Any]) -> None:
            self._save_trace(result)

        async def gen():
            async for ev in self._runner.iter_stream_events(
                user_id=data.user_id,
                session_id=data.session_id,
                analysis_type=data.analysis_type,
                query=data.query,
                options=opts,
                on_complete=on_complete,
            ):
                yield _encode_sse(ev)

        return StreamingResponse(
            gen(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )

    async def resume_stream(self, data: AnalysisAgentResumeRequest) -> StreamingResponse:
        async def on_complete(result: dict[str, Any]) -> None:
            self._save_trace(result)

        async def gen():
            async for ev in self._runner.iter_resume_stream_events(
                resume_token=data.resume_token,
                user_id=data.user_id,
                session_id=data.session_id,
                action=data.action,
                payload=data.payload,
                on_complete=on_complete,
            ):
                yield _encode_sse(ev)

        return StreamingResponse(
            gen(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )

    async def resume(self, data: AnalysisAgentResumeRequest) -> AnalysisAgentResult:
        result = await self._runner.resume_to_result(
            resume_token=data.resume_token,
            user_id=data.user_id,
            session_id=data.session_id,
            action=data.action,
            payload=data.payload,
        )
        self._save_trace(result)
        return AnalysisAgentResult(
            request_id=str(result.get("request_id") or ""),
            analysis_type=str(result.get("analysis_type") or ""),
            summary=str(result.get("summary") or ""),
            structured_report=result.get("structured_report") or {},
            evidence=result.get("evidence") or {},
            trace=result.get("trace") or {},
        )
