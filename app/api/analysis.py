from __future__ import annotations

"""
综合分析 HTTP 接口（企业版 V2）。

职责：
    - 提供企业级双入口：payload / nl2sql；
    - 提供 trace 回放、统计、趋势、降级 TopN 运维接口。

鉴权与身份：
    - 请求头须携带 `Authorization: Bearer <SERVICE_API_KEY>`（密钥生成与配置见 `app/auth/keygen.py` 与
      `app/app-deploy/README-simple-deploy.md`「Service API Key」）；
    - `user_id`、`session_id` 由调用方在请求体传入，用于会话记录与 Prompt 分流。
"""

from fastapi import APIRouter, HTTPException

from app.models.analysis import (
    AnalysisNL2SQLRequest,
    AnalysisPayloadRequest,
    AnalysisTraceDegradeTopNResponse,
    AnalysisTraceListItem,
    AnalysisTraceListResponse,
    AnalysisTraceStatsResponse,
    AnalysisTraceTrendResponse,
    AnalysisTraceView,
    AnalysisV2Result,
)
from app.services.analysis_service import AnalysisService

router = APIRouter()
service = AnalysisService()


@router.post("/run-with-payload", response_model=AnalysisV2Result, summary="综合分析执行（payload 模式）")
async def run_analysis_with_payload(data: AnalysisPayloadRequest) -> AnalysisV2Result:
    """
    综合分析 V2 入口（payload 模式）：
    - 由调用方直接传入分析数据载荷；
    - 内部走 `AnalysisGraphRunner`（LangGraph `StateGraph`，不可用时顺序回退），输出结构化报告与证据信息。
    """
    return await service.run_analysis_payload(data)


@router.post("/run-with-nl2sql", response_model=AnalysisV2Result, summary="综合分析执行（NL2SQL 模式）")
async def run_analysis_with_nl2sql(data: AnalysisNL2SQLRequest) -> AnalysisV2Result:
    """
    综合分析 V2 入口（nl2sql 模式）：
    - 由系统根据分析需求多次调用 NL2SQL 获取数据；
    - 计划阶段可选 LLM 意图/数据计划（与 `analysis_plan_*` 模板合并），再结合 RAG 与模板生成结构化报告。
    """
    return await service.run_analysis_nl2sql(data)


@router.get("/traces/{request_id}", response_model=AnalysisTraceView, summary="查询综合分析执行 trace")
async def get_analysis_trace(request_id: str) -> AnalysisTraceView:
    """
    查询已执行综合分析请求的回放信息（具体存储依赖 `ANALYSIS_TRACE_BACKEND`：Redis/ES/内存）。
    """
    hit = service.get_trace(request_id)
    if hit is None:
        raise HTTPException(status_code=404, detail=f"analysis trace not found: {request_id}")
    return hit


@router.get("/traces", response_model=AnalysisTraceListResponse, summary="分页查询综合分析 trace 列表")
async def list_analysis_traces(
    limit: int = 20,
    offset: int = 0,
    analysis_type: str | None = None,
    data_mode: str | None = None,
    request_id_like: str | None = None,
    started_from: str | None = None,
    started_to: str | None = None,
) -> AnalysisTraceListResponse:
    """分页列出历史 trace；`request_id_like` 为内存侧子串过滤。"""
    items, total = service.list_traces(
        limit=limit,
        offset=offset,
        analysis_type=analysis_type,
        data_mode=data_mode,
        request_id_like=request_id_like,
        started_from=started_from,
        started_to=started_to,
    )
    rows = [
        AnalysisTraceListItem(
            request_id=x.request_id,
            analysis_type=x.analysis_type,
            data_mode=x.data_mode,
            summary_preview=x.summary[:120],
            created_at=str(x.trace.execution_summary.get("started_at", "")),
            used_rag=bool(x.trace.execution_summary.get("used_rag", False)),
        )
        for x in items
    ]
    return AnalysisTraceListResponse(ok=True, limit=limit, offset=offset, total=total, items=rows)


@router.get("/traces/stats", response_model=AnalysisTraceStatsResponse, summary="综合分析 trace 聚合统计")
async def get_analysis_trace_stats(
    analysis_type: str | None = None,
    data_mode: str | None = None,
    started_from: str | None = None,
    started_to: str | None = None,
) -> AnalysisTraceStatsResponse:
    return service.get_trace_stats(
        analysis_type=analysis_type,
        data_mode=data_mode,
        started_from=started_from,
        started_to=started_to,
    )


@router.get("/traces/trend", response_model=AnalysisTraceTrendResponse, summary="综合分析 trace 时间趋势统计")
async def get_analysis_trace_trend(
    bucket: str = "hour",
    analysis_type: str | None = None,
    data_mode: str | None = None,
    started_from: str | None = None,
    started_to: str | None = None,
) -> AnalysisTraceTrendResponse:
    """按分钟或小时桶统计各模式 trace 量（服务层带短 TTL 缓存）。"""
    return service.get_trace_trend(
        bucket=bucket,
        analysis_type=analysis_type,
        data_mode=data_mode,
        started_from=started_from,
        started_to=started_to,
    )


@router.get("/traces/degrade-topn", response_model=AnalysisTraceDegradeTopNResponse, summary="综合分析 trace 降级原因 TopN")
async def get_analysis_trace_degrade_topn(
    top_n: int = 10,
    analysis_type: str | None = None,
    data_mode: str | None = None,
    started_from: str | None = None,
    started_to: str | None = None,
) -> AnalysisTraceDegradeTopNResponse:
    """返回降级原因出现次数 TopN（默认最多 50 条原因槽位）。"""
    return service.get_degrade_topn(
        top_n=top_n,
        analysis_type=analysis_type,
        data_mode=data_mode,
        started_from=started_from,
        started_to=started_to,
    )
