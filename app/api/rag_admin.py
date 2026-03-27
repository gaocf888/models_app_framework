from __future__ import annotations

"""
RAG 管理接口（对应《下一阶段工作清单》中的 TODO-P6）。

说明：
- 提供文本摄入、批量摄入、按文档删除、检索查询、数据集列表查询等管理能力；
- 摄入支持 doc_name + replace_if_exists，实现同名文档更新（先删后灌）；
- 同时支持“原始文档内容”摄入（自动执行清洗与切块）；
- 异常路径统一记录错误日志并返回明确 HTTP 错误信息。

服务配置前置条件（运维/开发必读）：
1) 向量与全文检索库
   - 默认 ES/EasySearch：需配置 RAG_VECTOR_STORE_TYPE=es（或 easysearch）；
   - 连接参数：RAG_ES_HOSTS、RAG_ES_USERNAME/RAG_ES_PASSWORD（或 RAG_ES_API_KEY）；
   - 索引参数：RAG_ES_INDEX_*、RAG_ES_DOCS_INDEX_*、RAG_ES_JOBS_INDEX_*。
2) 嵌入模型
   - 需可加载 EMBEDDING_MODEL_PATH（离线）或 EMBEDDING_MODEL_NAME（在线下载）。
3) 可选 GraphRAG
   - 若启用 GRAPH_RAG_ENABLED=true，需配置 NEO4J_URI/NEO4J_USERNAME/NEO4J_PASSWORD。
4) 摄入切块/清洗默认参数
   - 可通过 RAG_CHUNK_SIZE/RAG_CHUNK_OVERLAP/RAG_MIN_CHUNK_SIZE 与 RAG_CLEANING_PROFILE 调整。
"""

from functools import lru_cache
from typing import Any, List

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.core.config import get_app_config
from app.rag.ingestion import RAGDatasetMeta, RAGIngestionService
from app.rag.document_repository import DocumentRepository
from app.rag.document_pipeline import ChunkingConfig, DocumentPipeline
from app.rag.ingestion_orchestrator import IngestionOrchestrator
from app.rag.job_repository import JobRepository
from app.rag.migrations import IndexMigrator
from app.rag.models import DocumentSource
from app.core.logging import get_logger

router = APIRouter()
logger = get_logger(__name__)


@lru_cache(maxsize=1)
def _get_service() -> RAGIngestionService:
    return RAGIngestionService()


@lru_cache(maxsize=1)
def _get_orchestrator() -> IngestionOrchestrator:
    return IngestionOrchestrator(ingestion_service=_get_service())


@lru_cache(maxsize=1)
def _get_job_repo() -> JobRepository:
    return JobRepository()


@lru_cache(maxsize=1)
def _get_doc_repo() -> DocumentRepository:
    return DocumentRepository()


class IngestTextsRequest(BaseModel):
    dataset_id: str = Field(..., description="数据集标识")
    description: str | None = Field(None, description="数据集描述")
    texts: List[str] = Field(..., description="要摄入的文本列表")
    namespace: str | None = Field(
        None,
        description="可选命名空间，例如 nl2sql_schema/nl2sql_biz_knowledge/nl2sql_qa_examples",
    )
    doc_name: str | None = Field(None, description="文档名称，用于后续同名更新（先删后灌）")
    replace_if_exists: bool = Field(True, description="同名文档是否先全量删除再重建")


@router.post("/ingest/texts", summary="摄入文本到 RAG 知识库")
async def ingest_texts(req: IngestTextsRequest) -> dict:
    """
    摄入已分块文本。

    参数说明：
    - 必传：dataset_id、texts
    - 可选：description、namespace、doc_name、replace_if_exists（默认 true）
    """
    try:
        _get_service().ingest_texts(
            req.dataset_id,
            req.texts,
            description=req.description,
            namespace=req.namespace,
            doc_name=req.doc_name,
            replace_if_exists=req.replace_if_exists,
        )
        return {"ok": True}
    except Exception as e:  # noqa: BLE001
        logger.exception("rag ingest_texts failed: dataset_id=%s doc_name=%s", req.dataset_id, req.doc_name)
        raise HTTPException(status_code=500, detail=f"RAG ingest_texts failed: {e}") from e


