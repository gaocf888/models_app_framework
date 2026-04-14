from __future__ import annotations

import os

from app.conversation.manager import ConversationManager
from app.core.logging import get_logger
from app.core.metrics import NL2SQL_QUERY_COUNT, NL2SQL_QUERY_ERROR_COUNT
from app.models.nl2sql import NL2SQLQueryRequest, NL2SQLQueryResponse
from app.nl2sql.chain import NL2SQLChain
from app.nl2sql.executor import SQLExecutor

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

    async def query(self, req: NL2SQLQueryRequest, *, record_conversation: bool = True) -> NL2SQLQueryResponse:
        if not req.user_id:
            raise ValueError("user_id is required (must be provided by the caller).")
        if record_conversation:
            self._conv.append_user_message(req.user_id, req.session_id, req.question)

        NL2SQL_QUERY_COUNT.inc()

        logger.info(
            "NL2SQLService.query start user_id=%s session_id=%s record_conversation=%s",
            req.user_id,
            req.session_id,
            record_conversation,
        )
        sql, vctx = await self._chain.generate_sql_with_validation_context(
            req.question, user_id=req.user_id
        )
        rows: list = []
        explain_first = os.getenv("NL2SQL_EXPLAIN_BEFORE_EXECUTE", "false").lower() == "true"
        refine_on_exec = os.getenv("NL2SQL_REFINE_ON_EXEC_ERROR", "true").lower() == "true"
        max_refines = max(0, int(os.getenv("NL2SQL_MAX_EXEC_REFINES", "1")))
        refine_attempts_left = max_refines

        if not (sql or "").strip():
            logger.warning(
                "NL2SQLService.query empty SQL after chain user_id=%s session_id=%s",
                req.user_id,
                req.session_id,
            )
        while (sql or "").strip():
            if explain_first:
                try:
                    await self._executor.explain(sql)
                except Exception as exc_explain:  # noqa: BLE001
                    NL2SQL_QUERY_ERROR_COUNT.inc()
                    logger.exception(
                        "NL2SQLService.query EXPLAIN failed user_id=%s session_id=%s sql_len=%d",
                        req.user_id,
                        req.session_id,
                        len(sql or ""),
                    )
                    if refine_on_exec and refine_attempts_left > 0:
                        new_sql = await self._chain.refine_sql_after_executor_error(
                            req.question,
                            sql,
                            str(exc_explain),
                            ctx=vctx,
                        )
                        if new_sql:
                            sql = new_sql
                            refine_attempts_left -= 1
                            continue
                    if record_conversation:
                        self._conv.append_assistant_message(
                            req.user_id,
                            req.session_id,
                            f"SQL EXPLAIN error: {exc_explain}",
                        )
                    break
            try:
                rows = await self._executor.execute(sql)
                logger.info(
                    "NL2SQLService.query execute ok user_id=%s session_id=%s row_count=%d",
                    req.user_id,
                    req.session_id,
                    len(rows),
                )
                break
            except Exception as exc:  # noqa: BLE001
                NL2SQL_QUERY_ERROR_COUNT.inc()
                logger.exception(
                    "NL2SQLService.query execute failed user_id=%s session_id=%s sql_len=%d",
                    req.user_id,
                    req.session_id,
                    len(sql or ""),
                )
                if refine_on_exec and refine_attempts_left > 0:
                    new_sql = await self._chain.refine_sql_after_executor_error(
                        req.question,
                        sql,
                        str(exc),
                        ctx=vctx,
                    )
                    if new_sql:
                        sql = new_sql
                        refine_attempts_left -= 1
                        continue
                if record_conversation:
                    self._conv.append_assistant_message(
                        req.user_id, req.session_id, f"SQL execution error: {exc}"
                    )
                break

        if record_conversation:
            self._conv.append_assistant_message(req.user_id, req.session_id, f"SQL: {sql}")

        return NL2SQLQueryResponse(sql=sql, rows=rows)

