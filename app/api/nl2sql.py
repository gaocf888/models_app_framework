from __future__ import annotations

"""
NL2SQL HTTP 接口（`/nl2sql/query`）。

职责：
    - 将自然语言问题交给 `NL2SQLService`：大模型生成 SQL、`SQLExecutor` 执行、
      结果与会话摘要写入 `ConversationManager`。

鉴权与身份：
    - 请求头 `Authorization: Bearer <SERVICE_API_KEY>`（密钥生成见 `app/auth/keygen.py`，部署说明见 `app/app-deploy/README.md`）；
    - `user_id`、`session_id` 由调用方传入，用于会话轨迹与生成 SQL 时的侧写（若链中使用）。
"""

import os

from fastapi import APIRouter, HTTPException

from app.core.logging import get_logger
from app.models.nl2sql import NL2SQLQueryRequest, NL2SQLQueryResponse
from app.nl2sql.errors import NL2SQLExecutionError
from app.services.nl2sql_service import NL2SQLService

router = APIRouter()
service = NL2SQLService()
logger = get_logger(__name__)


def _question_preview(q: str, max_len: int = 160) -> str:
    s = (q or "").replace("\n", " ").strip()
    if len(s) <= max_len:
        return s
    return s[: max_len - 3] + "..."


@router.post("/query", response_model=NL2SQLQueryResponse, summary="NL2SQL 查询（基础版）")
async def nl2sql_query(req: NL2SQLQueryRequest) -> NL2SQLQueryResponse:
    """
    根据自然语言问题生成 SQL 并执行，返回结果行（若有）。

    Args:
        req (NL2SQLQueryRequest): 必填 `user_id`、`session_id`、`question`。
            可选 `time_intent_text`：仅用于动态时间窗等从该文本抽取时间语义（未填则等同 `question`）。

    Returns:
        NL2SQLQueryResponse:
            - ``sql``：模型生成并经 TiDB/时间/范围改写后的 SQL；
            - ``rows``：执行结果行列表；
            - ``parsed_intent``（可选）：问句意图 JSON。默认不返回；部署侧设置
              ``NL2SQL_RESPONSE_INCLUDE_PARSED_INTENT=true`` 后包含，结构见
              ``NL2SQLQueryResponse.parsed_intent`` 字段说明。

    Raises:
        HTTPException: SQL 执行失败时 502，detail 含 ``error_code`` 与可选 ``sql``。
        ValueError: 服务层在 ``user_id`` 为空时可能抛出。
    """
    logger.info(
        "NL2SQL HTTP /query start user_id=%s session_id=%s question_len=%d preview=%r",
        req.user_id,
        req.session_id,
        len(req.question or ""),
        _question_preview(req.question or ""),
    )
    try:
        resp = await service.query(req)
    except NL2SQLExecutionError as exc:
        expose_sql = os.getenv("NL2SQL_API_EXPOSE_SQL_ON_ERROR", "true").lower() == "true"
        logger.error(
            "NL2SQL HTTP /query execution failed user_id=%s session_id=%s error_code=%s detail=%s",
            req.user_id,
            req.session_id,
            exc.error_code,
            exc.log_detail(),
        )
        raise HTTPException(
            status_code=502,
            detail={
                "message": "SQL execution failed",
                "error_code": exc.error_code,
                "sql": exc.sql if expose_sql else None,
            },
        ) from exc
    logger.info(
        "NL2SQL HTTP /query done user_id=%s session_id=%s sql_len=%d row_count=%d sql_empty=%s",
        req.user_id,
        req.session_id,
        len(resp.sql or ""),
        len(resp.rows or []),
        not (resp.sql or "").strip(),
    )
    return resp
