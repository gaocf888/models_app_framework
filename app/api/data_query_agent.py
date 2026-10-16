from __future__ import annotations

"""
数据查询智能体 HTTP 接口（data_query_agent）。

入口：`/data-query-agent/*`
开关关闭时返回 503（与 inspection_extract_v0 一致，避免误判 404）。
"""

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from typing import Annotated, Any

from app.core.config import get_app_config
from app.data_query_agent.browse import BrowseRequestError
from app.data_query_agent.catalog import CatalogError, get_library_catalog
from app.data_query_agent.hud import HudRequestError
from app.models.data_query_agent import (
    DataQueryAgentBrowseResponse,
    DataQueryAgentHudResponse,
    DataQueryAgentLibrariesResponse,
    DataQueryAgentResumeRequest,
    DataQueryAgentRunRequest,
    DataQueryAgentStreamStopRequest,
    DataQueryAgentStreamStopResponse,
    DataQueryAgentTraceListResponse,
    DataQueryAgentTraceStatsResponse,
    DataQueryAgentTreeResponse,
)
from app.services.data_query_agent_service import DataQueryAgentService


def require_data_query_agent_enabled() -> None:
    """开关关闭返回 503，与其它智能体一致，避免前端当成接口不存在。"""
    if not get_app_config().data_query_agent.enabled:
        raise HTTPException(
            status_code=503,
            detail="DATA_QUERY_AGENT_ENABLED is not true; set it to true and restart to use /data-query-agent/*.",
        )


router = APIRouter(dependencies=[Depends(require_data_query_agent_enabled)])
service = DataQueryAgentService()


@router.get(
    "/libraries",
    response_model=DataQueryAgentLibrariesResponse,
    summary="库注册表（HITL 选项与树节点 ID 同源）",
)
async def list_libraries() -> DataQueryAgentLibrariesResponse:
    try:
        catalog = get_library_catalog()
    except CatalogError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    payload = catalog.public_payload()
    return DataQueryAgentLibrariesResponse(ok=True, **payload)


@router.post(
    "/run-stream",
    summary="数据查询智能体流式执行",
    response_description="SSE：started → data_query_* → finished / HITL 中断",
)
async def run_data_query_stream(data: DataQueryAgentRunRequest) -> StreamingResponse:
    return await service.run_stream(data)


@router.post(
    "/resume-stream",
    summary="选库后续流（SSE）",
    response_description="从 library HITL 断点继续：library_hit → scope → result",
)
async def resume_data_query_stream(data: DataQueryAgentResumeRequest) -> StreamingResponse:
    return await service.resume_stream(data)


@router.post(
    "/stream/stop",
    response_model=DataQueryAgentStreamStopResponse,
    summary="中断指定数据查询流式任务",
)
async def stop_data_query_stream(
    data: DataQueryAgentStreamStopRequest,
) -> DataQueryAgentStreamStopResponse:
    return await service.stop_stream(data.user_id, data.session_id, data.stream_id)


@router.get(
    "/traces",
    response_model=DataQueryAgentTraceListResponse,
    summary="分页查询 data_query_agent trace（独立前缀，不混入 analysis）",
)
async def list_data_query_traces(
    limit: Annotated[int, Query(ge=1, le=200)] = 20,
    offset: Annotated[int, Query(ge=0)] = 0,
    library_id: Annotated[str | None, Query()] = None,
    user_id: Annotated[str | None, Query()] = None,
    request_id_like: Annotated[str | None, Query()] = None,
) -> DataQueryAgentTraceListResponse:
    return service.list_traces(
        limit=limit,
        offset=offset,
        library_id=library_id,
        user_id=user_id,
        request_id_like=request_id_like,
    )


@router.get(
    "/traces/stats",
    response_model=DataQueryAgentTraceStatsResponse,
    summary="data_query_agent trace 聚合",
)
async def get_data_query_trace_stats(
    library_id: Annotated[str | None, Query()] = None,
    user_id: Annotated[str | None, Query()] = None,
) -> DataQueryAgentTraceStatsResponse:
    return service.get_trace_stats(library_id=library_id, user_id=user_id)


@router.get("/trace/{request_id}", summary="单条 data_query_agent trace")
async def get_data_query_trace(request_id: str) -> dict[str, Any]:
    rec = service.get_trace(request_id)
    if rec is None:
        raise HTTPException(status_code=404, detail="trace not found")
    return rec


@router.get("/traces/{request_id}", summary="单条 data_query_agent trace（别名）")
async def get_data_query_trace_alias(request_id: str) -> dict[str, Any]:
    return await get_data_query_trace(request_id)


@router.get(
    "/hud",
    response_model=DataQueryAgentHudResponse,
    summary="按实体补拉 HUD（浏览表点行；解析路径仍用 run-stream 一组 payload）",
)
async def get_data_query_hud(
    entity_type: Annotated[str, Query(description="station | district | city")],
    entity_id: Annotated[str, Query(description="站=station_id，区=area，市=beijing")],
    library_id: Annotated[str, Query(description="监测库，与树节点 / 行上 library_id 一致")],
    user_id: Annotated[str | None, Query()] = None,
    session_id: Annotated[str | None, Query()] = None,
    expose_sql: Annotated[bool, Query(description="联调时返回 sql")] = False,
) -> DataQueryAgentHudResponse:
    try:
        return await service.get_hud(
            library_id=library_id,
            entity_type=entity_type,
            entity_id=entity_id,
            user_id=user_id,
            session_id=session_id,
            expose_sql=expose_sql,
        )
    except HudRequestError as exc:
        raise HTTPException(
            status_code=exc.status_code,
            detail={"code": exc.code, "message": exc.message},
        ) from exc


def _browse_http_error(exc: BrowseRequestError) -> HTTPException:
    return HTTPException(
        status_code=exc.status_code,
        detail={"code": exc.code, "message": exc.message},
    )


@router.get(
    "/tree",
    response_model=DataQueryAgentTreeResponse,
    summary="感知树：监测库 → 行政区 → 站点（yaml 覆盖 + t_station 出区）",
)
async def get_data_query_tree(
    library_id: Annotated[str | None, Query(description="只返回该库子树；空=七库全部")] = None,
) -> DataQueryAgentTreeResponse:
    try:
        return await service.get_tree(library_id=library_id)
    except BrowseRequestError as exc:
        raise _browse_http_error(exc) from exc


@router.get(
    "/browse",
    response_model=DataQueryAgentBrowseResponse,
    summary="浏览列表：进页默认全北京分层标 / 点树刷新；每站最新一条，强制分页",
)
async def browse_data_query_list(
    library_id: Annotated[
        str | None,
        Query(description="监测库；空=默认分层标 fcb"),
    ] = None,
    district: Annotated[str | None, Query(description="行政区标准名，如 朝阳区")] = None,
    station_id: Annotated[
        str | None,
        Query(description="站点=project_name，与树节点 / HUD entity_id 一致"),
    ] = None,
    page: Annotated[int, Query(ge=1, description="页码，从 1 起")] = 1,
    page_size: Annotated[int, Query(ge=1, le=100, description="每页行数")] = 20,
) -> DataQueryAgentBrowseResponse:
    try:
        return await service.browse_list(
            library_id=library_id,
            district=district,
            station_id=station_id,
            page=page,
            page_size=page_size,
        )
    except BrowseRequestError as exc:
        raise _browse_http_error(exc) from exc
