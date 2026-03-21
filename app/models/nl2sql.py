from __future__ import annotations

from typing import Any, List

from pydantic import BaseModel, Field


class NL2SQLQueryRequest(BaseModel):
    user_id: str = Field(..., description="用户唯一标识")
    session_id: str = Field(..., description="会话唯一标识")
    question: str = Field(..., description="自然语言问题")


class NL2SQLQueryResponse(BaseModel):
    sql: str = Field(..., description="生成的 SQL 语句")
    rows: List[dict[str, Any]] = Field(default_factory=list, description="查询结果（占位）")

