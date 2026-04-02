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
from typing import Annotated, Any, List

from fastapi import APIRouter, Body, HTTPException, Path, Query
from pydantic import BaseModel, ConfigDict, Field

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


'''
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


@router.post("/ingest/texts", summary="摄入文本到 RAG 知识库", deprecated=True, include_in_schema=False)
async def ingest_texts(req: IngestTextsRequest) -> dict:
    """
    [废弃] - 已由jobs/ingest 或 upsert管线摄入/更新接口替代
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

'''

'''
class IngestDocumentItem(BaseModel):
    dataset_id: str = Field(..., description="数据集标识")
    texts: List[str] = Field(..., description="要摄入的文本列表")
    description: str | None = Field(None, description="数据集描述")
    namespace: str | None = Field(None, description="命名空间")
    doc_name: str | None = Field(None, description="文档名称")
    replace_if_exists: bool = Field(True, description="同名文档是否先删除后重建")


class IngestDocumentsRequest(BaseModel):
    documents: List[IngestDocumentItem] = Field(..., description="批量摄入文档列表")


@router.post("/ingest/documents", summary="批量摄入多个文档到 RAG 知识库", deprecated=True, include_in_schema=False)
async def ingest_documents(req: IngestDocumentsRequest) -> dict:
    """
    [废弃] - 已由jobs/ingest 或 upsert管线摄入/更新接口替代
    批量摄入已分块文本(上述 /ingest/texts 接口的批量处理版本)。

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
'''

'''
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


@router.post("/ingest/raw_document", summary="摄入原始文档（自动清洗与切块）", deprecated=True, include_in_schema=False)
async def ingest_raw_document(req: IngestRawDocumentRequest) -> dict:
    """
    [已废弃] - 已由jobs/ingest（异步）或 documents/upsert（同步单条） 替代
    同步摄入模块(同步清洗、切块、入库)
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


@router.post("/ingest/raw_documents", summary="批量摄入原始文档（自动清洗与切块）", deprecated=True, include_in_schema=False)
async def ingest_raw_documents(req: IngestRawDocumentsRequest) -> dict:
    """
    [已废弃] - 已由jobs/ingest（异步）或 documents/upsert（同步单条） 替代
    同步摄入模块(同步清洗、切块、入库)
    批量摄入原始文档（每个文档自动清洗 + 切块 + 入库） - 上述/ingest/raw_documents的批量处理版本。

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
'''

class IngestionJobDocumentRequest(BaseModel):
    """异步任务中的单篇待摄入文档（documents[] 的元素类型）。"""

    dataset_id: str = Field(..., description="【必传】数据集标识，用于分类与检索过滤")
    doc_name: str = Field(..., description="【必传】文档名称；同名+同命名空间下配合 replace_if_exists 可实现更新")
    content: str = Field(
        ...,
        min_length=1,
        description="【必传】原始正文；后端将按任务级 chunk_* 做清洗、切块后写入向量+全文索引",
    )
    doc_version: str = Field(
        "v1",
        description="【可选，默认 v1】文档版本号，用于版本化治理与按版本删除",
    )
    tenant_id: str | None = Field(None, description="【可选】租户 ID，多租户隔离时使用")
    namespace: str | None = Field(None, description="【可选】命名空间，与检索 namespace 过滤一致")
    source_type: str = Field(
        "text",
        description="【可选，默认 text】源类型：text / markdown / html / pdf / docx（影响解析分支）",
    )
    source_uri: str | None = Field(None, description="【可选】来源 URI 或路径，仅元数据落库")
    description: str | None = Field(None, description="【可选】文档业务描述")
    replace_if_exists: bool = Field(
        True,
        description="【可选，默认 true】为 true 时同名文档会先删后灌（按 store 语义删除旧 chunk）",
    )
    metadata: dict[str, Any] = Field(
        default_factory=dict,
        description="【可选】自定义扩展字段，随 chunk 元数据写入索引（OpenAPI 中类型为 object）",
    )