class IngestDocumentItem(BaseModel):
    dataset_id: str = Field(..., description="数据集标识")
    texts: List[str] = Field(..., description="要摄入的文本列表")
    description: str | None = Field(None, description="数据集描述")
    namespace: str | None = Field(None, description="命名空间")
    doc_name: str | None = Field(None, description="文档名称")
    replace_if_exists: bool = Field(True, description="同名文档是否先删除后重建")


class IngestDocumentsRequest(BaseModel):
    documents: List[IngestDocumentItem] = Field(..., description="批量摄入文档列表")


@router.post("/ingest/documents", summary="批量摄入多个文档到 RAG 知识库")
async def ingest_documents(req: IngestDocumentsRequest) -> dict:
    """
    批量摄入已分块文本。

    参数说明：
    - 必传：documents[]，且每项必须有 dataset_id、texts
    - 可选：description、namespace、doc_name、replace_if_exists
    """
    try:
        total_docs = 0
        total_chunks = 0
        for doc in req.documents:
            _get_service().ingest_texts(
                doc.dataset_id,
                doc.texts,
                description=doc.description,
                namespace=doc.namespace,
                doc_name=doc.doc_name,
                replace_if_exists=doc.replace_if_exists,
            )
            total_docs += 1
            total_chunks += len(doc.texts)
        return {"ok": True, "documents": total_docs, "chunks": total_chunks}
    except Exception as e:  # noqa: BLE001
        logger.exception("rag ingest_documents failed")
        raise HTTPException(status_code=500, detail=f"RAG ingest_documents failed: {e}") from e


class IngestRawDocumentRequest(BaseModel):
    dataset_id: str = Field(..., description="数据集标识，可以作为数据集分类标签")
    doc_name: str = Field(..., description="文档名称（更新主键）")
    content: str = Field(..., description="原始文档文本")
    description: str | None = Field(None, description="数据集描述")
    namespace: str | None = Field(None, description="命名空间")
    replace_if_exists: bool = Field(True, description="同名文档是否先删除后重建")
    chunk_size: int = Field(500, description="切块长度（字符）")
    chunk_overlap: int = Field(80, description="切块重叠长度（字符）")
    min_chunk_size: int = Field(40, description="最小切块长度（字符）")


class IngestRawDocumentsRequest(BaseModel):
    documents: List[IngestRawDocumentRequest] = Field(..., description="批量原始文档")


@router.post("/ingest/raw_document", summary="摄入原始文档（自动清洗与切块）")
async def ingest_raw_document(req: IngestRawDocumentRequest) -> dict:
    """
    摄入原始文档（接口内自动清洗 + 切块 + 入库）。

    参数说明：
    - 必传：dataset_id、doc_name、content
    - 可选：namespace、description、replace_if_exists、chunk_size/chunk_overlap/min_chunk_size
    - 默认切块参数：500/80/40（不传时自动使用）
    """
    try:
        pipeline = DocumentPipeline(
            ChunkingConfig(
                chunk_size=req.chunk_size,
                chunk_overlap=req.chunk_overlap,
                min_chunk_size=req.min_chunk_size,
            )
        )
        chunks = pipeline.process(req.content)
        if not chunks:
            raise ValueError("document content is empty after normalization/chunking")
        _get_service().ingest_texts(
            req.dataset_id,
            chunks,
            description=req.description,
            namespace=req.namespace,
            doc_name=req.doc_name,
            replace_if_exists=req.replace_if_exists,
        )
        return {
            "ok": True,
            "dataset_id": req.dataset_id,
            "doc_name": req.doc_name,
            "chunk_count": len(chunks),
        }
    except Exception as e:  # noqa: BLE001
        logger.exception("rag ingest_raw_document failed: dataset_id=%s doc_name=%s", req.dataset_id, req.doc_name)
        raise HTTPException(status_code=500, detail=f"RAG ingest_raw_document failed: {e}") from e


