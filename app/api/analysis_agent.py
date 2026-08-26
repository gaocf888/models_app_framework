from __future__ import annotations

"""
综合分析智能体 HTTP 接口（analysis_agent）。

**推荐入口**：`/analysis-agent/*`（本路由）。现网 `/analysis/*` 仅作兼容，不再新增特性。

服务配置前置条件（运维/开发）：
1) LLM：`LLM_DEFAULT_MODEL` / `LLM_DEFAULT_ENDPOINT`
2) NL2SQL：数据库与 `NL2SQL_*` 配置
3) RAG（可选）：`enable_rag=true` 时需 ES/RAG 可用
4) LangGraph checkpoint：`ANALYSIS_AGENT_CHECKPOINT_BACKEND=memory|redis`（HITL 须非 none）
5) 多 worker HITL：`ANALYSIS_AGENT_SESSION_STORE_BACKEND=redis` 且 `REDIS_URL` 可用
6) Trace：`ANALYSIS_AGENT_TRACE_BACKEND=memory|redis|elasticsearch`（生产建议 redis）
7) 开关：`ANALYSIS_AGENT_ENABLED=true`
"""

from typing import Annotated

from fastapi import APIRouter, HTTPException, Path, Query
from fastapi.responses import StreamingResponse

from app.models.analysis_agent import (
    AnalysisAgentResumeRequest,
    AnalysisAgentResult,
    AnalysisAgentRunRequest,
    AnalysisAgentStreamStopRequest,
    AnalysisAgentStreamStopResponse,
    AnalysisAgentTraceDegradeTopNResponse,
    AnalysisAgentTraceListResponse,
    AnalysisAgentTraceStatsResponse,
    AnalysisAgentTraceTrendResponse,
)
from app.services.analysis_agent_service import AnalysisAgentService

router = APIRouter()
service = AnalysisAgentService()


@router.post(
    "/run-stream",
    summary="综合分析智能体流式执行",
    response_description="SSE：started → analysis_agent_* 事件序列",
)
async def run_analysis_agent_stream(data: AnalysisAgentRunRequest) -> StreamingResponse:
    """
    按章串行 LangGraph 编排（T1：先全量 acquire_data，再按章合成；T2：真流式 + stop）。

    参数说明（见 `AnalysisAgentRunRequest`）：
    - 必传：user_id、session_id、analysis_type、query
    - 可选：options（enable_rag、strict、chart_mode、use_react_agent、narrative_streaming 等）
    - 首帧 `started` 含 `stream_id`，可调用 `POST /analysis-agent/stream/stop` 中断
    - 缺数 HITL 已从主路径移除；`enable_human_in_the_loop` 默认 false 且编排忽略
    """
    return await service.run_stream(data)


@router.post(
    "/stream/stop",
    response_model=AnalysisAgentStreamStopResponse,
    summary="中断指定综合分析智能体流式任务",
)
async def stop_analysis_agent_stream(
    data: AnalysisAgentStreamStopRequest,
) -> AnalysisAgentStreamStopResponse:
    """
    协作式中断 `/run-stream`（对齐现网 `/analysis/stream/stop`）。

    1. 从首帧 `started.stream_id` 取得标识；
    2. 调用本接口置位取消；
    3. 服务端在 acquire / 章合成检查点停止，下发 `analysis_agent_cancelled`
       与 `finished`（`trace.status=aborted`、`terminate_reason=user_cancelled`）。
    """
    return await service.stop_stream(data.user_id, data.session_id, data.stream_id)


@router.post(
    "/resume-stream",
    summary="人机协同恢复（SSE 续流，兼容保留）",
    response_description="从 interrupt 断点继续推送剩余槽位事件",
    deprecated=True,
)
async def resume_analysis_agent_stream(data: AnalysisAgentResumeRequest) -> StreamingResponse:
    """
    T1 起主路径不再触发 `user_input_required`；本接口仅兼容旧客户端。
    """
    return await service.resume_stream(data)