class IngestionJobRequest(BaseModel):
    """提交异步摄入：后台执行解析/清洗/切块、写入 ES 向量+全文通道。"""

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "documents": [
                    {
                        "dataset_id": "company_kb",
                        "doc_name": "employee_handbook_2024",
                        "content": "第一章 总则……\n第二章 考勤……",
                        "source_type": "text",
                        "namespace": "hr",
                        "replace_if_exists": True,
                    }
                ],
                "operator": "admin",
                "idempotency_key": "batch-20260402-001",
                "chunk_size": 500,
                "chunk_overlap": 80,
                "min_chunk_size": 40,
            }
        }
    )

    documents: List[IngestionJobDocumentRequest] = Field(
        ...,
        min_length=1,
        description="【必传】待摄入文档列表，至少 1 篇；每篇字段见 IngestionJobDocumentRequest",
    )
    operator: str | None = Field(None, description="【可选】操作人，写入任务记录便于审计")
    idempotency_key: str | None = Field(
        None,
        description="【可选】幂等键；相同键且任务仍在运行时可能复用同一 job，避免重复提交",
    )
    chunk_size: int = Field(500, ge=1, le=8192, description="【可选，默认 500】切块目标长度（字符级，依 pipeline 实现）")
    chunk_overlap: int = Field(80, ge=0, le=2048, description="【可选，默认 80】相邻块重叠长度，减少截断语义损失")
    min_chunk_size: int = Field(40, ge=1, le=2048, description="【可选，默认 40】过短片段过滤阈值")


class IngestionJobInfo(BaseModel):
    """单条摄入任务状态（任务索引中持久化字段的对外视图）。"""

    job_id: str = Field(..., description="任务唯一 ID，提交接口返回的 job_id")
    job_type: str = Field("upsert", description="任务类型，如 upsert")
    idempotency_key: str | None = Field(None, description="调用方幂等键（若有）")
    status: str = Field(..., description="任务状态：如 pending/running/success/failed 等")
    step: str = Field(..., description="当前流水线步骤说明，便于排障")
    created_at: str = Field(..., description="创建时间（ISO 8601 字符串）")
    updated_at: str = Field(..., description="最后更新时间")
    finished_at: str | None = Field(None, description="结束时间；未完成时为 null")
    error_code: str | None = Field(None, description="失败时的业务/系统错误码")
    error_message: str | None = Field(None, description="失败时的可读说明")
    metrics: dict[str, Any] = Field(default_factory=dict, description="任务指标扩展字段（块数、耗时等）")
    operator: str | None = Field(None, description="操作人标识（若提交时传入）")


class IngestionJobSubmitResponse(BaseModel):
    """提交异步摄入任务后的响应。"""

    model_config = ConfigDict(
        json_schema_extra={"example": {"ok": True, "job_id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890"}}
    )

    ok: bool = Field(True, description="是否受理成功（已写入任务队列/索引）")
    job_id: str = Field(..., description="新建任务 ID，用于 GET /rag/jobs/{job_id} 轮询直至终态")


class IngestionJobGetResponse(BaseModel):
    """查询单任务状态响应。"""

    ok: bool = Field(True, description="请求是否成功解析")
    job: IngestionJobInfo = Field(..., description="任务详情")


class IngestionJobListResponse(BaseModel):
    """分页任务列表响应。"""

    ok: bool = Field(True, description="请求是否成功解析")
    total: int = Field(..., description="符合条件的任务总数（用于分页）")
    limit: int = Field(..., description="本页条数")
    offset: int = Field(..., description="本页偏移")
    jobs: List[IngestionJobInfo] = Field(..., description="当前页任务列表")


class JobDocumentItem(BaseModel):
    """任务关联的单个文档摘要（来自任务记录中的 documents 快照）。"""

    dataset_id: str = Field(..., description="数据集 ID")
    doc_name: str = Field(..., description="文档名（更新主键之一）")
    doc_version: str = Field("v1", description="文档版本")
    tenant_id: str | None = Field(None, description="租户 ID")
    namespace: str | None = Field(None, description="命名空间")
    source_type: str = Field("text", description="源类型：text/markdown/html/pdf/docx 等")
    source_uri: str | None = Field(None, description="原始来源 URI")
    description: str | None = Field(None, description="文档描述")
    replace_if_exists: bool = Field(True, description="是否允许同名先删后灌")
    metadata: dict[str, Any] = Field(default_factory=dict, description="扩展元数据")


