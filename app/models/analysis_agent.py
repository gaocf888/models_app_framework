from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field, field_validator

from app.conversation.ids import validate_session_id, validate_user_id

AnalysisAgentType = Literal[
    "overheat_guidance",
    "maintenance_strategy",
    "four_tube_health_interpretation",
    "leakage_burst_analysis",
    "subsidence_daily",
    "subsidence_weekly",
    "subsidence_monthly",
    "subsidence_quarterly",
    "subsidence_yearly",
]


class AnalysisAgentOptions(BaseModel):
    enable_rag: bool = Field(True, description="是否启用业务 RAG")
    strict: bool = Field(False, description="mandatory 数据缺失是否失败")
    max_rows_per_query: int = Field(2000, ge=50, le=20000)
    chart_mode: Literal["auto", "minimal", "off"] = Field("auto")
    plan_template_version: str = Field(
        "",
        description="analysis_agent 模板逻辑版本；空则使用 ANALYSIS_AGENT_PLAN_TEMPLATE_VERSION（默认 analysis_agent_v1）。传入 v1/v2 将自动映射为 env 默认（与现网 /analysis 隔离）",
    )
    enable_human_in_the_loop: bool = Field(
        False,
        description="T1 起主路径已去掉缺数 HITL；保留字段仅兼容旧客户端，默认 false 且编排忽略",
    )
    use_react_agent: bool | None = Field(
        None,
        description="是否允许 ReAct；默认跟随 ANALYSIS_AGENT_USE_REACT_AGENT；仅 use_emit_tools 章生效",
    )
    narrative_streaming: bool | None = Field(
        None,
        description="叙述章是否真流式推送 summary_delta；默认跟随 ANALYSIS_AGENT_NARRATIVE_STREAMING",
    )
    quality_profile: Literal["light", "strict_like"] | None = Field(
        None,
        description="质量门强度；默认跟随 ANALYSIS_AGENT_QUALITY_PROFILE（light）",
    )


class AnalysisAgentStreamStopRequest(BaseModel):
    user_id: str = Field(..., description="用户唯一标识")
    session_id: str = Field(..., description="会话唯一标识")
    stream_id: str = Field(..., description="需要停止的流式请求标识（由 started 事件返回）")

    @field_validator("user_id")
    @classmethod
    def _v_uid(cls, v: str) -> str:
        return validate_user_id(v)

    @field_validator("session_id")
    @classmethod
    def _v_sid(cls, v: str) -> str:
        return validate_session_id(v)

    @field_validator("stream_id")
    @classmethod
    def _validate_stream_id(cls, v: str) -> str:
        s = (v or "").strip()
        if not s:
            raise ValueError("stream_id is required")
        return s


class AnalysisAgentStreamStopResponse(BaseModel):
    ok: bool = True
    stream_id: str = Field(..., description="被停止的流式请求 ID")
    message: str = "stop signal sent"


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


class AnalysisAgentTraceListItem(BaseModel):
    request_id: str
    analysis_type: str
    summary_preview: str = ""
    created_at: str = ""
    status: str = "success"
    user_id: str = ""
    degrade_count: int = 0


class AnalysisAgentTraceListResponse(BaseModel):
    ok: bool = True
    limit: int
    offset: int
    total: int
    items: List[AnalysisAgentTraceListItem] = Field(default_factory=list)


class AnalysisAgentTraceStatsResponse(BaseModel):
    ok: bool = True
    total: int = 0
    by_analysis_type: Dict[str, int] = Field(default_factory=dict)
    by_status: Dict[str, int] = Field(default_factory=dict)
    degrade_reasons: Dict[str, int] = Field(default_factory=dict)


class AnalysisAgentTraceTrendPoint(BaseModel):
    bucket_start: str
    total: int = 0
    by_analysis_type: Dict[str, int] = Field(default_factory=dict)


class AnalysisAgentTraceTrendResponse(BaseModel):
    ok: bool = True
    bucket: Literal["minute", "hour"] = "hour"
    points: List[AnalysisAgentTraceTrendPoint] = Field(default_factory=list)


class AnalysisAgentTraceDegradeItem(BaseModel):
    reason: str
    count: int = 0


class AnalysisAgentTraceDegradeTopNResponse(BaseModel):
    ok: bool = True
    total_unique: int = 0
    items: List[AnalysisAgentTraceDegradeItem] = Field(default_factory=list)


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