@router.post("/ingest/raw_documents", summary="批量摄入原始文档（自动清洗与切块）")
async def ingest_raw_documents(req: IngestRawDocumentsRequest) -> dict:
    """
    批量摄入原始文档（每个文档自动清洗 + 切块 + 入库）。

    参数说明：
    - 必传：documents[]，且每项必须有 dataset_id、doc_name、content
    - 可选：namespace、description、replace_if_exists、chunk_size/chunk_overlap/min_chunk_size
    """
    try:
        total_docs = 0
        total_chunks = 0
        for doc in req.documents:
            pipeline = DocumentPipeline(
                ChunkingConfig(
                    chunk_size=doc.chunk_size,
                    chunk_overlap=doc.chunk_overlap,
                    min_chunk_size=doc.min_chunk_size,
                )
            )
            chunks = pipeline.process(doc.content)
            if not chunks:
                raise ValueError(f"document is empty after processing: doc_name={doc.doc_name}")
            _get_service().ingest_texts(
                doc.dataset_id,
                chunks,
                description=doc.description,
                namespace=doc.namespace,
                doc_name=doc.doc_name,
                replace_if_exists=doc.replace_if_exists,
            )
            total_docs += 1
            total_chunks += len(chunks)
        return {"ok": True, "documents": total_docs, "chunks": total_chunks}
    except Exception as e:  # noqa: BLE001
        logger.exception("rag ingest_raw_documents failed")
        raise HTTPException(status_code=500, detail=f"RAG ingest_raw_documents failed: {e}") from e


class IngestionJobDocumentRequest(BaseModel):
    dataset_id: str = Field(..., description="数据集标识")
    doc_name: str = Field(..., description="文档名称")
    doc_version: str = Field("v1", description="文档版本")
    tenant_id: str | None = Field(None, description="租户标识")
    namespace: str | None = Field(None, description="命名空间")
    content: str = Field(..., description="原始文档内容")
    source_type: str = Field("text", description="文档类型：text/markdown/html/pdf/docx")
    source_uri: str | None = Field(None, description="源地址")
    description: str | None = Field(None, description="文档描述")
    replace_if_exists: bool = Field(True, description="同名文档是否先删除后重建")
    metadata: dict[str, Any] = Field(default_factory=dict, description="扩展元数据")


class IngestionJobRequest(BaseModel):
    documents: List[IngestionJobDocumentRequest] = Field(..., description="待摄入文档列表")
    operator: str | None = Field(None, description="操作人")
    idempotency_key: str | None = Field(None, description="可选幂等键；相同键的运行中任务会复用")
    chunk_size: int = Field(500, description="切块长度")
    chunk_overlap: int = Field(80, description="切块重叠")
    min_chunk_size: int = Field(40, description="最小切块长度")


class IngestionJobInfo(BaseModel):
    job_id: str
    job_type: str = "upsert"
    idempotency_key: str | None = None
    status: str
    step: str
    created_at: str
    updated_at: str
    finished_at: str | None = None
    error_code: str | None = None
    error_message: str | None = None
    metrics: dict[str, Any] = Field(default_factory=dict)
    operator: str | None = None


class IngestionJobSubmitResponse(BaseModel):
    ok: bool = True
    job_id: str


class IngestionJobGetResponse(BaseModel):
    ok: bool = True
    job: IngestionJobInfo


class IngestionJobListResponse(BaseModel):
    ok: bool = True
    total: int
    limit: int
    offset: int
    jobs: List[IngestionJobInfo]


class JobDocumentItem(BaseModel):
    dataset_id: str
    doc_name: str
    doc_version: str = "v1"
    tenant_id: str | None = None
    namespace: str | None = None
    source_type: str = "text"
    source_uri: str | None = None
    description: str | None = None
    replace_if_exists: bool = True
    metadata: dict[str, Any] = Field(default_factory=dict)


class JobDocumentsResponse(BaseModel):
    ok: bool = True
    job_id: str
    documents: List[JobDocumentItem]