class JobDocumentsResponse(BaseModel):
    """查询任务关联文档列表响应。"""

    ok: bool = Field(True, description="请求是否成功解析")
    job_id: str = Field(..., description="任务 ID")
    documents: List[JobDocumentItem] = Field(..., description="该任务包含的文档条目")


class RetryJobResponse(BaseModel):
    """任务重试响应：生成新任务 ID。"""

    ok: bool = Field(True, description="是否受理成功")
    job_id: str = Field(..., description="新任务 ID")
    retry_of: str = Field(..., description="原失败任务 ID")


class UpsertDocumentResponse(BaseModel):
    """同步 upsert 单文档后的响应。"""

    ok: bool = Field(True, description="是否成功写入")
    doc_name: str = Field(..., description="文档名")
    chunk_count: int = Field(..., description="写入的 chunk 条数")
    stats: dict[str, Any] = Field(
        default_factory=dict,
        description="流水线统计信息（清洗、切块等，结构随 pipeline 实现扩展）",
    )


class DeleteDocumentResponse(BaseModel):
    """按文档删除响应。"""

    ok: bool = Field(True, description="是否执行成功（无匹配时 deleted 可能为 0）")
    deleted: int = Field(..., description="删除的向量/chunk 条数（底层 store 语义）")


class QueryRagResponse(BaseModel):
    """RAG 检索调试响应：返回纯文本片段列表（已走混合检索与场景 profile）。"""

    ok: bool = Field(True, description="检索流程是否完成")
    query: str = Field(..., description="原始查询")
    count: int = Field(..., description="返回片段条数")
    snippets: List[str] = Field(..., description="上下文文本片段，供调试或拼装 prompt")


class ChunksMigrationRunResponse(BaseModel):
    """chunks 物理索引创建并切换 alias 后的结果。"""

    ok: bool = Field(True, description="是否成功")
    alias: str = Field(..., description="逻辑别名（如 rag_knowledge_base_current）")
    new_index: str = Field(..., description="新创建的物理索引名")
    old_indices: List[str] = Field(default_factory=list, description="此前指向该 alias 的旧物理索引名列表")


class ChunksMigrationRollbackResponse(BaseModel):
    """chunks alias 回滚结果。"""

    ok: bool = Field(True, description="是否成功")
    rolled_back_to: str = Field(..., description="回滚目标物理索引名")
    alias: str = Field(..., description="当前 alias 名称")


@router.post(
    "/jobs/ingest",
    summary="提交异步摄入任务",
    response_model=IngestionJobSubmitResponse,
    response_description="返回新任务 job_id，请用 GET /rag/jobs/{job_id} 轮询直至 success/failed。",
    description=(
        "**本接口无 Query / Path 参数**，所有字段均在 **Request body** 的 JSON 内。"
        "Swagger 中「Parameters」为空属正常现象；请展开下方 **Request body** 查看 Schema 与 Example。"
    ),
)
async def submit_ingestion_job(
    req: Annotated[
        IngestionJobRequest,
        Body(
            openapi_examples={
                "minimal": {
                    "summary": "最小请求（单文档）",
                    "description": "仅必传字段：documents[0].dataset_id / doc_name / content",
                    "value": {
                        "documents": [
                            {
                                "dataset_id": "demo_kb",
                                "doc_name": "hello",
                                "content": "这是要入库的正文。",
                            }
                        ]
                    },
                },
                "full": {
                    "summary": "完整示例（含可选与切块参数）",
                    "value": {
                        "documents": [
                            {
                                "dataset_id": "company_kb",
                                "doc_name": "policy",
                                "doc_version": "v1",
                                "tenant_id": "t1",
                                "namespace": "legal",
                                "content": "制度正文……",
                                "source_type": "markdown",
                                "source_uri": "https://intranet/docs/policy.md",
                                "description": "员工制度",
                                "replace_if_exists": True,
                                "metadata": {"dept": "HR"},
                            }
                        ],
                        "operator": "admin",
                        "idempotency_key": "ingest-001",
                        "chunk_size": 500,
                        "chunk_overlap": 80,
                        "min_chunk_size": 40,
                    },
                },
            },
        ),
    ],
) -> IngestionJobSubmitResponse:
    """企业运维推荐入口：异步摄入，避免大文档阻塞 HTTP。"""
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


