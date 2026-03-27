from __future__ import annotations

"""
NL2SQL 接口。

服务配置前置条件（运维/开发）：
1) LLM 服务可用：
   - 需正确配置模型调用参数（模型名称、服务地址、鉴权）。
2) 数据库连接可用：
   - 需配置业务数据库连接参数（DB_URL 或 DB_HOST/DB_USER/DB_PASSWORD/DB_NAME）。
3) 可选 NL2SQL-RAG：
   - 若启用 RAG 增强，需配置 RAG 向量库与嵌入模型（RAG_ES_* / EMBEDDING_MODEL_*）。
"""

from fastapi import APIRouter

from app.models.nl2sql import NL2SQLQueryRequest, NL2SQLQueryResponse
from app.services.nl2sql_service import NL2SQLService

router = APIRouter()
service = NL2SQLService()


@router.post("/query", response_model=NL2SQLQueryResponse, summary="NL2SQL 查询（基础版）")
async def nl2sql_query(req: NL2SQLQueryRequest) -> NL2SQLQueryResponse:
    """
    NL2SQL 查询接口。

    参数说明（见 NL2SQLQueryRequest）：
    - 必传：user_id、session_id、question
    - 可选：当前无额外业务参数（后续可扩展）
    - 默认行为：按服务端默认配置执行 NL2SQL 生成、校验与执行流程
    """
    return await service.query(req)