@router.post("/jobs/ingest", summary="提交异步摄入任务")
async def submit_ingestion_job(req: IngestionJobRequest) -> IngestionJobSubmitResponse:
    """
    提交异步摄入任务（企业运维推荐入口）。

    参数说明：
    - 必传：documents[]，每项需 dataset_id、doc_name、content
    - 可选：doc_version（默认 v1）、tenant_id、namespace、source_type、source_uri、metadata
    - 任务级可选：operator、idempotency_key、chunk_size/chunk_overlap/min_chunk_size
    """
    try:
        docs = [
            DocumentSource(
                dataset_id=d.dataset_id,
                doc_name=d.doc_name,
                doc_version=d.doc_version,
                tenant_id=d.tenant_id,
                namespace=d.namespace,
                content=d.content,
                source_type=d.source_type,
                source_uri=d.source_uri,
                description=d.description,
                replace_if_exists=d.replace_if_exists,
                metadata=d.metadata,
            )
            for d in req.documents
        ]
        chunk_cfg = ChunkingConfig(
            chunk_size=req.chunk_size,
            chunk_overlap=req.chunk_overlap,
            min_chunk_size=req.min_chunk_size,
        )
        job_id = _get_orchestrator().submit_job(
            documents=docs,
            operator=req.operator,
            chunk_cfg=chunk_cfg,
            idempotency_key=req.idempotency_key,
        )
        return IngestionJobSubmitResponse(ok=True, job_id=job_id)
    except Exception as e:  # noqa: BLE001
        logger.exception("rag submit_ingestion_job failed")
        raise HTTPException(status_code=500, detail=f"RAG submit_ingestion_job failed: {e}") from e


@router.get("/jobs/{job_id}", summary="查询摄入任务状态", response_model=IngestionJobGetResponse)
async def get_ingestion_job(job_id: str) -> IngestionJobGetResponse:
    """
    查询任务状态与步骤信息。

    参数说明：
    - 必传：job_id（路径参数）
    """
    try:
        job = _get_orchestrator().get_job(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail=f"job not found: {job_id}")
        return IngestionJobGetResponse(
            ok=True,
            job=IngestionJobInfo(
                job_id=job.job_id,
                job_type=job.job_type.value,
                idempotency_key=job.idempotency_key,
                status=job.status.value,
                step=job.step,
                created_at=job.created_at,
                updated_at=job.updated_at,
                finished_at=job.finished_at,
                error_code=job.error_code,
                error_message=job.error_message,
                metrics=job.metrics,
                operator=job.operator,
            ),
        )
    except HTTPException:
        raise
    except Exception as e:  # noqa: BLE001
        logger.exception("rag get_ingestion_job failed: %s", job_id)
        raise HTTPException(status_code=500, detail=f"RAG get_ingestion_job failed: {e}") from e


@router.post("/jobs/{job_id}/retry", summary="重试摄入任务")
async def retry_ingestion_job(job_id: str) -> dict:
    """
    重试指定任务。

    参数说明：
    - 必传：job_id（路径参数）
    """
    try:
        new_job_id = _get_orchestrator().retry_job(job_id)
        return {"ok": True, "job_id": new_job_id, "retry_of": job_id}
    except Exception as e:  # noqa: BLE001
        logger.exception("rag retry_ingestion_job failed: %s", job_id)
        raise HTTPException(status_code=500, detail=f"RAG retry_ingestion_job failed: {e}") from e


@router.get("/jobs", summary="分页查询摄入任务", response_model=IngestionJobListResponse)
async def list_ingestion_jobs(limit: int = 20, offset: int = 0) -> IngestionJobListResponse:
    """
    分页查询任务列表。

    参数说明：
    - 可选：limit、offset（默认 20/0）
    """
    try:
        jobs = _get_orchestrator().list_jobs(limit=limit, offset=offset)
        infos = [
            IngestionJobInfo(
                job_id=j.job_id,
                job_type=j.job_type.value,
                idempotency_key=j.idempotency_key,
                status=j.status.value,
                step=j.step,
                created_at=j.created_at,
                updated_at=j.updated_at,
                finished_at=j.finished_at,
                error_code=j.error_code,
                error_message=j.error_message,
                metrics=j.metrics,
                operator=j.operator,
            )
            for j in jobs
        ]
        return IngestionJobListResponse(
            ok=True,
            total=_get_orchestrator().count_jobs(),
            limit=limit,
            offset=offset,
            jobs=infos,
        )
    except Exception as e:  # noqa: BLE001
        logger.exception("rag list_ingestion_jobs failed")
        raise HTTPException(status_code=500, detail=f"RAG list_ingestion_jobs failed: {e}") from e