@router.get(
    "/jobs/{job_id}",
    summary="查询摄入任务状态",
    response_model=IngestionJobGetResponse,
    response_description="含 status、step、错误信息与 metrics。",
)
async def get_ingestion_job(
    job_id: Annotated[str, Path(description="任务 ID，由 POST /rag/jobs/ingest 返回")],
) -> IngestionJobGetResponse:
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


@router.post(
    "/jobs/{job_id}/retry",
    summary="重试摄入任务",
    response_model=RetryJobResponse,
    response_description="生成新 job_id；原任务 ID 在 retry_of 字段。",
)
async def retry_ingestion_job(
    job_id: Annotated[str, Path(description="待重试的失败或中断任务 ID")],
) -> RetryJobResponse:
    """
    重试指定任务。

    参数说明：
    - 必传：job_id（路径参数）
    """
    try:
        new_job_id = _get_orchestrator().retry_job(job_id)
        return RetryJobResponse(ok=True, job_id=new_job_id, retry_of=job_id)
    except Exception as e:  # noqa: BLE001
        logger.exception("rag retry_ingestion_job failed: %s", job_id)
        raise HTTPException(status_code=500, detail=f"RAG retry_ingestion_job failed: {e}") from e


@router.get(
    "/jobs",
    summary="分页查询摄入任务",
    response_model=IngestionJobListResponse,
    response_description="按创建时间倒序分页；total 为总任务数。",
)
async def list_ingestion_jobs(
    limit: Annotated[int, Query(description="每页条数", ge=1, le=500)] = 20,
    offset: Annotated[int, Query(description="跳过条数（分页偏移）", ge=0)] = 0,
) -> IngestionJobListResponse:
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


@router.get(
    "/jobs/{job_id}/documents",
    summary="查询任务关联文档",
    response_model=JobDocumentsResponse,
    response_description="来自任务记录内嵌的文档快照，非实时 ES 全量扫描。",
)
async def get_job_documents(
    job_id: Annotated[str, Path(description="任务 ID")],
) -> JobDocumentsResponse:
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
    """同步写入单文档：请求内完成 pipeline 与索引，适合小文本快速修订。"""

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


@router.post(
    "/documents/upsert",
    summary="同步 upsert 文档（自动清洗切块后立即入库）",
    response_model=UpsertDocumentResponse,
    response_description="立即写入向量+全文索引；大文档建议改用 POST /rag/jobs/ingest。",
)
async def upsert_document(req: UpsertDocumentRequest) -> UpsertDocumentResponse:
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
        return UpsertDocumentResponse(
            ok=True, doc_name=req.doc_name, chunk_count=len(chunks), stats=stats
        )
    except Exception as e:  # noqa: BLE001
        logger.exception("rag upsert_document failed: doc_name=%s", req.doc_name)
        raise HTTPException(status_code=500, detail=f"RAG upsert_document failed: {e}") from e


class DeleteDocumentRequest(BaseModel):
    """按 doc_name（及可选 namespace/version）删除向量库中的 chunk。"""

    doc_name: str = Field(..., description="文档名称")
    namespace: str | None = Field(None, description="命名空间；为空则跨命名空间删除")
    doc_version: str | None = Field(None, description="可选文档版本；传入时按版本精确删除")


@router.post(
    "/documents/delete",
    summary="按文档名删除已摄入知识",
    response_model=DeleteDocumentResponse,
    response_description="deleted 为底层 store 删除的条数；可选按 namespace/doc_version 缩小范围。",
)
async def delete_document(req: DeleteDocumentRequest) -> DeleteDocumentResponse:
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
        return DeleteDocumentResponse(ok=True, deleted=deleted)
    except Exception as e:  # noqa: BLE001
        logger.exception("rag delete_document failed: doc_name=%s namespace=%s", req.doc_name, req.namespace)
        raise HTTPException(status_code=500, detail=f"RAG delete_document failed: {e}") from e


class QueryRequest(BaseModel):
    """RAG 检索调试请求（走 RAGService 混合检索 + 场景 profile，非对话 Graph 路由）。"""

    query: str = Field(..., description="检索问句或关键词")
    top_k: int | None = Field(
        None,
        description="返回片段条数上限；不传则使用对应 scene 的默认 top_k（见 RAG_SCENE_* 配置）",
    )
    namespace: str | None = Field(None, description="仅检索该命名空间下的 chunk；不传则不限定")
    scene: str = Field(
        "llm_inference",
        description="场景键：llm_inference / chatbot / analysis / nl2sql，影响召回宽度与重排参数",
    )


