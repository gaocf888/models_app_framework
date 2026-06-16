"""NL2SQLService 执行失败抛出结构化异常。"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.models.nl2sql import NL2SQLQueryRequest
from app.nl2sql.chain import NL2SQLValidationContext
from app.nl2sql.errors import NL2SQLExecutionError
from app.services.nl2sql_service import NL2SQLService


@pytest.mark.asyncio
async def test_query_raises_nl2sql_execution_error_after_execute_failure() -> None:
    vctx = NL2SQLValidationContext(
        allowed_tables=frozenset(),
        allowed_columns=frozenset(),
        schema_ok=False,
        table_columns={},
        join_whitelist=frozenset(),
        parsed_intent={"parse_mode": "rule", "scope": {"boiler": "1号锅炉"}},
    )
    chain = MagicMock()
    chain.generate_sql_with_validation_context = AsyncMock(return_value=("SELECT bad", vctx))
    chain.refine_sql_after_executor_error = AsyncMock(return_value=None)
    executor = MagicMock()
    executor.execute = AsyncMock(
        side_effect=RuntimeError("(1054, \"Unknown column 'bad.col' in 'field list'\")")
    )
    svc = NL2SQLService(chain=chain, executor=executor, conv_manager=MagicMock())

    with patch.dict("os.environ", {"NL2SQL_REFINE_ON_EXEC_ERROR": "false", "NL2SQL_MAX_EXEC_REFINES": "0"}):
        with pytest.raises(NL2SQLExecutionError) as exc_info:
            await svc.query(
                NL2SQLQueryRequest(user_id="u1", session_id="s1", question="测试"),
                record_conversation=False,
            )

    err = exc_info.value
    assert err.error_code == "unknown_column"
    assert err.sql == "SELECT bad"
    assert "1054" not in str(err)
    assert err.parsed_intent == vctx.parsed_intent
