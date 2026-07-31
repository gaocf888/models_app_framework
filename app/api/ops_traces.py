from __future__ import annotations

"""统一运维 traces API：GET /ops/traces*。"""

from typing import Annotated, Any, Optional

from fastapi import APIRouter, HTTPException, Path, Query

from app.models.execution_trace import (
    ExecutionTraceDegradeTopNResponse,
    ExecutionTraceDetailResponse,
    ExecutionTraceListResponse,
    ExecutionTraceStatsResponse,
    ExecutionTraceTrendResponse,
)
from app.observability.settings import get_execution_trace_settings
from app.services.execution_trace_service import ExecutionTraceService
from app.services.execution_trace_store import get_execution_trace_store

router = APIRouter()
_service = ExecutionTraceService()


@router.get("/traces-status", summary="Execution Trace / OTLP（Tempo）运行时配置状态")
def traces_status() -> dict[str, Any]:
    """运维探活：确认 Redis 后端与 Tempo OTLP 是否按推荐路径生效。"""
    cfg = get_execution_trace_settings()
    store = get_execution_trace_store()
    return {
        "ok": True,
        "enabled": cfg.enabled,
        "backend_configured": cfg.backend,
        "backend_impl": store.backend_name(),
        "redis_url_configured": bool(cfg.redis_url),
        "ttl_minutes": cfg.ttl_minutes,
        "max_items": cfg.max_items,
        "modules": sorted(cfg.modules),
        "otlp_enabled": cfg.otlp_enabled,
        "otlp_endpoint": cfg.otlp_endpoint,
        "otlp_protocol": cfg.otlp_protocol,
        "otlp_service_name": cfg.otlp_service_name,
        "otlp_sample_rate": cfg.otlp_sample_rate,
        "otlp_preassign_trace_id": cfg.otlp_preassign_trace_id,
        "otlp_job_live_export": cfg.otlp_job_live_export,
        "recommended_path": "redis+tempo",
    }


@router.get("/traces", response_model=ExecutionTraceListResponse, summary="分页列出执行轨迹")
def list_traces(
    limit: int = Query(50, ge=1, le=1000),
    offset: int = Query(0, ge=0),
    module: Optional[str] = Query(None),
    kind: Optional[str] = Query(None, description="request | job"),
    scene: Optional[str] = Query(None),
    status: Optional[str] = Query(None),
    started_after: Optional[str] = Query(None, description="ISO8601 下界（含）"),
    started_before: Optional[str] = Query(None, description="ISO8601 上界（含）"),
) -> ExecutionTraceListResponse:
    return _service.list_traces(
        limit=limit,
        offset=offset,
        module=module,
        kind=kind,
        scene=scene,
        status=status,
        started_after=started_after,
        started_before=started_before,
    )


@router.get("/traces/stats", response_model=ExecutionTraceStatsResponse, summary="执行轨迹聚合统计")
def traces_stats(
    module: Optional[str] = Query(None),
    kind: Optional[str] = Query(None),
) -> ExecutionTraceStatsResponse:
    return _service.stats(module=module, kind=kind)


@router.get("/traces/trend", response_model=ExecutionTraceTrendResponse, summary="执行轨迹时间趋势")
def traces_trend(
    bucket: str = Query("hour", pattern="^(minute|hour)$"),
) -> ExecutionTraceTrendResponse:
    return _service.trend(bucket=bucket)


@router.get(
    "/traces/degrade-topn",
    response_model=ExecutionTraceDegradeTopNResponse,
    summary="降级原因 TopN",
)
def traces_degrade_topn(
    n: int = Query(20, ge=1, le=100),
    module: Optional[str] = Query(None),
) -> ExecutionTraceDegradeTopNResponse:
    return _service.degrade_topn(n, module=module)


@router.get(
    "/traces/{request_id}/result",
    summary="按需拉取业务全文（analysis 等有 payload_ref 时）",
)
def get_trace_result(
    request_id: Annotated[str, Path(description="request_id / job_id")],
) -> dict[str, Any]:
    payload = _service.get_result_payload(request_id)
    if payload is None:
        raise HTTPException(status_code=404, detail=f"trace not found: {request_id}")
    return payload


@router.get(
    "/traces/{request_id}",
    response_model=ExecutionTraceDetailResponse,
    summary="按 request_id / job_id 查询执行轨迹",
)
def get_trace(
    request_id: Annotated[str, Path(description="request_id；任务类等于 job_id")],
) -> ExecutionTraceDetailResponse:
    detail = _service.get_detail(request_id)
    if detail is None:
        raise HTTPException(status_code=404, detail=f"trace not found: {request_id}")
    return detail