@router.post(
    "/query",
    summary="查询 RAG 知识库（调试/冒烟）",
    response_model=QueryRagResponse,
    response_description="snippets 为文本列表。与启用 GraphRAG 时的 /chatbot 链路不完全一致。",
)
async def query_rag(req: QueryRequest) -> QueryRagResponse:
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
        return QueryRagResponse(
            ok=True, query=req.query, count=len(snippets), snippets=snippets
        )
    except Exception as e:  # noqa: BLE001
        logger.exception("rag query failed: query=%s namespace=%s scene=%s", req.query, req.namespace, req.scene)
        raise HTTPException(status_code=500, detail=f"RAG query failed: {e}") from e


class DatasetMetaResponse(BaseModel):
    """进程内数据集登记项（非 ES 权威视图）。"""

    dataset_id: str = Field(..., description="数据集 ID")
    description: str | None = Field(None, description="描述")
    num_items: int = Field(..., description="最近一次登记时的 chunk 条数")
    namespace: str | None = Field(None, description="命名空间")
    doc_name: str | None = Field(None, description="关联文档名")


@router.get(
    "/datasets",
    response_model=List[DatasetMetaResponse],
    summary="列出已登记的 RAG 数据集（进程内，已废弃）",
    deprecated=True,
    description=(
        "**已废弃**：数据来自应用进程内存，重启后丢失，且不等于 ES 中全量知识库。"
        "请使用 `GET /rag/documents/meta` 或 `GET /rag/documents/overview`。"
    ),
)
async def list_datasets() -> List[DatasetMetaResponse]:
    """兼容保留；新集成请使用文档元数据接口。"""
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
    """单篇文档在文档索引中的元数据一行。"""

    doc_name: str = Field(..., description="文档名")
    doc_version: str = Field("v1", description="文档版本")
    tenant_id: str | None = Field(None, description="租户 ID")
    dataset_id: str = Field(..., description="所属数据集 ID")
    namespace: str | None = Field(None, description="命名空间")
    source_type: str = Field("text", description="源类型")
    source_uri: str | None = Field(None, description="来源 URI")
    chunk_count: int = Field(0, description="关联 chunk 数量（若已统计）")
    pipeline_version: str | None = Field(None, description="摄入流水线版本")
    status: str | None = Field(None, description="文档状态：如 ready / failed 等")
    created_at: str | None = Field(None, description="创建时间")
    updated_at: str | None = Field(None, description="最后更新时间")
    last_job_id: str | None = Field(None, description="最近关联任务 ID")
    last_job_type: str | None = Field(None, description="最近任务类型")
    last_job_status: str | None = Field(None, description="最近任务状态")
    metadata: dict[str, Any] = Field(default_factory=dict, description="扩展元数据")
    error: str | None = Field(None, description="失败时的错误摘要")


class DocumentMetaListResponse(BaseModel):
    """分页文档元数据列表。"""

    ok: bool = Field(True, description="请求是否成功解析")
    limit: int = Field(..., description="本页最大条数")
    offset: int = Field(..., description="本页偏移")
    namespace: str | None = Field(None, description="查询时使用的 namespace 过滤条件（若有）")
    documents: List[DocumentMetaItem] = Field(..., description="文档元数据列表")


