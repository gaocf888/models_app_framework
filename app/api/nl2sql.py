from __future__ import annotations

from fastapi import APIRouter

from app.models.nl2sql import NL2SQLQueryRequest, NL2SQLQueryResponse
from app.services.nl2sql_service import NL2SQLService

router = APIRouter()
service = NL2SQLService()


@router.post("/query", response_model=NL2SQLQueryResponse, summary="NL2SQL 查询（基础版）")
async def nl2sql_query(req: NL2SQLQueryRequest) -> NL2SQLQueryResponse:
    """
    NL2SQL 查询接口（V1，占位实现）。

    - 接收自然语言问题；
    - 通过 NL2SQLChain + LLM 生成 SQL（已做基础安全校验）；
    - 通过 SQLExecutor 执行并返回结果（当前为占位空结果）。
    """
    return await service.query(req)

