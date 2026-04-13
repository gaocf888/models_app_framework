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

from fastapi import APIRouter

from app.core.logging import get_logger
from app.models.nl2sql import NL2SQLQueryRequest, NL2SQLQueryResponse
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

    Returns:
        NL2SQLQueryResponse: `sql` 为模型生成的语句，`rows` 为查询结果列表（执行失败时行为见服务层与会话记录）。

    Raises:
        HTTPException: 路由层不直接抛出；校验失败 422。
        ValueError: 服务层在 `user_id` 为空时可能抛出。
    """
    logger.info(
        "NL2SQL HTTP /query start user_id=%s session_id=%s question_len=%d preview=%r",
        req.user_id,
        req.session_id,
        len(req.question or ""),
        _question_preview(req.question or ""),
    )
    resp = await service.query(req)
    logger.info(
        "NL2SQL HTTP /query done user_id=%s session_id=%s sql_len=%d row_count=%d sql_empty=%s",
        req.user_id,
        req.session_id,
        len(resp.sql or ""),
        len(resp.rows or []),
        not (resp.sql or "").strip(),
    )
    return resp
