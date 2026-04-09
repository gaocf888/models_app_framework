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

from app.models.nl2sql import NL2SQLQueryRequest, NL2SQLQueryResponse
from app.services.nl2sql_service import NL2SQLService

router = APIRouter()
service = NL2SQLService()


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
    return await service.query(req)
