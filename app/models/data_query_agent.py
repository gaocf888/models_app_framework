from __future__ import annotations

"""数据查询智能体请求/响应模型（解析 SQL 路径）。"""

from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.conversation.ids import validate_session_id, validate_user_id


class DataQueryAgentOptions(BaseModel):
    max_rows: int | None = Field(None, ge=1, le=20000, description="列表截断行数")
    include_hud: bool = Field(
        True,
        description="是否附带 HUD；站点/区/市列表按行实体给时序，false 仅列表",
    )
    expose_sql: bool = Field(False, description="联调时在 result 中带 sql")


class DataQueryAgentRunRequest(BaseModel):
    """解析 SQL：query 必填；library_id 为树当前库，可空。"""

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
    """选库后续：须带 HITL 的 resume_token；abort=true 取消。"""
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


class DataQueryAgentRunResponse(BaseModel):
    """解析 / 选库恢复一次性 JSON。业务结果看 status / event，HTTP 仍为 200。"""

    model_config = ConfigDict(extra="allow", populate_by_name=True)

    ok: bool = True
    status: str = Field(..., description="success | interrupted | error | cancelled")
    event: str = Field(
        ...,
        description=(
            "data_query_result / data_query_library_input_required / "
            "data_query_error / data_query_cancelled"
        ),
    )
    request_id: str = ""


class DataQueryAgentStreamStopRequest(BaseModel):
    user_id: str = Field(..., description="用户唯一标识")
    session_id: str = Field(..., description="会话唯一标识")
    stream_id: str = Field(..., description="兼容字段；解析/恢复 JSON 路径不再下发 stream_id")

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
    """GET /hud：单实体面板，字段与解析结果 hud_by_entity[id] 对齐。"""

    ok: bool = True
    request_id: str = ""
    library_id: str
    entity_type: str
    entity_id: str
    hud: dict[str, Any]
    warnings: list[str] = Field(default_factory=list)
    found: bool = True
    sql: dict[str, Any] | None = None


class DataQueryAgentTreeStation(BaseModel):
    station_id: str
    station_name: str
    district: str = ""
    latest_value: Any = None
    data_time: Any = None
    unit: str = ""


class DataQueryAgentTreeDistrict(BaseModel):
    district: str
    station_count: int = 0
    stations: list[DataQueryAgentTreeStation] = Field(default_factory=list)


class DataQueryAgentTreeLibrary(BaseModel):
    library_id: str
    display_name: str = ""
    group: str = ""
    station_count: int = 0
    districts: list[DataQueryAgentTreeDistrict] = Field(default_factory=list)


class DataQueryAgentTreeResponse(BaseModel):
    """GET /tree：监测库 → 行政区 → 站点（yaml 覆盖 + t_station.area；无洗数最新值）。"""

    ok: bool = True
    default_library_id: str = "fcb"
    libraries: list[DataQueryAgentTreeLibrary] = Field(default_factory=list)


class DataQueryAgentBrowseResponse(BaseModel):
    """GET /browse：点库/区=代表标列表；点站=设备×时间矩阵。JSON 字段名仍为 list。"""

    model_config = ConfigDict(populate_by_name=True)

    ok: bool = True
    library_id: str
    display_name: str = ""
    district: str | None = None
    station_id: str | None = None
    result_grain: str = "station"
    page: int
    page_size: int
    total: int
    has_more: bool = False
    columns: list[dict[str, Any]] = Field(default_factory=list)
    rows: list[dict[str, Any]] = Field(
        default_factory=list, alias="list", serialization_alias="list"
    )
    hud_enabled: bool = False
    warnings: list[str] = Field(default_factory=list)
