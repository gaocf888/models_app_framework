"""数据查询智能体 sequential runner：SSE 事件字典生成器。"""

from __future__ import annotations

import time
import uuid
from collections.abc import AsyncIterator
from datetime import datetime, timezone
from typing import Any

from app.core.config import get_app_config
from app.core.logging import get_logger
from app.core.metrics import DATA_QUERY_AGENT_LIBRARY_HITL_TOTAL, DATA_QUERY_AGENT_RUNS_TOTAL
from app.data_query_agent.acquire import acquire_data
from app.data_query_agent.assemble import assemble_result
from app.data_query_agent.catalog import CatalogError, get_library_catalog
from app.data_query_agent.graph.state import DataQueryAgentState
from app.data_query_agent.hitl import (
    create_resume_token,
    delete_resume_session,
    get_resume_session,
    update_resume_session,
)
from app.data_query_agent.library_intent import resolve_library_intent
from app.data_query_agent.library_intent_llm import supplement_library_intent_llm
from app.data_query_agent.scope_intent import resolve_scope_intent
from app.models.data_query_agent import DataQueryAgentResumeRequest, DataQueryAgentRunRequest
from app.services.data_query_agent_stream_control import DataQueryAgentStreamControl
from app.services.data_query_agent_trace_store import save_data_query_agent_trace
from app.services.nl2sql_service import NL2SQLService

logger = get_logger(__name__)


def _event(name: str, request_id: str, **payload: Any) -> dict[str, Any]:
    out = {"event": name, "request_id": request_id}
    out.update(payload)
    return out


def _save_run_trace(state: DataQueryAgentState, *, status: str, extra: dict[str, Any] | None = None) -> None:
    now = datetime.now(timezone.utc).isoformat()
    rec: dict[str, Any] = {
        "request_id": state.request_id,
        "user_id": state.user_id,
        "session_id": state.session_id,
        "query": state.query,
        "library_id": state.library_id or (state.library.id if state.library else ""),
        "table": state.library.table if state.library else "",
        "source": state.library_source,
        "status": status,
        "hitl": state.hitl,
        "warnings": list(state.warnings),
        "district": state.district,
        "station_id": state.station_id,
        "started_at": datetime.fromtimestamp(state.started_at, tz=timezone.utc).isoformat()
        if state.started_at
        else now,
        "finished_at": now,
    }
    if extra:
        rec.update(extra)
    save_data_query_agent_trace(rec)