@router.post(
    "/resume",
    summary="人机协同恢复（同步结果，兼容保留）",
    response_model=AnalysisAgentResult,
    deprecated=True,
)
async def resume_analysis_agent(data: AnalysisAgentResumeRequest) -> AnalysisAgentResult:
    """兼容保留；T1 主路径无缺数 HITL。"""
    try:
        return await service.resume(data)
    except RuntimeError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get(
    "/traces",
    summary="分页查询 analysis_agent trace 列表",
    response_model=AnalysisAgentTraceListResponse,
)
async def list_analysis_agent_traces(
    limit: Annotated[int, Query(description="每页条数", ge=1, le=200)] = 20,
    offset: Annotated[int, Query(description="偏移量", ge=0)] = 0,
    analysis_type: Annotated[str | None, Query(description="可选。按分析类型过滤")] = None,
    user_id: Annotated[str | None, Query(description="可选。按 user_id 过滤")] = None,
    request_id_like: Annotated[
        str | None, Query(description="可选。request_id 子串匹配（窗口内过滤）")
    ] = None,
    started_from: Annotated[str | None, Query(description="可选。时间下界 ISO8601")] = None,
    started_to: Annotated[str | None, Query(description="可选。时间上界 ISO8601")] = None,
) -> AnalysisAgentTraceListResponse:
    items, total = service.list_traces(
        limit=limit,
        offset=offset,
        analysis_type=analysis_type,
        user_id=user_id,
        request_id_like=request_id_like,
        started_from=started_from,
        started_to=started_to,
    )
    return AnalysisAgentTraceListResponse(
        ok=True, limit=limit, offset=offset, total=total, items=items
    )


@router.get(
    "/traces/stats",
    summary="analysis_agent trace 聚合统计",
    response_model=AnalysisAgentTraceStatsResponse,
)
async def get_analysis_agent_trace_stats(
    analysis_type: Annotated[str | None, Query(description="可选。只统计该类型")] = None,
    user_id: Annotated[str | None, Query(description="可选。只统计该用户")] = None,
    started_from: Annotated[str | None, Query(description="可选。时间下界 ISO8601")] = None,
    started_to: Annotated[str | None, Query(description="可选。时间上界 ISO8601")] = None,
) -> AnalysisAgentTraceStatsResponse:
    return service.get_trace_stats(
        analysis_type=analysis_type,
        user_id=user_id,
        started_from=started_from,
        started_to=started_to,
    )


@router.get(
    "/traces/trend",
    summary="analysis_agent trace 时间趋势",
    response_model=AnalysisAgentTraceTrendResponse,
)
async def get_analysis_agent_trace_trend(
    bucket: Annotated[str, Query(description="时间桶：minute 或 hour")] = "hour",
    analysis_type: Annotated[str | None, Query(description="可选。只统计该类型")] = None,
    user_id: Annotated[str | None, Query(description="可选。只统计该用户")] = None,
    started_from: Annotated[str | None, Query(description="可选。时间下界 ISO8601")] = None,
    started_to: Annotated[str | None, Query(description="可选。时间上界 ISO8601")] = None,
) -> AnalysisAgentTraceTrendResponse:
    return service.get_trace_trend(
        bucket=bucket,
        analysis_type=analysis_type,
        user_id=user_id,
        started_from=started_from,
        started_to=started_to,
    )


@router.get(
    "/traces/degrade-topn",
    summary="analysis_agent 降级原因 TopN",
    response_model=AnalysisAgentTraceDegradeTopNResponse,
)
async def get_analysis_agent_trace_degrade_topn(
    top_n: Annotated[int, Query(description="返回条数上限 1～50", ge=1, le=50)] = 10,
    analysis_type: Annotated[str | None, Query(description="可选。只统计该类型")] = None,
    user_id: Annotated[str | None, Query(description="可选。只统计该用户")] = None,
    started_from: Annotated[str | None, Query(description="可选。时间下界 ISO8601")] = None,
    started_to: Annotated[str | None, Query(description="可选。时间上界 ISO8601")] = None,
) -> AnalysisAgentTraceDegradeTopNResponse:
    return service.get_degrade_topn(
        top_n=top_n,
        analysis_type=analysis_type,
        user_id=user_id,
        started_from=started_from,
        started_to=started_to,
    )


@router.get(
    "/traces/{request_id}",
    summary="按 request_id 查询 analysis_agent trace（复数路径，与现网 /analysis/traces 对齐）",
)
async def get_analysis_agent_trace_plural(
    request_id: Annotated[str, Path(description="run-stream 返回的 request_id")],
) -> dict:
    trace = service.get_trace(request_id)
    if trace is None:
        raise HTTPException(status_code=404, detail="trace not found")
    return trace


@router.get(
    "/trace/{request_id}",
    summary="查询 analysis_agent trace",
)
async def get_analysis_agent_trace(
    request_id: str = Path(..., description="run-stream 返回的 request_id"),
) -> dict:
    trace = service.get_trace(request_id)
    if trace is None:
        raise HTTPException(status_code=404, detail="trace not found")
    return trace