@router.get("/jobs/{job_id}/documents", summary="查询任务关联文档", response_model=JobDocumentsResponse)
async def get_job_documents(job_id: str) -> JobDocumentsResponse:
    """
    查询任务关联文档。

    参数说明：
    - 必传：job_id（路径参数）
    """
    try:
        rec = _get_job_repo().get(job_id)
        if rec is None:
            raise HTTPException(status_code=404, detail=f"job not found: {job_id}")
        docs = rec.get("documents") or []
        items = [
            JobDocumentItem(
                dataset_id=d.get("dataset_id", ""),
                doc_name=d.get("doc_name", ""),
                doc_version=d.get("doc_version", "v1"),
                tenant_id=d.get("tenant_id"),
                namespace=d.get("namespace"),
                source_type=d.get("source_type", "text"),
                source_uri=d.get("source_uri"),
                description=d.get("description"),
                replace_if_exists=bool(d.get("replace_if_exists", True)),
                metadata=d.get("metadata") or {},
            )
            for d in docs
        ]
        return JobDocumentsResponse(ok=True, job_id=job_id, documents=items)
    except HTTPException:
        raise
    except Exception as e:  # noqa: BLE001
        logger.exception("rag get_job_documents failed: %s", job_id)
        raise HTTPException(status_code=500, detail=f"RAG get_job_documents failed: {e}") from e


class UpsertDocumentRequest(BaseModel):
    dataset_id: str = Field(..., description="数据集标识")
    doc_name: str = Field(..., description="文档名称")
    namespace: str | None = Field(None, description="命名空间")
    content: str = Field(..., description="原始文档内容")
    source_type: str = Field("text", description="文档类型")
    source_uri: str | None = Field(None, description="源地址")
    description: str | None = Field(None, description="文档描述")
    chunk_size: int = Field(500, description="切块长度")
    chunk_overlap: int = Field(80, description="切块重叠")
    min_chunk_size: int = Field(40, description="最小切块长度")
    metadata: dict[str, Any] = Field(default_factory=dict, description="扩展元数据")


@router.post("/documents/upsert", summary="同步 upsert 文档（自动清洗切块后立即入库）")
async def upsert_document(req: UpsertDocumentRequest) -> dict:
    """
    同步 upsert 文档（小批量快速修订入口）。

    参数说明：
    - 必传：dataset_id、doc_name、content
    - 可选：namespace、source_type、source_uri、description、metadata
    - 切块参数可选：chunk_size/chunk_overlap/min_chunk_size（默认 500/80/40）
    """
    try:
        cfg = ChunkingConfig(
            chunk_size=req.chunk_size,
            chunk_overlap=req.chunk_overlap,
            min_chunk_size=req.min_chunk_size,
        )
        pipeline = DocumentPipeline(cfg)
        doc = DocumentSource(
            dataset_id=req.dataset_id,
            doc_name=req.doc_name,
            namespace=req.namespace,
            content=req.content,
            source_type=req.source_type,
            source_uri=req.source_uri,
            description=req.description,
            replace_if_exists=True,
            metadata=req.metadata,
        )
        chunks, stats = pipeline.process_document(doc)
        if not chunks:
            raise ValueError("no chunks generated after processing")
        _get_service().ingest_texts(
            dataset_id=req.dataset_id,
            texts=[c.text for c in chunks],
            description=req.description,
            namespace=req.namespace,
            doc_name=req.doc_name,
            replace_if_exists=True,
        )
        return {"ok": True, "doc_name": req.doc_name, "chunk_count": len(chunks), "stats": stats}
    except Exception as e:  # noqa: BLE001
        logger.exception("rag upsert_document failed: doc_name=%s", req.doc_name)
        raise HTTPException(status_code=500, detail=f"RAG upsert_document failed: {e}") from e


class DeleteDocumentRequest(BaseModel):
    doc_name: str = Field(..., description="文档名称")
    namespace: str | None = Field(None, description="命名空间；为空则跨命名空间删除")
    doc_version: str | None = Field(None, description="可选文档版本；传入时按版本精确删除")


@router.post("/documents/delete", summary="按文档名删除已摄入知识")
async def delete_document(req: DeleteDocumentRequest) -> dict:
    """
    按文档删除知识（支持按版本精确删除）。

    参数说明：
    - 必传：doc_name
    - 可选：namespace、doc_version（传入则按版本删除）
    """
    try:
        deleted = _get_service().delete_by_doc_name(
            doc_name=req.doc_name, namespace=req.namespace, doc_version=req.doc_version
        )
        return {"ok": True, "deleted": deleted}
    except Exception as e:  # noqa: BLE001
        logger.exception("rag delete_document failed: doc_name=%s namespace=%s", req.doc_name, req.namespace)
        raise HTTPException(status_code=500, detail=f"RAG delete_document failed: {e}") from e


