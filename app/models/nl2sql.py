from __future__ import annotations

from typing import Any, List

from pydantic import BaseModel, Field, field_validator

from app.conversation.ids import validate_session_id, validate_user_id


class NL2SQLQueryRequest(BaseModel):
    user_id: str = Field(..., description="用户唯一标识（由调用方后台传入）")
    session_id: str = Field(..., description="会话唯一标识")
    question: str = Field(..., description="自然语言问题")

    @field_validator("user_id")
    @classmethod
    def _v_uid(cls, v: str) -> str:
        return validate_user_id(v)

    @field_validator("session_id")
    @classmethod
    def _v_sid(cls, v: str) -> str:
        return validate_session_id(v)


class NL2SQLQueryResponse(BaseModel):
    sql: str = Field(..., description="生成的 SQL 语句")
    rows: List[dict[str, Any]] = Field(default_factory=list, description="查询结果（占位）")

