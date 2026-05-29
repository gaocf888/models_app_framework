from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field, field_validator

from app.conversation.ids import validate_session_id, validate_user_id

AnalysisAgentType = Literal[
    "overheat_guidance",
    "maintenance_strategy",
    "four_tube_health_interpretation",
    "leakage_burst_analysis",
]


class AnalysisAgentOptions(BaseModel):
    enable_rag: bool = Field(True, description="是否启用业务 RAG")
    strict: bool = Field(False, description="mandatory 数据缺失是否失败")
    max_rows_per_query: int = Field(2000, ge=50, le=20000)
    chart_mode: Literal["auto", "minimal", "off"] = Field("auto")
    plan_template_version: str = Field(
        "",
        description="analysis_agent 模板版本；空则 v1（统一多槽位）。传入 v2 将自动视为 v1（兼容旧客户端）",
    )
    enable_human_in_the_loop: bool = Field(True)


class AnalysisAgentRunRequest(BaseModel):
    user_id: str = Field(..., description="用户唯一标识")
    session_id: str = Field(..., description="会话唯一标识")
    analysis_type: AnalysisAgentType = Field(..., description="分析类型")
    query: str = Field(..., description="分析需求自然语言描述")
    options: AnalysisAgentOptions = Field(default_factory=AnalysisAgentOptions)

    @field_validator("user_id")
    @classmethod
    def _v_uid(cls, v: str) -> str:
        return validate_user_id(v)

    @field_validator("session_id")
    @classmethod
    def _v_sid(cls, v: str) -> str:
        return validate_session_id(v)


class AnalysisAgentResult(BaseModel):
    request_id: str
    analysis_type: str
    summary: str
    structured_report: Dict[str, Any] = Field(default_factory=dict)
    evidence: Dict[str, Any] = Field(default_factory=dict)
    trace: Dict[str, Any] = Field(default_factory=dict)


class AnalysisAgentResumeRequest(BaseModel):
    resume_token: str
    user_id: str
    session_id: str
    action: str = Field(..., description="widen_time_range | skip_slot | abort | custom")
    payload: Dict[str, Any] = Field(default_factory=dict)

    @field_validator("user_id")
    @classmethod
    def _v_uid(cls, v: str) -> str:
        return validate_user_id(v)

    @field_validator("session_id")
    @classmethod
    def _v_sid(cls, v: str) -> str:
        return validate_session_id(v)