class QueryRequest(BaseModel):
    query: str = Field(..., description="检索问题")
    top_k: int | None = Field(None, description="返回条数")
    namespace: str | None = Field(None, description="命名空间")
    scene: str = Field("llm_inference", description="检索场景：llm_inference/chatbot/analysis/nl2sql")


@router.post("/query", summary="查询 RAG 知识库")
async def query_rag(req: QueryRequest) -> dict:
    """
    查询 RAG 知识库并返回上下文片段。

    参数说明：
    - 必传：query
    - 可选：top_k、namespace、scene（默认 llm_inference）
    """
    try:
        snippets = _get_service().query(
            query=req.query,
            top_k=req.top_k,
            namespace=req.namespace,
            scene=req.scene,
        )
        return {"ok": True, "query": req.query, "count": len(snippets), "snippets": snippets}
    except Exception as e:  # noqa: BLE001
        logger.exception("rag query failed: query=%s namespace=%s scene=%s", req.query, req.namespace, req.scene)
        raise HTTPException(status_code=500, detail=f"RAG query failed: {e}") from e


class DatasetMetaResponse(BaseModel):
    dataset_id: str
    description: str | None = None
    num_items: int
    namespace: str | None = None
    doc_name: str | None = None


@router.get("/datasets", response_model=List[DatasetMetaResponse], summary="列出已登记的 RAG 数据集")
async def list_datasets() -> List[DatasetMetaResponse]:
    """
    列出进程内已登记的数据集元信息。

    参数说明：
    - 无必传参数
    """
    metas: List[RAGDatasetMeta] = _get_service().list_datasets()
    return [
        DatasetMetaResponse(
            dataset_id=m.dataset_id,
            description=m.description,
            num_items=m.num_items,
            namespace=m.namespace,
            doc_name=m.doc_name,
        )
        for m in metas
    ]


class DocumentMetaItem(BaseModel):
    doc_name: str
    doc_version: str = "v1"
    tenant_id: str | None = None
    dataset_id: str
    namespace: str | None = None
    source_type: str = "text"
    source_uri: str | None = None
    chunk_count: int = 0
    pipeline_version: str | None = None
    status: str | None = None
    created_at: str | None = None
    updated_at: str | None = None
    last_job_id: str | None = None
    last_job_type: str | None = None
    last_job_status: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    error: str | None = None


class DocumentMetaListResponse(BaseModel):
    ok: bool = True
    limit: int
    offset: int
    namespace: str | None = None
    documents: List[DocumentMetaItem]


@router.get("/documents/meta", summary="分页查询文档元数据", response_model=DocumentMetaListResponse)
async def list_document_meta(
    limit: int = 20,
    offset: int = 0,
    namespace: str | None = None,
    tenant_id: str | None = None,
    dataset_id: str | None = None,
    doc_name: str | None = None,
) -> DocumentMetaListResponse:
    """
    分页查询文档元数据（管理面清单接口）。

    参数说明：
    - 可选：limit、offset、namespace、tenant_id、dataset_id、doc_name
    """
    try:
        repo = _get_doc_repo()
        try:
            docs = repo.list(
                limit=limit,
                offset=offset,
                namespace=namespace,
                tenant_id=tenant_id,
                dataset_id=dataset_id,
                doc_name=doc_name,
            )
        except TypeError:
            # 兼容旧测试桩/旧实现签名：list(limit, offset, namespace)
            docs = repo.list(limit=limit, offset=offset, namespace=namespace)
        items = [
            DocumentMetaItem(
                doc_name=d.get("doc_name", ""),
                doc_version=d.get("doc_version", "v1"),
                tenant_id=d.get("tenant_id"),
                dataset_id=d.get("dataset_id", ""),
                namespace=d.get("namespace"),
                source_type=d.get("source_type", "text"),
                source_uri=d.get("source_uri"),
                chunk_count=int(d.get("chunk_count", 0)),
                pipeline_version=d.get("pipeline_version"),
                status=d.get("status"),
                created_at=d.get("created_at"),
                updated_at=d.get("updated_at"),
                last_job_id=d.get("last_job_id"),
                last_job_type=d.get("last_job_type"),
                last_job_status=d.get("last_job_status"),
                metadata=d.get("metadata") or {},
                error=d.get("error"),
            )
            for d in docs
        ]
        return DocumentMetaListResponse(ok=True, limit=limit, offset=offset, namespace=namespace, documents=items)
    except Exception as e:  # noqa: BLE001
        logger.exception("rag list_document_meta failed")
        raise HTTPException(status_code=500, detail=f"RAG list_document_meta failed: {e}") from e


