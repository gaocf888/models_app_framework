from __future__ import annotations

from app.conversation.manager import ConversationManager
from app.core.metrics import NL2SQL_QUERY_COUNT, NL2SQL_QUERY_ERROR_COUNT
from app.models.nl2sql import NL2SQLQueryRequest, NL2SQLQueryResponse
from app.nl2sql.chain import NL2SQLChain
from app.nl2sql.executor import SQLExecutor


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

    async def query(self, req: NL2SQLQueryRequest) -> NL2SQLQueryResponse:
        # 记录用户问题
        self._conv.append_user_message(req.user_id, req.session_id, req.question)

        NL2SQL_QUERY_COUNT.inc()

        sql = await self._chain.generate_sql(req.question, user_id=req.user_id)
        rows = []
        if sql:
            try:
                rows = await self._executor.execute(sql)
            except Exception as exc:  # noqa: BLE001
                NL2SQL_QUERY_ERROR_COUNT.inc()
                # 将错误信息简要记录在会话中，便于后续分析
                self._conv.append_assistant_message(req.user_id, req.session_id, f"SQL execution error: {exc}")

        # 记录生成的 SQL 摘要
        self._conv.append_assistant_message(req.user_id, req.session_id, f"SQL: {sql}")

        return NL2SQLQueryResponse(sql=sql, rows=rows)

