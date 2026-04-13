from __future__ import annotations

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
        sql = await self._chain.generate_sql(req.question, user_id=req.user_id)
        rows = []
        if not (sql or "").strip():
            logger.warning(
                "NL2SQLService.query empty SQL after chain user_id=%s session_id=%s",
                req.user_id,
                req.session_id,
            )
        if sql:
            try:
                rows = await self._executor.execute(sql)
                logger.info(
                    "NL2SQLService.query execute ok user_id=%s session_id=%s row_count=%d",
                    req.user_id,
                    req.session_id,
                    len(rows),
                )
            except Exception as exc:  # noqa: BLE001
                NL2SQL_QUERY_ERROR_COUNT.inc()
                logger.exception(
                    "NL2SQLService.query execute failed user_id=%s session_id=%s sql_len=%d",
                    req.user_id,
                    req.session_id,
                    len(sql or ""),
                )
                if record_conversation:
                    self._conv.append_assistant_message(
                        req.user_id, req.session_id, f"SQL execution error: {exc}"
                    )

        if record_conversation:
            self._conv.append_assistant_message(req.user_id, req.session_id, f"SQL: {sql}")

        return NL2SQLQueryResponse(sql=sql, rows=rows)

