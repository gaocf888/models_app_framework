from __future__ import annotations

"""统一执行轨迹模型（在线请求 + 异步任务同构）。"""

from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field

TraceKind = Literal["request", "job"]
TraceStatus = Literal["success", "partial", "failed", "aborted", "running"]
NodeStatus = Literal["success", "failed", "skipped", "running"]


class TraceNode(BaseModel):
    """单个图节点或流水线阶段。"""

    node_id: str = Field(..., description="节点/阶段名")
    status: NodeStatus = Field("success", description="节点状态")
    latency_ms: Optional[int] = Field(None, description="耗时毫秒")
    started_at: Optional[str] = Field(None, description="开始时间 ISO8601")
    finished_at: Optional[str] = Field(None, description="结束时间 ISO8601")
    error: Optional[str] = Field(None, description="截断后的错误信息")
    attributes: Dict[str, Any] = Field(default_factory=dict, description="小字段扩展")


class ExecutionTraceRecord(BaseModel):
    """统一执行轨迹记录（本地 Store / OTLP / LangSmith 同源）。"""

    request_id: str = Field(..., description="主键；job 时等于 job_id")
    kind: TraceKind = Field("request", description="request | job")
    module: str = Field(..., description="业务模块标识")
    scene: Optional[str] = Field(None, description="场景/意图/模式")
    user_id: Optional[str] = Field(None, description="用户 ID")
    session_id: Optional[str] = Field(None, description="会话 ID")
    status: TraceStatus = Field("running", description="整体状态")
    started_at: str = Field(..., description="开始时间 ISO8601")
    finished_at: Optional[str] = Field(None, description="结束时间 ISO8601")
    total_latency_ms: Optional[int] = Field(None, description="总耗时毫秒")
    nodes: List[TraceNode] = Field(default_factory=list, description="有序节点")
    degrade_reasons: List[str] = Field(default_factory=list, description="降级原因")
    summary: Optional[str] = Field(None, description="短摘要")
    meta: Dict[str, Any] = Field(default_factory=dict, description="模块扩展元数据")
    payload_ref: Optional[str] = Field(None, description="大结果引用键")


class ExecutionTraceListItem(BaseModel):
    request_id: str
    kind: TraceKind
    module: str
    scene: Optional[str] = None
    status: TraceStatus
    summary_preview: str = ""
    started_at: str
    total_latency_ms: Optional[int] = None


class ExecutionTraceListResponse(BaseModel):
    ok: bool = True
    limit: int
    offset: int
    total: int
    items: List[ExecutionTraceListItem] = Field(default_factory=list)


class ExecutionTraceDetailResponse(BaseModel):
    ok: bool = True
    trace: ExecutionTraceRecord


class ExecutionTraceStatsResponse(BaseModel):
    ok: bool = True
    total: int = 0
    by_module: Dict[str, int] = Field(default_factory=dict)
    by_kind: Dict[str, int] = Field(default_factory=dict)
    by_status: Dict[str, int] = Field(default_factory=dict)
    degrade_reasons: Dict[str, int] = Field(default_factory=dict)


class ExecutionTraceTrendPoint(BaseModel):
    bucket_start: str
    total: int = 0
    by_kind: Dict[str, int] = Field(default_factory=dict)


class ExecutionTraceTrendResponse(BaseModel):
    ok: bool = True
    bucket: Literal["minute", "hour"] = "hour"
    points: List[ExecutionTraceTrendPoint] = Field(default_factory=list)


class ExecutionTraceDegradeItem(BaseModel):
    reason: str
    count: int = 0


class ExecutionTraceDegradeTopNResponse(BaseModel):
    ok: bool = True
    items: List[ExecutionTraceDegradeItem] = Field(default_factory=list)