class OverviewBucketItem(BaseModel):
    key: str | None = None
    count: int = 0


class KnowledgeOverviewResponse(BaseModel):
    ok: bool = True
    namespace: str | None = None
    tenant_id: str | None = None
    dataset_id: str | None = None
    total_documents: int = 0
    total_doc_names: int = 0
    by_namespace: List[OverviewBucketItem] = Field(default_factory=list)
    by_tenant: List[OverviewBucketItem] = Field(default_factory=list)
    by_status: List[OverviewBucketItem] = Field(default_factory=list)
    documents: List[DocumentMetaItem] = Field(default_factory=list)


class KnowledgeTrendPoint(BaseModel):
    bucket: str = Field(..., description="时间桶，如 2026-03-27 或 2026-W13")
    created_success: int = Field(0, description="FULL 成功数量（新增）")
    updated_success: int = Field(0, description="UPSERT 成功数量（更新）")
    failed: int = Field(0, description="FAILED 数量（任意 job_type）")


class KnowledgeTrendsResponse(BaseModel):
    ok: bool = True
    granularity: str = Field("day", description="聚合粒度：day 或 week")
    days: int = Field(30, description="统计窗口天数")
    points: List[KnowledgeTrendPoint] = Field(default_factory=list)


@router.get("/documents/overview", summary="查询知识库整体情况（元数据总览）", response_model=KnowledgeOverviewResponse)
async def get_documents_overview(
    limit: int = 20,
    offset: int = 0,
    namespace: str | None = None,
    tenant_id: str | None = None,
    dataset_id: str | None = None,
    doc_name: str | None = None,
) -> KnowledgeOverviewResponse:
    """
    查询知识库总览（聚合统计 + 分页明细）。

    参数说明：
    - 可选：limit、offset、namespace、tenant_id、dataset_id、doc_name
    """
    try:
        repo = _get_doc_repo()
        try:
            stats = repo.overview(namespace=namespace, tenant_id=tenant_id, dataset_id=dataset_id)
        except TypeError:
            stats = repo.overview(namespace=namespace)
        try:
            docs = repo.list(
                limit=limit,
                offset=offset,
                namespace=namespace,
                tenant_id=tenant_id,
                dataset_id=dataset_id,
                doc_name=doc_name,
            )
        except TypeError:
            docs = repo.list(limit=limit, offset=offset, namespace=namespace)
        items = [
            DocumentMetaItem(
                doc_name=d.get("doc_name", ""),
                doc_version=d.get("doc_version", "v1"),
                tenant_id=d.get("tenant_id"),
                dataset_id=d.get("dataset_id", ""),
                namespace=d.get("namespace"),
                source_type=d.get("source_type", "text"),
                source_uri=d.get("source_uri"),
                chunk_count=int(d.get("chunk_count", 0)),
                pipeline_version=d.get("pipeline_version"),
                status=d.get("status"),
                created_at=d.get("created_at"),
                updated_at=d.get("updated_at"),
                last_job_id=d.get("last_job_id"),
                last_job_type=d.get("last_job_type"),
                last_job_status=d.get("last_job_status"),
                metadata=d.get("metadata") or {},
                error=d.get("error"),
            )
            for d in docs
        ]
        return KnowledgeOverviewResponse(
            ok=True,
            namespace=namespace,
            tenant_id=tenant_id,
            dataset_id=dataset_id,
            total_documents=int(stats.get("total_documents", 0)),
            total_doc_names=int(stats.get("total_doc_names", 0)),
            by_namespace=[OverviewBucketItem(**it) for it in (stats.get("by_namespace") or [])],
            by_tenant=[OverviewBucketItem(**it) for it in (stats.get("by_tenant") or [])],
            by_status=[OverviewBucketItem(**it) for it in (stats.get("by_status") or [])],
            documents=items,
        )
    except Exception as e:  # noqa: BLE001
        logger.exception("rag get_documents_overview failed")
        raise HTTPException(status_code=500, detail=f"RAG get_documents_overview failed: {e}") from e


