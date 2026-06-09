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
    plan_template_version: str | None = Field(
        default=None,
        description=(
            "可选：NL2SQL 数据计划模板版本（prompts_bak_new.yaml analysis_plan_<type> 的 version，如 v1/v2）；"
            "综合分析 acquire_data 传入，用于 QA 向量闭环五元组去重"
        ),
    )
    time_intent_text: str | None = Field(
        default=None,
        description=(
            "可选：仅用于动态时间窗等规则从该文本抽取时间语义；未设置时与 question 一致。"
            "综合分析等场景可设为上层用户原句，避免任务 question 末尾 RAG 附录污染时间抽取。"
        ),
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

