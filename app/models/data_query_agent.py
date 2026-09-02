from __future__ import annotations

"""数据查询智能体请求/响应模型（解析 SQL 路径）。"""

from typing import Any

from pydantic import BaseModel, Field, field_validator

from app.conversation.ids import validate_session_id, validate_user_id


class DataQueryAgentOptions(BaseModel):
    max_rows: int | None = Field(None, ge=1, le=20000, description="列表截断行数")
    include_hud: bool = Field(
        True,
        description="是否附带 HUD；站点/区/市列表按行实体给时序，false 仅列表",
    )
    expose_sql: bool = Field(False, description="联调时在 result 中带 sql")


class DataQueryAgentRunRequest(BaseModel):
    """run-stream：query 必填；library_id 为树当前库，可空。"""

    user_id: str = Field(..., description="用户唯一标识")
    session_id: str = Field(..., description="会话唯一标识")
    query: str = Field(..., description="自然语言查询")
    library_id: str | None = Field(None, description="左侧树当前选中的库节点，可空")
    district: str | None = Field(None, description="树点到行政区时传入，优先于问句解析")
    station_id: str | None = Field(None, description="树点到站点时传入，优先于问句解析")
    options: DataQueryAgentOptions = Field(default_factory=DataQueryAgentOptions)

    @field_validator("user_id")
    @classmethod
    def _v_uid(cls, v: str) -> str:
        return validate_user_id(v)

    @field_validator("session_id")
    @classmethod
    def _v_sid(cls, v: str) -> str:
        return validate_session_id(v)

    @field_validator("query")
    @classmethod
    def _v_query(cls, v: str) -> str:
        s = (v or "").strip()
        if not s:
            raise ValueError("query is required")
        return s

    @field_validator("library_id")
    @classmethod
    def _v_lib(cls, v: str | None) -> str | None:
        if v is None:
            return None
        s = str(v).strip()
        return s or None

    @field_validator("district", "station_id")
    @classmethod
    def _v_opt_scope(cls, v: str | None) -> str | None:
        if v is None:
            return None
        s = str(v).strip()
        return s or None


class DataQueryAgentResumeRequest(BaseModel):
    """resume-stream：须带 HITL 的 resume_token；abort=true 取消。"""
    user_id: str = Field(..., description="用户唯一标识")
    session_id: str = Field(..., description="会话唯一标识")
    resume_token: str = Field(..., description="中断事件返回的 resume_token")
    library_id: str | None = Field(None, description="用户选定的库")
    abort: bool = Field(False, description="放弃本次解析")

    @field_validator("user_id")
    @classmethod
    def _v_uid(cls, v: str) -> str:
        return validate_user_id(v)

    @field_validator("session_id")
    @classmethod
    def _v_sid(cls, v: str) -> str:
        return validate_session_id(v)

    @field_validator("resume_token")
    @classmethod
    def _v_token(cls, v: str) -> str:
        s = (v or "").strip()
        if not s:
            raise ValueError("resume_token is required")
        return s


class DataQueryAgentStreamStopRequest(BaseModel):
    user_id: str = Field(..., description="用户唯一标识")
    session_id: str = Field(..., description="会话唯一标识")
    stream_id: str = Field(..., description="started 事件返回的 stream_id")

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
    def _v_stream(cls, v: str) -> str:
        s = (v or "").strip()
        if not s:
            raise ValueError("stream_id is required")
        return s


class DataQueryAgentStreamStopResponse(BaseModel):
    ok: bool = True
    stream_id: str
    message: str = "stop signal sent"


class DataQueryAgentLibrariesResponse(BaseModel):
    ok: bool = True
    version: str = ""
    default_library_id: str = ""
    hitl_prompt: str = ""
    groups: list[dict[str, Any]] = Field(default_factory=list)
    libraries: list[dict[str, Any]] = Field(default_factory=list)
    library_options: list[dict[str, Any]] = Field(default_factory=list)


class DataQueryAgentTraceListItem(BaseModel):
    request_id: str
    library_id: str = ""
    status: str = "success"
    user_id: str = ""
    result_grain: str = ""
    hud_enabled: bool = False
    created_at: str = ""
    warning_count: int = 0


class DataQueryAgentTraceListResponse(BaseModel):
    ok: bool = True
    limit: int
    offset: int
    total: int
    items: list[DataQueryAgentTraceListItem] = Field(default_factory=list)


class DataQueryAgentTraceStatsResponse(BaseModel):
    ok: bool = True
    total: int = 0
    by_library_id: dict[str, int] = Field(default_factory=dict)
    by_status: dict[str, int] = Field(default_factory=dict)
    warnings: dict[str, int] = Field(default_factory=dict)


class DataQueryAgentHudResponse(BaseModel):
    """GET /hud：单实体面板，字段与 data_query_result.hud_by_entity[id] 对齐。"""

    ok: bool = True
    request_id: str = ""
    library_id: str
    entity_type: str
    entity_id: str
    hud: dict[str, Any]
    warnings: list[str] = Field(default_factory=list)
    found: bool = True
    sql: dict[str, Any] | None = None