@router.get(
    "/documents/meta",
    summary="分页查询文档元数据",
    response_model=DocumentMetaListResponse,
    response_description="面向管理台的文档清单，数据来自文档索引（非 chunk 正文）。",
)
async def list_document_meta(
    limit: Annotated[int, Query(description="每页条数", ge=1, le=500)] = 20,
    offset: Annotated[int, Query(description="分页偏移", ge=0)] = 0,
    namespace: Annotated[str | None, Query(description="按命名空间过滤")] = None,
    tenant_id: Annotated[str | None, Query(description="按租户过滤")] = None,
    dataset_id: Annotated[str | None, Query(description="按数据集过滤")] = None,
    doc_name: Annotated[str | None, Query(description="按文档名模糊或精确过滤（依仓库实现）")] = None,
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
    """聚合桶：key 为分组键，count 为文档条数。"""

    key: str | None = Field(None, description="分组键，如某 namespace / tenant / status")
    count: int = Field(0, description="该桶内文档数")


class KnowledgeOverviewResponse(BaseModel):
    """知识库总览：聚合统计 + 当前过滤条件下的分页文档明细。"""

    ok: bool = Field(True, description="请求是否成功解析")
    namespace: str | None = Field(None, description="查询过滤：命名空间")
    tenant_id: str | None = Field(None, description="查询过滤：租户")
    dataset_id: str | None = Field(None, description="查询过滤：数据集")
    total_documents: int = Field(0, description="文档记录总数（当前过滤条件下）")
    total_doc_names: int = Field(0, description="唯一 doc_name 数（若统计可用）")
    by_namespace: List[OverviewBucketItem] = Field(default_factory=list, description="按 namespace 分桶")
    by_tenant: List[OverviewBucketItem] = Field(default_factory=list, description="按 tenant 分桶")
    by_status: List[OverviewBucketItem] = Field(default_factory=list, description="按状态分桶")
    documents: List[DocumentMetaItem] = Field(default_factory=list, description="分页文档明细")


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


@router.get(
    "/documents/overview",
    summary="查询知识库整体情况（元数据总览）",
    response_model=KnowledgeOverviewResponse,
    response_description="聚合桶 + documents 分页列表。",
)
async def get_documents_overview(
    limit: Annotated[int, Query(description="明细每页条数", ge=1, le=500)] = 20,
    offset: Annotated[int, Query(description="明细分页偏移", ge=0)] = 0,
    namespace: Annotated[str | None, Query(description="过滤命名空间")] = None,
    tenant_id: Annotated[str | None, Query(description="过滤租户")] = None,
    dataset_id: Annotated[str | None, Query(description="过滤数据集")] = None,
    doc_name: Annotated[str | None, Query(description="过滤文档名")] = None,
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


@router.get(
    "/knowledge/trends",
    summary="知识库运营趋势（按天/周新增、更新、失败）",
    response_model=KnowledgeTrendsResponse,
    response_description="基于任务索引统计的成功/失败趋势。",
)
async def get_knowledge_trends(
    granularity: Annotated[str, Query(description="聚合粒度：day 或 week")] = "day",
    days: Annotated[int, Query(description="统计窗口天数，最大 180", ge=1, le=180)] = 30,
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


@router.post(
    "/migrations/chunks/run",
    summary="执行 chunks 索引迁移（创建并切换 alias）",
    response_model=ChunksMigrationRunResponse,
    response_description="创建新物理索引并切换 alias；old_indices 为被替换下的旧索引名列表。",
)
async def run_chunks_migration(req: RunChunksMigrationRequest) -> ChunksMigrationRunResponse:
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
        return ChunksMigrationRunResponse(
            ok=True,
            alias=result.alias,
            new_index=result.new_index,
            old_indices=result.old_indices,
        )
    except Exception as e:  # noqa: BLE001
        logger.exception("rag run_chunks_migration failed")
        raise HTTPException(status_code=500, detail=f"RAG run_chunks_migration failed: {e}") from e


@router.post(
    "/migrations/chunks/rollback",
    summary="回滚 chunks 索引 alias",
    response_model=ChunksMigrationRollbackResponse,
    response_description="将 alias 指回指定物理索引名。",
)
async def rollback_chunks_migration(req: RollbackChunksMigrationRequest) -> ChunksMigrationRollbackResponse:
    """
    回滚 chunks 索引 alias。

    参数说明：
    - 必传：previous_index（回滚目标物理索引名）
    """
    try:
        cfg = get_app_config().rag.es
        migrator = IndexMigrator(cfg)
        migrator.rollback_alias(req.previous_index)
        return ChunksMigrationRollbackResponse(
            ok=True, rolled_back_to=req.previous_index, alias=cfg.index_alias
        )
    except Exception as e:  # noqa: BLE001
        logger.exception("rag rollback_chunks_migration failed")
        raise HTTPException(status_code=500, detail=f"RAG rollback_chunks_migration failed: {e}") from e