class DataQueryAgentGraphRunner:
    """P0 sequential 主图：意图1 → HITL? → 意图2 → 锁表 NL2SQL → assemble → SSE。"""
    def __init__(
        self,
        *,
        stream_control: DataQueryAgentStreamControl | None = None,
        nl2sql: NL2SQLService | None = None,
    ) -> None:
        self._stream_ctrl = stream_control or DataQueryAgentStreamControl()
        self._nl2sql = nl2sql
        self._cfg = get_app_config().data_query_agent

    @property
    def _nl2sql_or_create(self) -> NL2SQLService:
        if self._nl2sql is None:
            self._nl2sql = NL2SQLService()
        return self._nl2sql

    async def run_stream(self, req: DataQueryAgentRunRequest) -> AsyncIterator[dict[str, Any]]:
        """主入口：新 request_id；未锁库则本轮只推 HITL 后断流。"""
        request_id = uuid.uuid4().hex
        stream_id = self._stream_ctrl.begin_stream(req.user_id, req.session_id)
        async for ev in self._run(
            state=DataQueryAgentState(
                request_id=request_id,
                stream_id=stream_id,
                user_id=req.user_id,
                session_id=req.session_id,
                query=req.query,
                library_id=req.library_id,
                include_hud=req.options.include_hud,
                expose_sql=req.options.expose_sql,
                max_rows=int(req.options.max_rows or self._cfg.default_max_rows),
                district=req.district,
                station_id=req.station_id,
                started_at=time.time(),
            ),
            emit_started=True,
        ):
            yield ev

    async def resume_stream(self, req: DataQueryAgentResumeRequest) -> AsyncIterator[dict[str, Any]]:
        """选库后续流：合法 library_id 跳过意图1；非法再 interrupt（上限 hitl_max_retries）。"""
        session = get_resume_session(req.resume_token)
        if session is None:
            yield _event(
                "data_query_error",
                req.resume_token,
                error="invalid_resume_token",
                message="resume_token 无效或已过期",
            )
            yield _event("finished", req.resume_token, status="error", trace_id=req.resume_token)
            return
        if session.user_id != req.user_id or session.session_id != req.session_id:
            yield _event(
                "data_query_error",
                session.request_id,
                error="resume_identity_mismatch",
                message="user_id/session_id 与 resume 会话不匹配",
            )
            yield _event("finished", session.request_id, status="error")
            return

        stream_id = self._stream_ctrl.begin_stream(req.user_id, req.session_id)
        opts = session.options or {}
        # 用户放弃选库：不进 NL2SQL，与 stop 一样 cancelled。
        if req.abort:
            delete_resume_session(req.resume_token)
            yield _event("started", session.request_id, stream_id=stream_id)
            yield _event("data_query_cancelled", session.request_id, reason="abort")
            yield _event("finished", session.request_id, status="cancelled", stream_id=stream_id)
            DATA_QUERY_AGENT_RUNS_TOTAL.labels(status="cancelled", hitl="true").inc()
            _save_run_trace(
                DataQueryAgentState(
                    request_id=session.request_id,
                    stream_id=stream_id,
                    user_id=req.user_id,
                    session_id=req.session_id,
                    query=session.query,
                    started_at=time.time(),
                ),
                status="cancelled",
                extra={"reason": "abort"},
            )
            await self._stream_ctrl.clear_stream(req.user_id, req.session_id, stream_id)
            return

        catalog = get_library_catalog()
        lib = catalog.get(req.library_id)
        if lib is None:
            session.hitl_attempts = int(session.hitl_attempts or 0) + 1
            max_n = int(self._cfg.hitl_max_retries)
            if session.hitl_attempts >= max_n:
                delete_resume_session(req.resume_token)
                yield _event("started", session.request_id, stream_id=stream_id)
                yield _event(
                    "data_query_error",
                    session.request_id,
                    error="hitl_retry_exhausted",
                    message="选库次数已达上限",
                )
                yield _event("finished", session.request_id, status="error", stream_id=stream_id)
                DATA_QUERY_AGENT_RUNS_TOTAL.labels(status="error", hitl="true").inc()
                await self._stream_ctrl.clear_stream(req.user_id, req.session_id, stream_id)
                return
            update_resume_session(session)
            yield _event("started", session.request_id, stream_id=stream_id)
            yield _event(
                "data_query_library_input_required",
                session.request_id,
                resume_token=req.resume_token,
                interrupt_reason="library_id_invalid",
                prompt=catalog.hitl_prompt,
                candidates=session.candidates,
                library_options=catalog.library_options(candidates=session.candidates),
                query=session.query,
                suggested_actions=["select_library", "abort"],
            )
            DATA_QUERY_AGENT_LIBRARY_HITL_TOTAL.labels(reason="library_id_invalid").inc()
            await self._stream_ctrl.clear_stream(req.user_id, req.session_id, stream_id)
            return

        delete_resume_session(req.resume_token)
        state = DataQueryAgentState(
            request_id=session.request_id,
            stream_id=stream_id,
            user_id=req.user_id,
            session_id=req.session_id,
            query=session.query,
            library_id=lib.id,
            include_hud=bool(opts.get("include_hud", True)),
            expose_sql=bool(opts.get("expose_sql", False)),
            max_rows=int(opts.get("max_rows") or self._cfg.default_max_rows),
            district=(opts.get("district") or None),
            station_id=(opts.get("station_id") or None),
            library=lib,
            library_source="hitl",
            hitl=True,
            started_at=time.time(),
        )
        async for ev in self._run(state=state, emit_started=True, skip_library_intent=True):
            yield ev

    async def _cancelled(self, state: DataQueryAgentState) -> bool:
        return await self._stream_ctrl.is_cancelled(state.user_id, state.session_id, state.stream_id)

    async def _run(
        self,
        *,
        state: DataQueryAgentState,
        emit_started: bool,
        skip_library_intent: bool = False,
    ) -> AsyncIterator[dict[str, Any]]:
        hitl_used = "true" if state.hitl else "false"
        try:
            catalog = get_library_catalog()
        except CatalogError as exc:
            if emit_started:
                yield _event("started", state.request_id, stream_id=state.stream_id)
            yield _event("data_query_error", state.request_id, error="catalog_invalid", message=str(exc))
            yield _event("finished", state.request_id, status="error", stream_id=state.stream_id)
            DATA_QUERY_AGENT_RUNS_TOTAL.labels(status="error", hitl=hitl_used).inc()
            await self._stream_ctrl.clear_stream(state.user_id, state.session_id, state.stream_id)
            return

        if emit_started:
            yield _event("started", state.request_id, stream_id=state.stream_id)

        if await self._cancelled(state):
            yield _event("data_query_cancelled", state.request_id, reason="user_cancelled")
            yield _event("finished", state.request_id, status="cancelled", stream_id=state.stream_id)
            DATA_QUERY_AGENT_RUNS_TOTAL.labels(status="cancelled", hitl=hitl_used).inc()
            await self._stream_ctrl.clear_stream(state.user_id, state.session_id, state.stream_id)
            return

        # resume 已带合法库时跳过意图1，直接 library_hit。
        if not skip_library_intent:
            intent = resolve_library_intent(state.query, state.library_id, catalog=catalog)
            state.warnings.extend(intent.warnings)
            if (not intent.ok or intent.library is None) and intent.interrupt_reason == "library_unresolved":
                llm_hit = await supplement_library_intent_llm(state.query, catalog)
                if llm_hit is not None and llm_hit.library is not None:
                    intent = llm_hit
            if not intent.ok or intent.library is None:
                # 未锁库：interrupt + 断流，前端选库后 POST resume-stream。
                reason = intent.interrupt_reason or "library_unresolved"
                token = create_resume_token(
                    request_id=state.request_id,
                    user_id=state.user_id,
                    session_id=state.session_id,
                    query=state.query,
                    interrupt_reason=reason,
                    candidates=intent.candidates,
                    options={
                        "include_hud": state.include_hud,
                        "expose_sql": state.expose_sql,
                        "max_rows": state.max_rows,
                        "district": state.district,
                        "station_id": state.station_id,
                    },
                    library_id=state.library_id,
                    hitl_attempts=0,
                )
                yield _event(
                    "data_query_library_input_required",
                    state.request_id,
                    resume_token=token,
                    interrupt_reason=reason,
                    prompt=catalog.hitl_prompt,
                    candidates=intent.candidates,
                    library_options=catalog.library_options(candidates=intent.candidates),
                    query=state.query,
                    suggested_actions=["select_library", "abort"],
                )
                DATA_QUERY_AGENT_LIBRARY_HITL_TOTAL.labels(reason=reason).inc()
                DATA_QUERY_AGENT_RUNS_TOTAL.labels(status="interrupted", hitl="true").inc()
                logger.info(
                    "data_query_agent HITL request_id=%s reason=%s checkpoint=on token_prefix=%s",
                    state.request_id,
                    reason,
                    token[:8],
                )
                _save_run_trace(state, status="interrupted", extra={"interrupt_reason": reason})
                await self._stream_ctrl.clear_stream(state.user_id, state.session_id, state.stream_id)
                return
            state.library = intent.library
            state.library_source = intent.source
            state.library_id = intent.library.id

        assert state.library is not None
        yield _event(
            "data_query_library_hit",
            state.request_id,
            library_id=state.library.id,
            display_name=state.library.display_name,
            table=state.library.table,
            source=state.library_source,
            warnings=list(state.warnings),
        )
        logger.info(
            "data_query_agent library_hit request_id=%s library=%s table=%s source=%s",
            state.request_id,
            state.library.id,
            state.library.table,
            state.library_source,
        )

        if await self._cancelled(state):
            yield _event("data_query_cancelled", state.request_id, reason="user_cancelled")
            yield _event("finished", state.request_id, status="cancelled", stream_id=state.stream_id)
            DATA_QUERY_AGENT_RUNS_TOTAL.labels(status="cancelled", hitl=hitl_used).inc()
            await self._stream_ctrl.clear_stream(state.user_id, state.session_id, state.stream_id)
            return

        # 意图 2 不改写 library；device_type 只来自已锁定的库。
        scope = resolve_scope_intent(
            state.query,
            state.library,
            district=state.district,
            station_id=state.station_id,
        )
        state.scope = scope
        yield _event(
            "data_query_scope_parsed",
            state.request_id,
            library_id=state.library.id,
            table=state.library.table,
            scope=scope.scope_snapshot,
            time=scope.time_snapshot,
            parse_mode=scope.parse_mode,
            result_grain=scope.grain,
        )
        cs = scope.confirmed_scope or {}
        tw = scope.time_snapshot or {}
        logger.info(
            "data_query_agent scope_parsed request_id=%s library=%s table=%s "
            "district=%s station_id=%s station_name=%s time_tag=%s annual_source=%s grain=%s",
            state.request_id,
            state.library.id,
            state.library.table,
            cs.get("district") or "",
            cs.get("station_id") or "",
            cs.get("station_name") or "",
            tw.get("time_window_tag") or "",
            (scope.annual_window or {}).get("source") or "",
            scope.grain,
        )

        if await self._cancelled(state):
            yield _event("data_query_cancelled", state.request_id, reason="user_cancelled")
            yield _event("finished", state.request_id, status="cancelled", stream_id=state.stream_id)
            DATA_QUERY_AGENT_RUNS_TOTAL.labels(status="cancelled", hitl=hitl_used).inc()
            await self._stream_ctrl.clear_stream(state.user_id, state.session_id, state.stream_id)
            return

        async def _cancelled_check() -> bool:
            return await self._cancelled(state)

        hud_plan = "skipped"
        city_ok = bool(getattr(self._cfg, "hud_city_enabled", True))
        if state.include_hud and state.library.hud_supported and scope.grain in (
            {"station", "station_series", "district"} | ({"city"} if city_ok else set())
        ):
            hud_plan = "running"
        yield _event(
            "data_query_nl2sql_progress",
            state.request_id,
            q_list="running",
            q_hud_series=hud_plan,
        )

        # 锁表 NL2SQL：q_list 必跑；HUD 时并行 q_hud_series。
        acquire = await acquire_data(
            nl2sql=self._nl2sql_or_create,
            user_id=state.user_id,
            session_id=state.session_id,
            request_id=state.request_id,
            query=state.query,
            library=state.library,
            scope=scope,
            include_hud=state.include_hud,
            max_rows=state.max_rows,
            cancelled_check=_cancelled_check,
        )
        if acquire.error == "cancelled" or await self._cancelled(state):
            yield _event("data_query_cancelled", state.request_id, reason="user_cancelled")
            yield _event("finished", state.request_id, status="cancelled", stream_id=state.stream_id)
            DATA_QUERY_AGENT_RUNS_TOTAL.labels(status="cancelled", hitl=hitl_used).inc()
            await self._stream_ctrl.clear_stream(state.user_id, state.session_id, state.stream_id)
            return

        yield _event(
            "data_query_nl2sql_progress",
            state.request_id,
            q_list="done" if acquire.list_item.ok else "failed",
            q_hud_series=(
                "skipped"
                if acquire.series_item is None
                else ("done" if acquire.series_item.ok else "failed")
            ),
        )

        if not acquire.ok:
            # 取数失败不再为库 HITL；前端展示 error 即可。
            yield _event(
                "data_query_error",
                state.request_id,
                error=acquire.error or "nl2sql_failed",
                message=acquire.list_item.error or acquire.error,
                sql=acquire.list_item.sql or None,
                gen_fail_reason=acquire.list_item.gen_fail_reason,
            )
            yield _event("finished", state.request_id, status="error", stream_id=state.stream_id, trace_id=state.request_id)
            DATA_QUERY_AGENT_RUNS_TOTAL.labels(status="error", hitl=hitl_used).inc()
            logger.warning(
                "data_query_agent nl2sql error request_id=%s library=%s err=%s",
                state.request_id,
                state.library.id,
                acquire.error,
            )
            _save_run_trace(
                state,
                status="error",
                extra={"error": acquire.error, "sql": acquire.list_item.sql},
            )
            await self._stream_ctrl.clear_stream(state.user_id, state.session_id, state.stream_id)
            return

        payload = assemble_result(
            library=state.library,
            scope=scope,
            acquire=acquire,
            include_hud=state.include_hud,
            expose_sql=state.expose_sql,
            extra_warnings=list(state.warnings),
        )
        result_event = _event("data_query_result", state.request_id, **payload)
        yield result_event
        yield _event(
            "finished",
            state.request_id,
            status="success",
            stream_id=state.stream_id,
            library_id=state.library.id,
            result_grain=payload.get("result_grain"),
            hud_enabled=payload.get("hud_enabled"),
            trace_id=state.request_id,
        )
        DATA_QUERY_AGENT_RUNS_TOTAL.labels(status="success", hitl=hitl_used).inc()
        logger.info(
            "data_query_agent finished request_id=%s library=%s grain=%s hud=%s rows=%d",
            state.request_id,
            state.library.id,
            payload.get("result_grain"),
            payload.get("hud_enabled"),
            len(payload.get("list") or []),
        )
        _save_run_trace(
            state,
            status="success",
            extra={
                "result_grain": payload.get("result_grain"),
                "hud_enabled": payload.get("hud_enabled"),
                "row_count": len(payload.get("list") or []),
                "warnings": payload.get("warnings") or state.warnings,
                "sql": payload.get("sql"),
            },
        )
        await self._stream_ctrl.clear_stream(state.user_id, state.session_id, state.stream_id)
