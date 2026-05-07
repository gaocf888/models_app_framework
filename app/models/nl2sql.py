from __future__ import annotations

from typing import Any, List

from pydantic import BaseModel, Field, field_validator

from app.conversation.ids import validate_session_id, validate_user_id


class NL2SQLQueryRequest(BaseModel):
    user_id: str = Field(..., description="用户唯一标识（由调用方后台传入）")
    session_id: str = Field(..., description="会话唯一标识")
    question: str = Field(..., description="自然语言问题")
    analysis_type: str | None = Field(default=None, description="可选：分析场景类型")
    analysis_request_id: str | None = Field(
        default=None,
        description="可选：综合分析等上层编排的 request_id，用于日志关联",
    )
    plan_item_id: str | None = Field(
        default=None,
        description="可选：数据计划子任务 item_id（如 q1、q2）",
    )

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