@router.get("/knowledge/trends", summary="知识库运营趋势（按天/周新增、更新、失败）", response_model=KnowledgeTrendsResponse)
async def get_knowledge_trends(
    granularity: str = "day",
    days: int = 30,
) -> KnowledgeTrendsResponse:
    """
    用于知识库运营看板的趋势数据。

    参数说明：
    - 可选：granularity=day|week（默认 day）、days（默认 30，上限 180）

    统计口径：
    - created_success：FULL 成功数量（视为“新增”）；
    - updated_success：UPSERT 成功数量（视为“更新”）；
    - failed：FAILED 数量（任意 job_type）。
    """
    try:
        repo = _get_job_repo()
        try:
            raw = repo.trends(days=days, granularity=granularity)
        except TypeError:
            # 兼容旧实现（如无参数版本）
            raw = repo.trends()
        points = [KnowledgeTrendPoint(**it) for it in raw]
        norm_granularity = "week" if granularity == "week" else "day"
        safe_days = max(1, min(days, 180))
        return KnowledgeTrendsResponse(ok=True, granularity=norm_granularity, days=safe_days, points=points)
    except Exception as e:  # noqa: BLE001
        logger.exception("rag get_knowledge_trends failed")
        raise HTTPException(status_code=500, detail=f"RAG get_knowledge_trends failed: {e}") from e


class RunChunksMigrationRequest(BaseModel):
    embedding_dim: int = Field(..., description="向量维度，如 768/1024")


class RollbackChunksMigrationRequest(BaseModel):
    previous_index: str = Field(..., description="回滚目标物理索引名，例如 rag_knowledge_base_v1")


@router.post("/migrations/chunks/run", summary="执行 chunks 索引迁移（创建并切换 alias）")
async def run_chunks_migration(req: RunChunksMigrationRequest) -> dict:
    """
    执行 chunks 索引迁移并切换 alias。

    参数说明：
    - 必传：embedding_dim（向量维度，需与嵌入模型维度一致）
    """
    try:
        cfg = get_app_config().rag.es
        migrator = IndexMigrator(cfg)
        mapping = {
            "settings": {"analysis": {"analyzer": {"default": {"type": "standard"}}}},
            "mappings": {
                "properties": {
                    "text": {"type": "text"},
                    "namespace": {"type": "keyword"},
                    "doc_name": {"type": "keyword"},
                    "ext_id": {"type": "keyword"},
                    "metadata": {"type": "object", "enabled": True},
                    cfg.vector_field: {
                        "type": "dense_vector",
                        "dims": req.embedding_dim,
                        "index": True,
                        "similarity": "cosine",
                    },
                }
            },
        }
        result = migrator.ensure_index_and_alias(mapping)
        return {
            "ok": True,
            "alias": result.alias,
            "new_index": result.new_index,
            "old_indices": result.old_indices,
        }
    except Exception as e:  # noqa: BLE001
        logger.exception("rag run_chunks_migration failed")
        raise HTTPException(status_code=500, detail=f"RAG run_chunks_migration failed: {e}") from e


@router.post("/migrations/chunks/rollback", summary="回滚 chunks 索引 alias")
async def rollback_chunks_migration(req: RollbackChunksMigrationRequest) -> dict:
    """
    回滚 chunks 索引 alias。

    参数说明：
    - 必传：previous_index（回滚目标物理索引名）
    """
    try:
        cfg = get_app_config().rag.es
        migrator = IndexMigrator(cfg)
        migrator.rollback_alias(req.previous_index)
        return {"ok": True, "rolled_back_to": req.previous_index, "alias": cfg.index_alias}
    except Exception as e:  # noqa: BLE001
        logger.exception("rag rollback_chunks_migration failed")
        raise HTTPException(status_code=500, detail=f"RAG rollback_chunks_migration failed: {e}") from e

