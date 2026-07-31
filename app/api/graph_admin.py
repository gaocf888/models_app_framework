from __future__ import annotations

"""
知识图谱运维 API（/graph/*），风格对齐 /rag/*。

默认 GRAPH_RAG_ENABLED=false 时所有接口返回 503。
"""

from functools import lru_cache
from typing import Annotated, Any, Literal

from fastapi import APIRouter, HTTPException, Path, Query
from pydantic import BaseModel, Field

from app.core.config import get_app_config
from app.core.logging import get_logger
from app.graph.admin_service import GraphAdminService
from app.graph.rebuild_jobs import GraphRebuildJobRunner

router = APIRouter()
logger = get_logger(__name__)


def _require_graph_enabled() -> GraphAdminService:
    cfg = get_app_config().rag.graph  # type: ignore[attr-defined]
    if not cfg.enabled:
        raise HTTPException(
            status_code=503,
            detail=(
                "GraphRAG is disabled (GRAPH_RAG_ENABLED=false). "
                "Deploy Neo4j via graphrag_db-deploy, set NEO4J_* and GRAPH_RAG_ENABLED=true."
            ),
        )
    return _get_admin_service()


@lru_cache(maxsize=1)
def _get_admin_service() -> GraphAdminService:
    return GraphAdminService()


class GraphHealthResponse(BaseModel):
    ok: bool
    enabled: bool = False
    neo4j_uri: str | None = None
    ingest_on_rag: bool = False
    delete_on_rag: bool = False
    extraction_mode: str | None = None
    detail: dict[str, Any] | None = None
    reason: str | None = None


class GraphStatsResponse(BaseModel):
    ok: bool = True
    stats: dict[str, Any]


class GraphSchemaResponse(BaseModel):
    ok: bool = True
    schema_info: dict[str, Any]


class GraphRebuildRequest(BaseModel):
    mode: Literal["full", "incremental"] = "incremental"
    namespace: str | None = None
    doc_names: list[str] = Field(default_factory=list)
    async_mode: bool = Field(
        default=True,
        description="为 true 时提交后台 rebuild 任务并返回 job_id；为 false 时同步执行（小数据量调试）",
    )


class GraphRebuildResponse(BaseModel):
    ok: bool = True
    async_mode: bool = False
    job_id: str | None = None
    status: str | None = None
    result: dict[str, Any] | None = None


class GraphRebuildJobInfo(BaseModel):
    job_id: str
    status: str
    mode: str
    namespace: str | None = None
    doc_names: list[str] = Field(default_factory=list)
    created_at: str
    updated_at: str
    result: dict[str, Any] | None = None
    error_message: str | None = None
    metrics: dict[str, Any] = Field(default_factory=dict)


class GraphRebuildJobGetResponse(BaseModel):
    ok: bool = True
    job: GraphRebuildJobInfo


class GraphRebuildJobListResponse(BaseModel):
    ok: bool = True
    jobs: list[GraphRebuildJobInfo]


class GraphDeleteDocumentResponse(BaseModel):
    ok: bool = True
    result: dict[str, Any]


class GraphDebugQueryRequest(BaseModel):
    question: str = Field(..., min_length=1)
    namespace: str | None = None


class GraphDebugQueryResponse(BaseModel):
    ok: bool = True
    result: dict[str, Any]


@router.get(
    "/health",
    summary="知识图谱健康检查",
    response_model=GraphHealthResponse,
)
async def graph_health() -> GraphHealthResponse:
    svc = GraphAdminService()
    data = svc.health()
    if not data.get("enabled"):
        return GraphHealthResponse(
            ok=False,
            enabled=False,
            reason=str(data.get("reason") or "GraphRAG disabled"),
        )
    return GraphHealthResponse(
        ok=bool(data.get("ok")),
        enabled=True,
        neo4j_uri=data.get("neo4j_uri"),
        ingest_on_rag=bool(data.get("ingest_on_rag")),
        delete_on_rag=bool(data.get("delete_on_rag")),
        extraction_mode=data.get("extraction_mode"),
        detail=data.get("detail") if isinstance(data.get("detail"), dict) else None,
    )


@router.get(
    "/stats",
    summary="知识图谱统计",
    response_model=GraphStatsResponse,
)
async def graph_stats(
    namespace: Annotated[str | None, Query(description="可选 namespace 过滤")] = None,
) -> GraphStatsResponse:
    svc = _require_graph_enabled()
    try:
        return GraphStatsResponse(ok=True, stats=svc.stats(namespace=namespace))
    except Exception as e:  # noqa: BLE001
        logger.exception("graph stats failed")
        raise HTTPException(status_code=500, detail=f"graph stats failed: {e}") from e


@router.get(
    "/schema",
    summary="查看当前 Graph Schema",
    response_model=GraphSchemaResponse,
)
async def graph_get_schema() -> GraphSchemaResponse:
    svc = _require_graph_enabled()
    return GraphSchemaResponse(ok=True, schema_info=svc.get_schema())


