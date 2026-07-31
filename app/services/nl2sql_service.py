from __future__ import annotations

import os
from time import perf_counter

from app.conversation.manager import ConversationManager
from app.core.logging import get_logger
from app.core.metrics import NL2SQL_QUERY_COUNT, NL2SQL_QUERY_ERROR_COUNT
from app.models.nl2sql import NL2SQLQueryRequest, NL2SQLQueryResponse
from app.nl2sql.chain import NL2SQLChain
from app.nl2sql.errors import NL2SQLExecutionError
from app.nl2sql.executor import SQLExecutor
from app.nl2sql.intent_config import response_include_parsed_intent

logger = get_logger(__name__)


class NL2SQLService:
    """
    NL2SQL 服务层。

    - 通过 NL2SQLChain 调用大模型生成 SQL（支持 LangChain 优先）；
    - 使用 SQLExecutor 执行 SQL；
    - 使用 ConversationManager 记录会话与 SQL 摘要。
    """

    def __init__(
        self,
        chain: NL2SQLChain | None = None,
        executor: SQLExecutor | None = None,
        conv_manager: ConversationManager | None = None,
    ) -> None:
        self._chain = chain or NL2SQLChain()
        self._executor = executor or SQLExecutor()
        self._conv = conv_manager or ConversationManager()

    async def query(
        self,
        req: NL2SQLQueryRequest,
        *,
        record_conversation: bool = True,
        include_parsed_intent: bool | None = None,
    ) -> NL2SQLQueryResponse:
        if not req.user_id:
            raise ValueError("user_id is required (must be provided by the caller).")
        if record_conversation:
            self._conv.append_user_message(req.user_id, req.session_id, req.question)

        NL2SQL_QUERY_COUNT.inc()

        t_query = perf_counter()
        arid = req.analysis_request_id or "-"
        piid = req.plan_item_id or "-"
        logger.info(
            "NL2SQLService.query start user_id=%s session_id=%s record_conversation=%s analysis_request_id=%s plan_item_id=%s",
            req.user_id,
            req.session_id,
            record_conversation,
            arid,
            piid,
        )
        sql, vctx = await self._chain.generate_sql_with_validation_context(
            req.question,
            user_id=req.user_id,
            analysis_type=req.analysis_type,
            plan_item_id=req.plan_item_id,
            plan_template_version=req.plan_template_version,
            time_intent_text=req.time_intent_text,
            confirmed_scope=req.confirmed_scope,
            scope_intent_text=req.scope_intent_text,
            original_query=req.original_query,
            sql_gen_extra_hint=req.sql_gen_extra_hint,
        )
        rows: list = []
        execute_succeeded = False
        last_execute_error: BaseException | None = None
        explain_first = os.getenv("NL2SQL_EXPLAIN_BEFORE_EXECUTE", "false").lower() == "true"
        refine_on_exec = os.getenv("NL2SQL_REFINE_ON_EXEC_ERROR", "true").lower() == "true"
        max_refines = max(0, int(os.getenv("NL2SQL_MAX_EXEC_REFINES", "0")))
        refine_attempts_left = max_refines

        if not (sql or "").strip():
            logger.warning(
                "NL2SQLService.query empty SQL after chain user_id=%s session_id=%s duration_ms=%d analysis_request_id=%s plan_item_id=%s",
                req.user_id,
                req.session_id,
                int((perf_counter() - t_query) * 1000),
                arid,
                piid,
            )
        while (sql or "").strip():
            if explain_first:
                try:
                    explain_rows = await self._executor.explain(sql)
                    logger.info(
                        "NL2SQLService.query EXPLAIN ok user_id=%s session_id=%s explain_rows=%d "
                        "analysis_request_id=%s plan_item_id=%s",
                        req.user_id,
                        req.session_id,
                        len(explain_rows),
                        arid,
                        piid,
                    )
                except Exception as exc_explain:  # noqa: BLE001
                    NL2SQL_QUERY_ERROR_COUNT.inc()
                    logger.exception(
                        "NL2SQLService.query EXPLAIN failed user_id=%s session_id=%s sql_len=%d analysis_request_id=%s plan_item_id=%s",
                        req.user_id,
                        req.session_id,
                        len(sql or ""),
                        arid,
                        piid,
                    )
                    if refine_on_exec and refine_attempts_left > 0:
                        new_sql = await self._chain.refine_sql_after_executor_error(
                            req.question,
                            sql,
                            str(exc_explain),
                            ctx=vctx,
                            time_intent_text=req.time_intent_text,
                        )
                        if new_sql:
                            sql = new_sql
                            refine_attempts_left -= 1
                            continue
                    if record_conversation:
                        explain_code = NL2SQLExecutionError.from_executor_failure(
                            sql=sql, cause=exc_explain
                        ).error_code
                        self._conv.append_assistant_message(
                            req.user_id,
                            req.session_id,
                            f"SQL EXPLAIN error: {explain_code}",
                        )
                    break
            try:
                rows = await self._executor.execute(sql)
                execute_succeeded = True
                logger.info(
                    "NL2SQLService.query execute ok user_id=%s session_id=%s row_count=%d duration_ms=%d analysis_request_id=%s plan_item_id=%s",
                    req.user_id,
                    req.session_id,
                    len(rows),
                    int((perf_counter() - t_query) * 1000),
                    arid,
                    piid,
                )
                break
            except Exception as exc:  # noqa: BLE001
                last_execute_error = exc
                execute_succeeded = False
                NL2SQL_QUERY_ERROR_COUNT.inc()
                logger.exception(
                    "NL2SQLService.query execute failed user_id=%s session_id=%s sql_len=%d analysis_request_id=%s plan_item_id=%s",
                    req.user_id,
                    req.session_id,
                    len(sql or ""),
                    arid,
                    piid,
                )
                if refine_on_exec and refine_attempts_left > 0:
                    new_sql = await self._chain.refine_sql_after_executor_error(
                        req.question,
                        sql,
                        str(exc),
                        ctx=vctx,
                        time_intent_text=req.time_intent_text,
                    )
                    if new_sql:
                        sql = new_sql
                        refine_attempts_left -= 1
                        continue
                if record_conversation:
                    exec_code = NL2SQLExecutionError.from_executor_failure(
                        sql=sql, cause=exc
                    ).error_code
                    self._conv.append_assistant_message(
                        req.user_id,
                        req.session_id,
                        f"SQL execution error: {exec_code}",
                    )
                break

        if (sql or "").strip() and not execute_succeeded:
            exc = NL2SQLExecutionError.from_executor_failure(
                sql=sql or "",
                cause=last_execute_error,
            )
            if vctx.parsed_intent is not None:
                exc.parsed_intent = vctx.parsed_intent
            logger.error(
                "NL2SQLService.query execution failed user_id=%s session_id=%s sql_len=%d "
                "analysis_request_id=%s plan_item_id=%s detail=%s",
                req.user_id,
                req.session_id,
                len(sql or ""),
                arid,
                piid,
                exc.log_detail(),
            )
            if sql:
                logger.error(
                    "NL2SQLService.query failed SQL (log only) user_id=%s session_id=%s\n%s",
                    req.user_id,
                    req.session_id,
                    sql[:8000] + ("..." if len(sql) > 8000 else ""),
                )
            try:
                self._emit_nl2sql_trace(
                    req,
                    sql=sql,
                    status="failed",
                    row_count=0,
                    error=str(exc),
                )
            except Exception:  # noqa: BLE001
                pass
            raise exc from last_execute_error

        if record_conversation:
            self._conv.append_assistant_message(req.user_id, req.session_id, f"SQL: {sql}")

        expose_intent = (
            include_parsed_intent
            if include_parsed_intent is not None
            else response_include_parsed_intent()
        )
        parsed_intent = vctx.parsed_intent if expose_intent else None
        if vctx.parsed_intent:
            logger.info(
                "NL2SQLService.query parsed_intent plan_item_id=%s parse_mode=%s boiler=%s",
                piid,
                (vctx.parsed_intent or {}).get("parse_mode"),
                ((vctx.parsed_intent or {}).get("scope") or {}).get("boiler"),
            )

        resp = NL2SQLQueryResponse(sql=sql, rows=rows, parsed_intent=parsed_intent)
        try:
            self._emit_nl2sql_trace(
                req,
                sql=sql,
                status="success",
                row_count=len(rows or []),
                error=None,
            )
        except Exception:  # noqa: BLE001
            pass
        return resp

    @staticmethod
    def _emit_nl2sql_trace(
        req: NL2SQLQueryRequest,
        *,
        sql: str | None,
        status: str,
        row_count: int,
        error: str | None,
    ) -> None:
        import uuid as _uuid

        from app.observability.sanitizer import sha256_text, truncate_text
        from app.observability.trace_recorder import TraceRecorder

        rid = (req.analysis_request_id or "").strip() or str(_uuid.uuid4())
        tr = TraceRecorder.start(
            module="nl2sql",
            request_id=rid,
            kind="request",
            scene="nl2sql_query",
            user_id=req.user_id,
            session_id=req.session_id,
            meta={
                "analysis_request_id": req.analysis_request_id,
                "plan_item_id": req.plan_item_id,
                "sql_sha256": sha256_text(sql),
                "sql_preview": truncate_text(sql, 256),
            },
        )
        tr.record_node("generate_validate", status="success" if sql else "failed")
        tr.record_node(
            "execute",
            status="failed" if status == "failed" else "success",
            error=error,
            attributes={"row_count": row_count},
        )
        if status == "failed":
            tr.add_degrade("nl2sql_execute_failed")
        tr.finalize(status="failed" if status == "failed" else "success", summary=truncate_text(sql, 200))  # type: ignore[arg-type]