@router.post(
    "/schema/reload",
    summary="热加载 Graph Schema（需 GRAPH_SCHEMA_HOT_RELOAD=true）",
    response_model=GraphSchemaResponse,
)
async def graph_reload_schema() -> GraphSchemaResponse:
    svc = _require_graph_enabled()
    try:
        summary = svc.reload_schema()
        return GraphSchemaResponse(
            ok=True,
            schema_info={
                "reloaded": True,
                "schema": summary,
                **svc.get_schema(),
            },
        )
    except RuntimeError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except Exception as e:  # noqa: BLE001
        logger.exception("graph schema reload failed")
        raise HTTPException(status_code=500, detail=f"graph schema reload failed: {e}") from e


def _job_to_info(job) -> GraphRebuildJobInfo:
    return GraphRebuildJobInfo(
        job_id=job.job_id,
        status=job.status.value if hasattr(job.status, "value") else str(job.status),
        mode=job.mode,
        namespace=job.namespace,
        doc_names=list(job.doc_names or []),
        created_at=job.created_at,
        updated_at=job.updated_at,
        result=job.result,
        error_message=job.error_message,
        metrics=dict(job.metrics or {}),
    )


@router.post(
    "/rebuild",
    summary="全量/增量重建图数据（默认异步；从向量库拉 chunk 后 LLM 抽图）",
    response_model=GraphRebuildResponse,
)
async def graph_rebuild(req: GraphRebuildRequest) -> GraphRebuildResponse:
    _require_graph_enabled()
    try:
        if req.mode == "incremental" and not req.doc_names:
            raise HTTPException(status_code=400, detail="incremental rebuild requires doc_names")
        if req.async_mode:
            job = GraphRebuildJobRunner.get_default().submit(
                mode=req.mode,
                namespace=req.namespace,
                doc_names=req.doc_names,
            )
            return GraphRebuildResponse(
                ok=True,
                async_mode=True,
                job_id=job.job_id,
                status=job.status.value,
                result={"message": "graph rebuild job accepted"},
            )
        svc = _get_admin_service()
        result = svc.rebuild(mode=req.mode, namespace=req.namespace, doc_names=req.doc_names or None)
        return GraphRebuildResponse(ok=True, async_mode=False, result=result)
    except HTTPException:
        raise
    except Exception as e:  # noqa: BLE001
        logger.exception("graph rebuild failed")
        raise HTTPException(status_code=500, detail=f"graph rebuild failed: {e}") from e


@router.get(
    "/jobs/{job_id}",
    summary="查询 Graph 重建任务状态",
    response_model=GraphRebuildJobGetResponse,
)
async def graph_get_rebuild_job(
    job_id: Annotated[str, Path(description="重建任务 ID")],
) -> GraphRebuildJobGetResponse:
    _require_graph_enabled()
    job = GraphRebuildJobRunner.get_default().get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail=f"graph rebuild job not found: {job_id}")
    return GraphRebuildJobGetResponse(ok=True, job=_job_to_info(job))


@router.get(
    "/jobs",
    summary="列出近期 Graph 重建任务",
    response_model=GraphRebuildJobListResponse,
)
async def graph_list_rebuild_jobs(
    limit: Annotated[int, Query(ge=1, le=100, description="返回条数上限")] = 20,
) -> GraphRebuildJobListResponse:
    _require_graph_enabled()
    jobs = GraphRebuildJobRunner.get_default().list_jobs(limit=limit)
    return GraphRebuildJobListResponse(ok=True, jobs=[_job_to_info(j) for j in jobs])


@router.delete(
    "/documents/{doc_name}",
    summary="删除图侧文档数据",
    response_model=GraphDeleteDocumentResponse,
)
async def graph_delete_document(
    doc_name: Annotated[str, Path(description="文档名")],
    namespace: Annotated[str | None, Query(description="namespace")] = None,
    doc_version: Annotated[str | None, Query(description="文档版本")] = None,
) -> GraphDeleteDocumentResponse:
    svc = _require_graph_enabled()
    try:
        result = svc.delete_document(doc_name=doc_name, namespace=namespace, doc_version=doc_version)
        return GraphDeleteDocumentResponse(ok=True, result=result)
    except Exception as e:  # noqa: BLE001
        logger.exception("graph delete document failed")
        raise HTTPException(status_code=500, detail=f"graph delete document failed: {e}") from e


@router.post(
    "/query/debug",
    summary="调试图查询（返回图事实预览）",
    response_model=GraphDebugQueryResponse,
)
async def graph_debug_query(req: GraphDebugQueryRequest) -> GraphDebugQueryResponse:
    svc = _require_graph_enabled()
    try:
        result = svc.debug_query(question=req.question, namespace=req.namespace)
        return GraphDebugQueryResponse(ok=True, result=result)
    except Exception as e:  # noqa: BLE001
        logger.exception("graph debug query failed")
        raise HTTPException(status_code=500, detail=f"graph debug query failed: {e}") from e


@router.get(
    "/traces/{job_id}",
    summary="查询 Graph 重建任务执行轨迹（统一 Store 别名）",
)
async def get_graph_rebuild_trace(job_id: str):
    from app.api.trace_aliases import get_module_trace

    return get_module_trace(job_id, expected_module="graph_rebuild")
