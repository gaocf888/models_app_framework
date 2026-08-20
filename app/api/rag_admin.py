from __future__ import annotations

"""
RAG 管理接口（对应《下一阶段工作清单》中的 TODO-P6）。

说明：
- 提供文本摄入、批量摄入、按文档删除、按 namespace 整库清空、namespace 启用/优先级管理、
  单篇文档 namespace 迁移、检索查询、数据集列表查询等管理能力；
- 摄入支持 doc_name + replace_if_exists，实现同名文档更新（先删后灌）；
- 摄入支持 namespace 级 ``namespace_kb_enabled`` / ``namespace_kb_priority``（写入 doc/chunk 元数据，召回时生效）；
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
5) namespace 召回优先级（可选）
   - RAG_NAMESPACE_PRIORITY_BOOST、RAG_NAMESPACE_PRIORITY_TIERED（见 .env.example）。
"""

from functools import lru_cache
from typing import Annotated, Any, List

from fastapi import APIRouter, BackgroundTasks, File, Form, HTTPException, Path, Query, UploadFile
from pydantic import BaseModel, ConfigDict, Field

from app.core.config import get_app_config
from app.rag.ingestion import RAGDatasetMeta, RAGIngestionService
from app.rag.document_repository import DocumentRepository
from app.rag.document_pipeline import ChunkingConfig, DocumentPipeline
from app.rag.ingestion_orchestrator import IngestionOrchestrator
from app.rag.job_repository import JobRepository
from app.rag.migrations import IndexMigrator
from app.rag.content_url_fetch import materialize_document_content_from_url
from app.rag.mineru_ingest import prepare_pdf_document_for_pipeline
from app.rag.models import DocumentSource
from app.rag.namespace_kb import (
    DEFAULT_NAMESPACE_PATH,
    build_chunk_metadatas,
    namespace_from_path_param,
)
from app.rag.original_docs import (
    DOC_STATUS_UPLOADED,
    META_FILE_SIZE,
    META_OBJECT_KEY,
    META_ORIGINAL_FILENAME,
    guess_source_type,
    original_ref_from_record,
    resolve_namespace_kb_for_ingest,
)
from app.rag.graph_namespace_resync import run_graph_resync_after_namespace_move
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


def _ensure_ingest_namespace(namespace: str | None, *, always: bool = False) -> str | None:
    from app.rag.original_docs import normalize_required_namespace, require_namespace_enabled

    if always or require_namespace_enabled():
        return normalize_required_namespace(namespace, always=True)
    ns = (namespace or "").strip()
    return ns or None


def _delete_original_objects(payloads: list[dict[str, Any]]) -> None:
    from app.rag.asset_storage import RagAssetStorage

    storage = RagAssetStorage()
    for payload in payloads:
        ref = original_ref_from_record(payload if isinstance(payload, dict) else None)
        if ref:
            storage.delete_original(ref)


def _mark_documents_job_pending(docs: list[DocumentSource], job_id: str) -> None:
    from app.rag.document_repository import make_document_storage_key
    from app.rag.models import utcnow_iso

    repo = _get_doc_repo()
    fallback = get_app_config().rag.ingestion.tenant_id_default or "default"
    for doc in docs:
        key = make_document_storage_key(
            doc.doc_name,
            namespace=doc.namespace,
            tenant_id=doc.tenant_id,
            doc_version=doc.doc_version,
            tenant_id_fallback=fallback,
        )
        payload = repo.get(key)
        if not payload:
            continue
        payload["last_job_id"] = job_id
        payload["last_job_type"] = "upsert"
        payload["last_job_status"] = "PENDING"
        payload["updated_at"] = utcnow_iso()
        repo.upsert(key, payload)


def warmup_rag_admin_components() -> None:
    """
    Eagerly initialize orchestrator so queue workers and startup recovery run
    when the app boots, not only on first ingest request.
    """
    _get_orchestrator()


def shutdown_rag_admin_components() -> None:
    """
    Gracefully stop background workers on app shutdown.
    """
    try:
        _get_orchestrator().close()
    except Exception:  # noqa: BLE001
        logger.warning("rag admin orchestrator close failed", exc_info=True)


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
    """异步任务中单篇文档（`documents[]` 元素）。`content` 可为内联正文或 pdf/docx/xlsx 等服务端本地路径，见 `content` 字段说明。"""

    dataset_id: str = Field(
        ...,
        description="必填。数据集 ID：知识/业务域划分，写入索引并用于检索、管理台按数据集过滤。",
    )
    doc_name: str = Field(
        ...,
        description="必填。文档逻辑名（更新主键之一）。与 `namespace`、`doc_version` 等共同标识一篇知识；同名同域下可配合 `replace_if_exists` 先删后灌。",
    )
    content: str = Field(
        ...,
        min_length=1,
        description=(
            "必填。语义由 `source_type` 决定："
            "① `text`/`markdown`/`html`：内联正文，或（在 `RAG_CONTENT_FETCH_ENABLED=true` 时）`http(s)://` 文件 URL，服务端拉取为文本；"
            "② `pdf`/`docx`/`xlsx`/`xlsm`：内联已抽取文本，或本地绝对路径/`file://...`，或（同上开关开启时）`http(s)://` 下载到临时文件再解析；"
            "③ 管理台上传后的稳定对象引用：`minio://bucket/key` 或 `local:` 路径（内部 get，不走预签名 URL / content-fetch）。"
            "URL 拉取受 `RAG_CONTENT_FETCH_ALLOW_HOSTS`、私网解析拦截等约束（防 SSRF）；`source_uri` 仍不用于 HTTP 下载。"
        ),
    )
    doc_version: str = Field(
        "v1",
        description="可选，默认 v1。文档版本号，用于版本治理与按版本删除；与 `doc_name` 等一起区分不同版内容。",
    )
    tenant_id: str | None = Field(
        None,
        description="可选。租户 ID：多租户隔离与过滤用，会写入 chunk/文档元数据；单租户场景可省略。",
    )
    namespace: str | None = Field(
        None,
        description="可选。命名空间：逻辑分区（部门/场景等），与 `GET /rag/query` 等接口的 namespace 过滤一致；用于缩小「同名」与检索范围。",
    )
    source_type: str = Field(
        "text",
        description="可选，默认 text。格式/解析方式：text、markdown、html、pdf、docx、xlsx/xlsm、image（需 RAG_FIGURE_ENABLED）；pdf 扫描件需 MinerU。",
    )
    source_uri: str | None = Field(
        None,
        description="可选。业务侧「来源地址」字符串（如 https 链接、对象存储 URI），仅写入元数据供溯源/展示；不用于拉取正文，也不参与向量解析。",
    )
    description: str | None = Field(
        None,
        description="可选。给人看的文档摘要或说明，写入元数据。",
    )
    replace_if_exists: bool = Field(
        True,
        description="可选，默认 true。为 true 时在写入前删除同 doc 名下已有 chunk（先删后灌）；false 时行为以实现为准，一般用于禁止覆盖场景。",
    )
    metadata: dict[str, Any] = Field(
        default_factory=dict,
        description="可选。自定义键值，并入索引 metadata（如部门、标签）；默认 {}。",
    )
    namespace_kb_enabled: bool | None = Field(
        None,
        description=(
            "可选。namespace 级知识库是否启用；默认 true。"
            "写入文档与 chunk metadata.namespace_kb_enabled；同 namespace 建议传一致值。"
        ),
    )
    namespace_kb_priority: int | None = Field(
        None,
        ge=1,
        description=(
            "可选。namespace 级召回优先级，数值越小越优先；默认 1，须 >= 1。"
            "写入 metadata.namespace_kb_priority。"
        ),
    )


class IngestionJobRequest(BaseModel):
    """提交异步摄入请求体。Swagger 中 Schema 与各 Field description 为权威说明；下方 example 为内联正文示例，pdf/docx/xlsx 时 `content` 可改为服务端路径字符串。"""

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "documents": [
                    {
                        "dataset_id": "company_kb",
                        "doc_name": "employee_handbook_2024",
                        "doc_version": "v1",
                        "tenant_id": "t1",
                        "namespace": "hr",
                        "content": "第一章 总则……\n第二章 考勤……",
                        "source_type": "text",
                        "source_uri": "https://intranet/docs/handbook.md",
                        "description": "员工手册",
                        "replace_if_exists": True,
                        "metadata": {"dept": "HR"},
                        "namespace_kb_enabled": True,
                        "namespace_kb_priority": 1,
                    }
                ],
                "operator": "admin",
                "idempotency_key": "ingest-20260402-001",
                "chunk_size": 500,
                "chunk_overlap": 80,
                "min_chunk_size": 40,
            }
        }
    )

    documents: List[IngestionJobDocumentRequest] = Field(
        ...,
        min_length=1,
        description="必填。至少 1 篇；结构见 `IngestionJobDocumentRequest`（每篇字段说明以 Schema 为准）。",
    )
    operator: str | None = Field(
        None,
        description="可选。操作人标识（账号/姓名等），写入任务记录供审计；不影响检索与切块逻辑。",
    )
    idempotency_key: str | None = Field(
        None,
        description=(
            "可选。调用方自定义幂等键。"
            "仅当本字段非空时：若已存在相同键且任务状态为 PENDING 或 RUNNING，将返回已有 `job_id`、不新建任务；"
            "不传则每次调用都会新建任务（已完成/失败的历史任务不会因同键自动合并）。"
        ),
    )
    chunk_size: int = Field(
        500, ge=1, le=8192, description="可选，默认 500。切块目标长度（字符），作用于本任务内全部文档。"
    )
    chunk_overlap: int = Field(
        80, ge=0, le=2048, description="可选，默认 80。相邻块重叠字符数，减轻边界截断。"
    )
    min_chunk_size: int = Field(
        40, ge=1, le=2048, description="可选，默认 40。过短片段的合并/丢弃阈值（字符）。"
    )


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
    """提交异步摄入任务后的响应体。"""

    model_config = ConfigDict(
        json_schema_extra={"example": {"ok": True, "job_id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890"}}
    )

    ok: bool = Field(True, description="是否受理成功")
    job_id: str = Field(..., description="新建任务 ID，用于轮询 GET /rag/jobs/{job_id}")


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
    source_type: str = Field("text", description="源类型：text/markdown/html/pdf/docx/xlsx/xlsm 等")
    source_uri: str | None = Field(None, description="原始来源 URI")
    description: str | None = Field(None, description="文档描述")
    replace_if_exists: bool = Field(True, description="是否允许同名先删后灌")
    namespace_kb_enabled: bool | None = Field(
        None,
        description="namespace 级是否启用；未传时任务执行按默认 true 处理。",
    )
    namespace_kb_priority: int | None = Field(
        None,
        ge=1,
        description="namespace 级召回优先级，数值越小越优先；未传时默认 1。",
    )
    metadata: dict[str, Any] = Field(default_factory=dict, description="其它扩展元数据（不含 namespace_kb_* 时以顶层字段为准）")


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


class RecoverStuckJobsResponse(BaseModel):
    """手动回收长时间未更新的 RUNNING 任务。"""

    ok: bool = Field(True, description="是否执行成功")
    recovered: int = Field(..., description="被转为 FAILED 的僵尸 RUNNING 任务数量")
    threshold_seconds: int = Field(..., description="判定卡死阈值（秒）")


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
    doc_records_deleted: int = Field(
        0,
        description="删除的文档元数据条数（docs 索引）；与 deleted 独立，overview 依赖此项被清理。",
    )


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
    response_description="受理成功后返回 job_id；请 GET /rag/jobs/{job_id} 轮询至终态。",
    # 勿在此写 description=：FastAPI 会用它覆盖函数 docstring，导致下方长说明不出现在 OpenAPI/Swagger。
)
async def submit_ingestion_job(req: IngestionJobRequest) -> IngestionJobSubmitResponse:
    """
    异步提交知识摄入任务（推荐生产入口）。无 Path/Query；字段以 Request body Schema 与各 `Field(description=…)` 为准。

    **要点**：`content` 可为内联正文、本地/`file://` 路径、管理台上传后的 ``minio://`` / ``local:`` 对象引用，或在 `RAG_CONTENT_FETCH_ENABLED=true` 时的 http(s) 文件 URL；`source_uri` 仅作 HTTP 溯源、不用于下载（对象引用可与 content 相同以便重灌）。

    **各字段释义与必填以 OpenAPI Schema 为准**，以下为速查。

    **路径/Query**：无。

    **`documents[]` 每篇文档（模型 `IngestionJobDocumentRequest`）**
    - `content`：**必填**。内联正文、本地/`file://` 路径、``minio://bucket/key`` / ``local:`` 对象引用；若开启 `RAG_CONTENT_FETCH_ENABLED`，可为 `http(s)://` 文件 URL。不会用 `source_uri` 做 HTTP 下载。
    - `dataset_id`：必填，数据集划分与过滤。[可作为知识库一级分区]
    - `doc_name`：必填，文档逻辑名（更新主键之一）。
    - `doc_version`：可选默认 v1，版本治理与按版本删除。
    - `tenant_id`：可选，多租户 ID，写入元数据供隔离/过滤。
    - `namespace`：逻辑分区；`RAG_REQUIRE_NAMESPACE=true` 时必填非空。[可作为知识库二级分区]
    - `source_type`：可选默认 text，决定如何解析 `content`。
    - `source_uri`：可选，**仅元数据**（链接/URI 字符串），溯源展示；**不用于抓取正文**。
    - `description`：可选，人读摘要。
    - `replace_if_exists`：可选默认 true，同名先删后灌。
    - `metadata`：可选，自定义扩展字段写入索引。[可作为知识库三级级及以下分区]
    - `namespace_kb_enabled`：可选，namespace 级是否启用；默认 true。写入 doc/chunk 元数据；
      召回时 ``namespace_kb_enabled=false`` 的 chunk 会被过滤。同 namespace 建议传一致值。
    - `namespace_kb_priority`：可选，namespace 级召回优先级，**数值越小越优先**；默认 1，须 >= 1。
      写入 doc/chunk 元数据；全库检索（未指定 namespace）时参与排序/分层召回。

    **任务级（模型 `IngestionJobRequest` 根字段）**
    - `operator`：可选，操作者标识，仅审计。
    - `idempotency_key`：可选；**仅传入时**若已有同键且任务仍为 PENDING/RUNNING 则返回原 `job_id`，否则每次新建任务。
    - `chunk_size` / `chunk_overlap` / `min_chunk_size`：可选，默认 500 / 80 / 40，作用于本任务全部文档。

    **响应体 `IngestionJobSubmitResponse`（200）**
    - `ok`、`job_id`（新任务或幂等命中时的已有任务）。

    失败时 HTTP 5xx，`detail` 为错误信息。
    """
    try:
        docs = []
        for d in req.documents:
            ns = _ensure_ingest_namespace(d.namespace)
            enabled, priority = resolve_namespace_kb_for_ingest(
                ns, d.namespace_kb_enabled, d.namespace_kb_priority
            )
            docs.append(
                DocumentSource(
                    dataset_id=d.dataset_id,
                    doc_name=d.doc_name,
                    doc_version=d.doc_version,
                    tenant_id=d.tenant_id,
                    namespace=ns,
                    content=d.content,
                    source_type=d.source_type,
                    source_uri=d.source_uri,
                    description=d.description,
                    replace_if_exists=d.replace_if_exists,
                    metadata=d.metadata,
                    namespace_kb_enabled=enabled,
                    namespace_kb_priority=priority,
                )
            )
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
        _mark_documents_job_pending(docs, job_id)
        return IngestionJobSubmitResponse(ok=True, job_id=job_id)
    except ValueError as e:
        if "namespace is required" in str(e):
            raise HTTPException(status_code=400, detail=str(e)) from e
        logger.exception("rag submit_ingestion_job failed")
        raise HTTPException(status_code=400, detail=f"RAG submit_ingestion_job failed: {e}") from e
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
    查询单条摄入任务当前状态。

    **路径参数**
    - `job_id`：必填。提交任务时返回的 ID。

    **响应体 `IngestionJobGetResponse`（200）**
    - `ok`：请求解析成功。
    - `job`：`IngestionJobInfo`，含 `status`、`step`、`error_*`、`metrics`、`created_at` 等；未找到任务时 404。
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
    对失败/可重试任务发起新一次执行（新 job_id）。

    **路径参数**
    - `job_id`：必填。原任务 ID。

    **响应体 `RetryJobResponse`（200）**
    - `ok`：是否受理。
    - `job_id`：新任务 ID。
    - `retry_of`：原任务 ID。
    """
    try:
        new_job_id = _get_orchestrator().retry_job(job_id)
        return RetryJobResponse(ok=True, job_id=new_job_id, retry_of=job_id)
    except Exception as e:  # noqa: BLE001
        logger.exception("rag retry_ingestion_job failed: %s", job_id)
        raise HTTPException(status_code=500, detail=f"RAG retry_ingestion_job failed: {e}") from e


@router.post(
    "/jobs/recover_stuck",
    summary="回收僵尸 RUNNING 任务",
    response_model=RecoverStuckJobsResponse,
    response_description="将超时未更新的 RUNNING 任务转为 FAILED，便于重试。",
)
async def recover_stuck_ingestion_jobs(
    threshold_seconds: Annotated[int, Query(description="判定卡死阈值（秒）", ge=60, le=86400)] = 1800,
) -> RecoverStuckJobsResponse:
    """
    手动回收长时间未更新的 RUNNING 任务（常见于服务重启中断后）。

    **Query**
    - `threshold_seconds`：可选，默认 1800；超过该秒数未更新的 RUNNING 任务将被转为 FAILED。
    """
    try:
        recovered = _get_orchestrator().recover_stuck_jobs(max_stuck_seconds=threshold_seconds)
        return RecoverStuckJobsResponse(ok=True, recovered=recovered, threshold_seconds=threshold_seconds)
    except Exception as e:  # noqa: BLE001
        logger.exception("rag recover_stuck_ingestion_jobs failed")
        raise HTTPException(status_code=500, detail=f"RAG recover_stuck_ingestion_jobs failed: {e}") from e


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
    分页列出摄入任务。

    **Query**
    - `limit`：可选，默认 20，每页条数（1～500）。
    - `offset`：可选，默认 0，跳过条数。

    **响应体 `IngestionJobListResponse`（200）**
    - `ok`、`total`、`limit`、`offset`、`jobs`（`IngestionJobInfo` 数组）。
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
    返回任务提交时记录的文档快照（非实时扫 ES）。

    **路径参数**
    - `job_id`：必填。

    **响应体 `JobDocumentsResponse`（200）**
    - `ok`、`job_id`、`documents`（`JobDocumentItem` 列表：含 `namespace_kb_enabled`、`namespace_kb_priority` 等）。
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
                namespace_kb_enabled=d.get("namespace_kb_enabled"),
                namespace_kb_priority=d.get("namespace_kb_priority"),
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
    """同步写入单文档。字段语义与 ``POST /rag/jobs/ingest`` 中单篇 ``IngestionJobDocumentRequest`` 对齐（含 namespace_kb_*）。"""

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "dataset_id": "company_kb",
                "doc_name": "readme",
                "namespace": "docs",
                "content": "正文……",
                "source_type": "text",
                "source_uri": "https://example.com/readme.md",
                "description": "说明",
                "chunk_size": 500,
                "chunk_overlap": 80,
                "min_chunk_size": 40,
                "metadata": {"dept": "IT"},
                "namespace_kb_enabled": True,
                "namespace_kb_priority": 1,
            }
        }
    )

    dataset_id: str = Field(..., description="必填。数据集 ID，写入索引并用于检索过滤（同异步任务）。")
    doc_name: str = Field(..., description="必填。文档逻辑名；同步接口固定 `replace_if_exists=true`（先删后灌）。")
    namespace: str | None = Field(
        None,
        description="可选。命名空间，与检索 namespace 过滤一致；用于逻辑分区。",
    )
    content: str = Field(
        ...,
        description=(
            "必填。内联正文、本地路径或 `file://...`；`RAG_CONTENT_FETCH_ENABLED=true` 时可为 `http(s)://` 文件 URL。"
            "不根据 `source_uri` 拉取；扫描 PDF 需 MinerU。"
        ),
    )
    source_type: str = Field(
        "text",
        description="可选，默认 text。解析方式：text、markdown、html、pdf、docx、xlsx/xlsm、image（需 RAG_FIGURE_ENABLED）。",
    )
    source_uri: str | None = Field(
        None,
        description="可选。业务来源 URI 字符串，仅写入元数据溯源；不用于下载正文。",
    )
    description: str | None = Field(None, description="可选。文档简介，写入元数据。")
    chunk_size: int = Field(500, description="可选，默认 500。切块目标长度（字符）。")
    chunk_overlap: int = Field(80, description="可选，默认 80。块重叠（字符）。")
    min_chunk_size: int = Field(40, description="可选，默认 40。最短块阈值（字符）。")
    metadata: dict[str, Any] = Field(
        default_factory=dict,
        description="可选。自定义键值并入索引 metadata；默认 {}。",
    )
    namespace_kb_enabled: bool | None = Field(
        None,
        description="可选。namespace 级知识库是否启用；默认 true。",
    )
    namespace_kb_priority: int | None = Field(
        None,
        ge=1,
        description="可选。namespace 级召回优先级（数值越小越优先）；默认 1，须 >= 1。",
    )


@router.post(
    "/documents/upsert",
    summary="同步 upsert 文档（自动清洗切块后立即入库）",
    response_model=UpsertDocumentResponse,
    response_description="立即写入向量+全文索引；大文档建议改用 POST /rag/jobs/ingest。",
    # 勿写 description=，以免覆盖 docstring，OpenAPI 中不显示下方说明。
)
async def upsert_document(req: UpsertDocumentRequest) -> UpsertDocumentResponse:
    """
    同步 upsert 单文档。无 Path/Query；`content` 可内联、本地路径，或（开启 `RAG_CONTENT_FETCH_ENABLED`）http(s) URL，详见 Schema。

    **无** `tenant_id` / `doc_version` / `idempotency_key` / `replace_if_exists`（同步路径固定覆盖同名）。

    **路径/Query**：无。

    **请求体 `UpsertDocumentRequest`**
    - `dataset_id`、`doc_name`：必填。
    - `content`：必填。内联、路径/`file://`，或开启 URL 拉取时的 `http(s)://`（与 jobs/ingest 一致）。
    - `namespace`、`source_type`、`source_uri`、`description`、`metadata`：可选；`source_uri` 仅元数据，不拉文件。
    - `namespace_kb_enabled`：可选，namespace 级是否启用；默认 true（语义同 ``POST /rag/jobs/ingest``）。
    - `namespace_kb_priority`：可选，namespace 级召回优先级，数值越小越优先；默认 1，须 >= 1。
    - `chunk_size`、`chunk_overlap`、`min_chunk_size`：可选切块参数。
    - 扫描 PDF：需 `MINERU_ENABLED` 与 mineru-api，与异步任务一致。
    - 同步写入向量 chunk 与 docs 索引文档登记（便于 ``GET /rag/namespaces`` 与 PATCH kb-config）。

    **响应体 `UpsertDocumentResponse`（200）**
    - `ok`、`doc_name`、`chunk_count`、`stats`。

    失败时 HTTP 5xx，`detail` 为错误信息。
    """
    try:
        cfg = ChunkingConfig(
            chunk_size=req.chunk_size,
            chunk_overlap=req.chunk_overlap,
            min_chunk_size=req.min_chunk_size,
        )
        pipeline = DocumentPipeline(cfg)
        ns = _ensure_ingest_namespace(req.namespace)
        enabled, priority = resolve_namespace_kb_for_ingest(
            ns, req.namespace_kb_enabled, req.namespace_kb_priority
        )
        doc = DocumentSource(
            dataset_id=req.dataset_id,
            doc_name=req.doc_name,
            namespace=ns,
            content=req.content,
            source_type=req.source_type,
            source_uri=req.source_uri,
            description=req.description,
            replace_if_exists=True,
            metadata=req.metadata,
            namespace_kb_enabled=enabled,
            namespace_kb_priority=priority,
        )
        tmp_fetched = None
        try:
            doc, tmp_fetched = materialize_document_content_from_url(doc)
            doc, _ = prepare_pdf_document_for_pipeline(doc)
            from app.rag.document_pipeline.ingest_document import build_chunks_for_document

            chunks, stats, _figure_metrics = build_chunks_for_document(doc, pipeline)
        finally:
            if tmp_fetched is not None:
                tmp_fetched.unlink(missing_ok=True)
        if not chunks:
            raise ValueError("no chunks generated after processing")
        chunk_metadatas = build_chunk_metadatas(doc, chunks)
        _get_service().ingest_texts(
            dataset_id=req.dataset_id,
            texts=[c.text for c in chunks],
            description=req.description,
            namespace=ns,
            doc_name=req.doc_name,
            replace_if_exists=True,
            metadatas=chunk_metadatas,
        )
        _get_doc_repo().upsert_document_record(
            doc,
            chunk_count=len(chunks),
            status="SUCCESS",
        )
        return UpsertDocumentResponse(
            ok=True, doc_name=req.doc_name, chunk_count=len(chunks), stats=stats
        )
    except ValueError as e:
        if "namespace is required" in str(e):
            raise HTTPException(status_code=400, detail=str(e)) from e
        logger.exception("rag upsert_document failed: doc_name=%s", req.doc_name)
        raise HTTPException(status_code=400, detail=f"RAG upsert_document failed: {e}") from e
    except Exception as e:  # noqa: BLE001
        logger.exception("rag upsert_document failed: doc_name=%s", req.doc_name)
        raise HTTPException(status_code=500, detail=f"RAG upsert_document failed: {e}") from e


class DeleteDocumentRequest(BaseModel):
    """按 doc_name（及可选 namespace/version）删除向量库中的 chunk。字段见各 Field description。"""

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "doc_name": "readme",
                "namespace": "docs",
                "doc_version": "v1",
            }
        }
    )

    doc_name: str = Field(..., description="必填。文档名称。")
    namespace: str | None = Field(
        None, description="可选。命名空间；不传则跨命名空间删除。"
    )
    doc_version: str | None = Field(
        None, description="可选。文档版本；传入时按版本精确删除。"
    )


@router.post(
    "/documents/delete",
    summary="按文档名删除已摄入知识",
    response_model=DeleteDocumentResponse,
    response_description="deleted 为 chunk 删除条数；doc_records_deleted 为 docs 元数据删除条数（overview 数据源）。",
)
async def delete_document(req: DeleteDocumentRequest) -> DeleteDocumentResponse:
    """
    按文档删除已摄入的 chunk（可选缩小 namespace / doc_version 范围）。

    单篇删除请用本接口；**清空整个 namespace** 请用 ``POST /rag/namespaces/{namespace}/purge``。

    **路径/Query**：无。

    **请求体 `DeleteDocumentRequest`**
    - 必填：`doc_name`。
    - 可选：`namespace`、`doc_version`（传入则仅删匹配版本）；不传 `namespace` 时按 `doc_name` 跨所有 namespace 删除。

    **响应体 `DeleteDocumentResponse`（200）**
    - `ok`、`deleted`（向量 chunk 删除条数，无匹配时可为 0）。
    - `doc_records_deleted`（docs 索引中的文档元数据删除条数；管理面 overview 依赖此项）。

    失败时 HTTP 5xx，`detail` 为错误信息。
    """
    try:
        rows = _get_doc_repo().list(
            limit=100_000,
            offset=0,
            namespace=req.namespace,
            doc_name=req.doc_name,
            doc_version=req.doc_version,
        )
        _delete_original_objects(rows)
        deleted = _get_service().delete_by_doc_name(
            doc_name=req.doc_name, namespace=req.namespace, doc_version=req.doc_version
        )
        doc_records_deleted = _get_doc_repo().delete_by_doc_name(
            doc_name=req.doc_name, namespace=req.namespace, doc_version=req.doc_version
        )
        return DeleteDocumentResponse(
            ok=True, deleted=deleted, doc_records_deleted=doc_records_deleted
        )
    except Exception as e:  # noqa: BLE001
        logger.exception("rag delete_document failed: doc_name=%s namespace=%s", req.doc_name, req.namespace)
        raise HTTPException(status_code=500, detail=f"RAG delete_document failed: {e}") from e


class QueryRequest(BaseModel):
    """RAG 检索调试请求（走 RAGService 混合检索 + 场景 profile，非对话 Graph 路由）。字段见各 Field description。"""

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "query": "如何配置 RAG？",
                "top_k": 5,
                "namespace": "docs",
                "scene": "llm_inference",
            }
        }
    )

    query: str = Field(..., description="必填。检索问句或关键词。")
    top_k: int | None = Field(
        None,
        description="可选。返回片段条数上限；不传则用该 scene 的默认 top_k（见 RAG_SCENE_* 配置）。",
    )
    namespace: str | None = Field(
        None, description="可选。仅检索该命名空间；不传则不限定。"
    )
    scene: str = Field(
        "llm_inference",
        description="可选，默认 llm_inference。场景键：llm_inference / chatbot / analysis / nl2sql。",
    )
    query_image_url: str | None = Field(
        None,
        description="可选。用户附图 URL；需 RAG_QUERY_VISION_AUGMENT_ENABLED=true 时用于增强检索 query。",
    )


class PresignAssetResponse(BaseModel):
    ok: bool = Field(True, description="是否成功")
    image_url: str = Field(..., description="新的预签名或可访问 URL")


@router.get(
    "/assets/presign",
    summary="刷新 RAG figure 图片预签名 URL",
    response_model=PresignAssetResponse,
)
async def presign_rag_asset(key: Annotated[str, Query(description="MinIO object key 或本地存储路径")]) -> PresignAssetResponse:
    """
    按 ``image_object_key`` 重新签发 MinIO 预签名 GET URL（预签名过期后前端可调用刷新）。

    需 MinIO 后端；本地存储模式返回静态路径 URL。
    """
    try:
        from app.rag.asset_storage import RagAssetStorage

        url = RagAssetStorage().presign_get_url(key)
        return PresignAssetResponse(ok=True, image_url=url)
    except Exception as e:  # noqa: BLE001
        logger.exception("rag presign failed: key=%s", key[:120])
        raise HTTPException(status_code=400, detail=f"RAG presign failed: {e}") from e


@router.post(
    "/query",
    summary="查询 RAG 知识库（调试/冒烟）",
    response_model=QueryRagResponse,
    response_description="snippets 为文本列表。与启用 GraphRAG 时的 /chatbot 链路不完全一致。",
)
async def query_rag(req: QueryRequest) -> QueryRagResponse:
    """
    查询 RAG 知识库并返回上下文文本片段（调试/冒烟；与 GraphRAG 对话链路不完全一致）。

    **路径/Query**：无。

    **请求体 `QueryRequest`**
    - 必填：`query`。
    - 可选：`top_k`、`namespace`、`scene`（默认 llm_inference）、`query_image_url`。
    - 召回自动过滤 ``namespace_kb_enabled=false`` 的 chunk；未指定 `namespace` 时按 priority 参与排序（见环境变量）。

    **响应体 `QueryRagResponse`（200）**
    - `ok`、`query`、`count`、`snippets`（文本片段列表）。

    失败时 HTTP 5xx，`detail` 为错误信息。
    """
    try:
        snippets = _get_service().query(
            query=req.query,
            top_k=req.top_k,
            namespace=req.namespace,
            scene=req.scene,
            query_image_url=req.query_image_url,
        )
        return QueryRagResponse(
            ok=True, query=req.query, count=len(snippets), snippets=snippets
        )
    except Exception as e:  # noqa: BLE001
        logger.exception("rag query failed: query=%s namespace=%s scene=%s", req.query, req.namespace, req.scene)
        raise HTTPException(status_code=500, detail=f"RAG query failed: {e}") from e


class Nl2sqlAutoQaItem(BaseModel):
    """NL2SQL 闭环自动写入的向量条目（namespace=nl2sql_qa_examples）。"""

    doc_name: str | None = Field(None, description="文档逻辑名；由五元组哈希生成；PATCH 的主键（改五元组请删后靠新写入生成新 doc_name）")
    ext_id: str | None = Field(None, description="底层向量存储中的条目 id（若后端提供）")
    namespace: str | None = Field(None, description="命名空间，系统自动写入固定为 nl2sql_qa_examples")
    analysis_type: str | None = Field(None, description="专项类型（来自 metadata.analysis_type）")
    plan_item_id: str | None = Field(None, description="数据计划子任务 id，如 q1（来自 metadata.plan_item_id）")
    plan_template_version: str | None = Field(
        None,
        description="数据计划模板版本 v1/v2（来自 metadata.plan_template_version；参与去重五元组）",
    )
    dedup_key: str | None = Field(None, description="明文去重键（metadata.dedup_key，五元组拼接）")
    text: str | None = Field(
        None,
        description="入库向量对应的完整文本（由 format_nl2sql_qa_embedding_text 拼装：问句摘要 + 可选前缀摘要 + SQL）",
    )
    metadata: dict[str, Any] = Field(
        default_factory=dict,
        description=(
            "索引元数据：含 ingest_source、nl2sql_auto_kind、data_source_fp、schema_fp、policy_fp、"
            "analysis_type、plan_item_id、plan_template_version、dedup_key、question_normalized 等；"
            "向量库 doc_version 固定 auto_v1（技术字段，不参与业务去重）"
        ),
    )


class Nl2sqlAutoQaListResponse(BaseModel):
    ok: bool = Field(True, description="是否成功")
    count: int = Field(..., description="本次返回的条目条数")
    items: list[Nl2sqlAutoQaItem] = Field(default_factory=list, description="条目列表")


class Nl2sqlAutoQaUpdateRequest(BaseModel):
    doc_name: str = Field(..., description="必填。与 GET 列表返回的 doc_name 一致，指定要更新的系统自动 QA 文档")
    question: str = Field(..., description="必填。更新后的用户问题全文（写入前会经 compact_nl2sql_feedback_question / normalize 参与向量正文与 doc_name 无关字段）")
    sql: str = Field(..., description="必填。替换后的只读 SELECT SQL（写入向量正文「校验通过的 SQL」段）")
    prompt_prefix_snapshot: str | None = Field(
        None,
        description=(
            "可选。**预制提示前缀快照**：与 NL2SQL 链路上自动写入时传入的 `system_prefix` 同语义——即 "
            "resolved 后的 nl2sql 场景 System 模板前缀（可含 {{NL2SQL_SCHEMA_CATALOG}} 替换后的目录摘要等）。"
            "传入后由 `format_nl2sql_qa_embedding_text` 写入向量正文中的「【预制提示前缀摘要】」段；"
            "受环境变量 **NL2SQL_QA_EMBED_PREFIX_MAX_CHARS** 截断（默认 0 表示不向量化前缀，仅问句+SQL）。"
            "运维修正 QA 时通常留空或与自动写入时一致；若需 Few-shot 附带短规则可填精简文本。"
        ),
    )
    metadata_patch: dict[str, Any] | None = Field(
        None,
        description=(
            "可选。与列举到的现有 metadata **浅合并**后重写索引（**勿随意删除** data_source_fp、schema_fp、policy_fp 等指纹键，否则检索过滤会失效）。"
            "合并后会按 analysis_type + plan_item_id + plan_template_version 重算 dedup_key；"
            "**勿在 patch 中修改 analysis_type / plan_item_id / plan_template_version**（doc_name 仍为原五元组哈希，"
            "与新区间不一致；若需迁移版本请删除本条后重新触发综合分析写入）。"
        ),
    )


class Nl2sqlAutoQaPatchResponse(BaseModel):
    ok: bool = Field(True, description="是否成功")
    doc_name: str = Field(..., description="已更新的文档名，与请求一致")


class Nl2sqlAutoQaCreateRequest(BaseModel):
    question: str = Field(..., description="必填。与 plan 子任务问句一致（或 compact 后的业务问句）")
    sql: str = Field(..., description="必填。只读 SELECT SQL；默认经 SQLValidator 校验后入库")
    analysis_type: str = Field(..., description="必填。专项类型，如 overheat_guidance")
    plan_item_id: str = Field(..., description="必填。数据计划子任务 id，如 q2a（方案 B 15 条之一）")
    plan_template_version: str | None = Field(
        None,
        description="可选。数据计划模板版本 v1/v2；省略时归一化为 unknown，参与五元组去重",
    )
    mode: str = Field(
        "replace",
        description="写入模式：replace=按五元组删后写（半自动默认）；skip_if_exists=已存在则跳过（同运行时自动写入）",
    )
    prompt_prefix_snapshot: str | None = Field(
        None,
        description="可选。预制 System 前缀快照；多数运维补录可省略（见 PATCH 说明）",
    )
    data_source_fp: str | None = Field(
        None,
        description="可选。数据源指纹；省略时用当前应用 DB 配置计算",
    )
    schema_fp: str | None = Field(
        None,
        description="可选。schema 指纹；省略时用内存 Schema 目录表名列表计算",
    )
    policy_fp: str | None = Field(
        None,
        description="可选。策略指纹；省略时按 analysis_type 计算",
    )
    validate_sql: bool = Field(
        True,
        description="为 true 时用 SQLValidator 做只读 SELECT 校验；失败返回 422",
    )


class Nl2sqlAutoQaCreateResponse(BaseModel):
    ok: bool = Field(True, description="是否成功")
    doc_name: str = Field(..., description="五元组哈希后的文档名")
    created: bool = Field(
        ...,
        description="true=本次已写入；false=skip_if_exists 下条目已存在未写入",
    )
    dedup_key: str = Field(..., description="明文去重键（五元组拼接）")


@router.post(
    "/nl2sql-auto-qa",
    summary="半自动写入 NL2SQL 系统自动 QA（按五元组创建或覆盖）",
    response_model=Nl2sqlAutoQaCreateResponse,
    response_description="写入或跳过后的 doc_name、created、dedup_key。",
)
async def post_nl2sql_auto_qa(req: Nl2sqlAutoQaCreateRequest) -> Nl2sqlAutoQaCreateResponse:
    """
    运维半自动灌库：按 **`(namespace, ingest_source, analysis_type, plan_item_id, plan_template_version)`**
    定位 `doc_name`，将问句 + SQL 写入 **`nl2sql_qa_examples`**（与运行时自动闭环同一向量格式与 metadata）。

    **`Nl2sqlAutoQaCreateRequest`（请求体）**
    - `question` / `sql`：**必填**。
    - `analysis_type` / `plan_item_id`：**必填**（直连 NL2SQL 无 plan_item_id 的场景不支持）。
    - `plan_template_version`：可选，默认 unknown。
    - `mode`：**`replace`（默认）** 已存在则删后写；**`skip_if_exists`** 与 `upsert_nl2sql_auto_qa_pair` 一致。
    - `data_source_fp` / `schema_fp` / `policy_fp`：均可省略，由服务端按当前 DB 与 Schema 目录解析。
    - `validate_sql`：默认 true，非只读 SELECT 返回 **422**。

    **响应 `Nl2sqlAutoQaCreateResponse`（200）**
    - `doc_name`、`created`、`dedup_key`。

    **错误**
    - **400**：`mode` 非法或缺少 analysis_type/plan_item_id。
    - **422**：SQL 校验未通过（`validate_sql=true`）。
    - **5xx**：索引失败。
    """
    from app.nl2sql.qa_feedback import (
        create_nl2sql_auto_qa_entry,
        resolve_nl2sql_qa_fingerprints,
    )
    from app.nl2sql.validator import SQLValidator

    mode = (req.mode or "replace").strip()
    if mode not in ("skip_if_exists", "replace"):
        raise HTTPException(
            status_code=400,
            detail=f"invalid mode: {mode!r}; use skip_if_exists or replace",
        )

    sql = (req.sql or "").strip()
    if req.validate_sql:
        validator = SQLValidator()
        normalized = validator.normalize_sql(sql)
        if not normalized or not validator.validate(normalized):
            raise HTTPException(
                status_code=422,
                detail="SQL must be a non-empty read-only SELECT (or WITH) statement",
            )
        sql = normalized

    try:
        fps = resolve_nl2sql_qa_fingerprints(
            data_source_fp=req.data_source_fp,
            schema_fp=req.schema_fp,
            policy_fp=req.policy_fp,
            analysis_type=req.analysis_type,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e

    rag = _get_service()._rag_service  # noqa: SLF001
    try:
        doc_name, created, dedup_key = create_nl2sql_auto_qa_entry(
            rag,
            question=req.question,
            sql=sql,
            data_source_fp=fps.data_source_fp,
            schema_fp=fps.schema_fp,
            policy_fp=fps.policy_fp,
            analysis_type=req.analysis_type,
            plan_item_id=req.plan_item_id,
            plan_template_version=req.plan_template_version,
            prompt_prefix_snapshot=req.prompt_prefix_snapshot,
            mode=mode,  # type: ignore[arg-type]
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except Exception as e:  # noqa: BLE001
        logger.exception(
            "post nl2sql-auto-qa failed analysis_type=%s plan_item_id=%s mode=%s",
            req.analysis_type,
            req.plan_item_id,
            mode,
        )
        raise HTTPException(status_code=500, detail=f"create failed: {e}") from e

    return Nl2sqlAutoQaCreateResponse(
        ok=True,
        doc_name=doc_name,
        created=created,
        dedup_key=dedup_key,
    )


@router.get(
    "/nl2sql-auto-qa",
    summary="列出 NL2SQL 系统自动写入的 QA 向量条目",
    response_model=Nl2sqlAutoQaListResponse,
    response_description="含条目条数与 nl2sql_qa_examples 命名空间下的文档详情（text、metadata）。",
)
async def list_nl2sql_auto_qa(
    limit: Annotated[int, Query(ge=1, le=5000, description="返回条数上限，默认 200")] = 200,
    analysis_type: Annotated[
        str | None, Query(description="可选。按 metadata.analysis_type 精确过滤，如 overheat_guidance")
    ] = None,
    plan_item_id: Annotated[
        str | None, Query(description="可选。按 metadata.plan_item_id 精确过滤，如 q1")
    ] = None,
    plan_template_version: Annotated[
        str | None,
        Query(description="可选。按 metadata.plan_template_version 精确过滤，如 v1、v2（空串视为 unknown）"),
    ] = None,
) -> Nl2sqlAutoQaListResponse:
    """
    列出由 NL2SQL 闭环自动写入、命名空间 **`nl2sql_qa_examples`** 下的 QA 条目（`ingest_source=auto`、`nl2sql_auto_kind` 等元数据标识）。

    **路径/Query**
    - `limit`：可选，默认 200，范围 1～5000；最多返回条数。
    - `analysis_type` / `plan_item_id` / `plan_template_version`：可选精确过滤（便于区分 plan v1 与 v2 下同名 q*）。

    **请求体**：无（GET）。

    **响应体 `Nl2sqlAutoQaListResponse`（200）**
    - `ok`：固定 true。
    - `count`：本次返回条数。
    - `items[]`：每条为 `Nl2sqlAutoQaItem`。
      - `doc_name` / `ext_id` / `namespace`：索引标识；`doc_name` 由五元组 `(namespace, ingest_source, analysis_type, plan_item_id, plan_template_version)` 哈希。
      - `analysis_type` / `plan_item_id` / `plan_template_version` / `dedup_key`：从 metadata 提取的便捷字段。
      - `text`：嵌入用拼接正文（问句 + 可选前缀摘要 + SQL），用于排查与对照更新。
      - `metadata`：完整指纹与业务标签（含 `plan_template_version`、`dedup_key` 等）。

    **存储后端差异**：Faiss 进程内实现可能全量扫描；Elasticsearch / EasySearch 走 metadata 条件召回（见 `list_nl2sql_auto_qa_entries`）。

    失败时 HTTP 5xx，`detail` 为错误信息。
    """
    from app.nl2sql.qa_feedback import list_nl2sql_auto_qa_entries

    rag = _get_service()._rag_service  # noqa: SLF001
    rows = list_nl2sql_auto_qa_entries(
        rag,
        limit=limit,
        analysis_type=analysis_type,
        plan_item_id=plan_item_id,
        plan_template_version=plan_template_version,
    )
    items = []
    for r in rows:
        meta = dict(r.get("metadata") or {})
        items.append(
            Nl2sqlAutoQaItem(
                doc_name=r.get("doc_name"),
                ext_id=r.get("ext_id"),
                namespace=r.get("namespace"),
                analysis_type=meta.get("analysis_type"),
                plan_item_id=meta.get("plan_item_id"),
                plan_template_version=meta.get("plan_template_version"),
                dedup_key=meta.get("dedup_key"),
                text=r.get("text"),
                metadata=meta,
            )
        )
    return Nl2sqlAutoQaListResponse(count=len(items), items=items)


@router.patch(
    "/nl2sql-auto-qa",
    summary="更新一条系统自动写入的 NL2SQL QA（同名先删后灌）",
    response_model=Nl2sqlAutoQaPatchResponse,
    response_description="更新成功后返回 ok 与 doc_name；未知 doc_name 时 404。",
)
async def patch_nl2sql_auto_qa(req: Nl2sqlAutoQaUpdateRequest) -> Nl2sqlAutoQaPatchResponse:
    """
    按 **`doc_name`** 修订一条系统自动写入的 QA：在 **`nl2sql_qa_examples`** 命名空间、**`doc_version=auto_v1`** 下 **先删后索引**
    （与自动闭环 `upsert_nl2sql_auto_qa_pair` 一致），重新生成向量正文并可选合并 metadata。

    **路径/Query**：无。

    **`Nl2sqlAutoQaUpdateRequest`（请求体）**
    - `doc_name`：**必填**。须为先前 **GET `/rag/nl2sql-auto-qa`** 列表中出现的文档名。
    - `question`：**必填**。更新后的完整问题字符串（写入链路会做 compact/normalize 用于向量文本）。
    - `sql`：**必填**。替换后的 **只读 SELECT** SQL。
    - `prompt_prefix_snapshot`：**可选**。与链路上 **`NL2SQLChain`** 成功写入 QA 时传入的 **预制 System 前缀快照**（`system_prefix`，即 resolved 后的 nl2sql 模板内容）同语义；用于拼入向量正文「【预制提示前缀摘要】」。默认部署 **`NL2SQL_QA_EMBED_PREFIX_MAX_CHARS=0`** 时该段会被丢弃，仅 **问句 + SQL** 参与嵌入；若提高该上限且传入非空前缀，可增强 Few-shot 上下文。**运维修正 SQL/问句时多数场景可省略（null）**。
    - `metadata_patch`：**可选**。与当前条目的 metadata **字典合并**后再入库；勿删除指纹键（`data_source_fp`、`schema_fp`、`policy_fp` 等）除非明确要切断检索过滤。合并后服务端会重算 `dedup_key`（**不**改 `doc_name`）；勿通过 patch 变更 `analysis_type` / `plan_item_id` / `plan_template_version` 来「换槽位」，应删文档后依赖新综合分析写入。

    **响应体 `Nl2sqlAutoQaPatchResponse`（200）**
    - `ok`：true。
    - `doc_name`：与请求一致。

    **错误**
    - **404**：列表中找不到给定 `doc_name`（非系统自动条目或拼写错误）。
    - **5xx**：删除/索引失败等，`detail` 为错误信息。
    """
    from app.nl2sql.qa_feedback import update_nl2sql_auto_qa_entry

    rag = _get_service()._rag_service  # noqa: SLF001
    try:
        update_nl2sql_auto_qa_entry(
            rag,
            doc_name=req.doc_name,
            question=req.question,
            sql=req.sql,
            prompt_prefix_snapshot=req.prompt_prefix_snapshot,
            metadata_patch=req.metadata_patch,
        )
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    except Exception as e:  # noqa: BLE001
        logger.exception("patch nl2sql-auto-qa failed doc_name=%s", req.doc_name)
        raise HTTPException(status_code=500, detail=f"update failed: {e}") from e
    return Nl2sqlAutoQaPatchResponse(ok=True, doc_name=req.doc_name)


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
    """
    **已废弃**：列出进程内登记的数据集（重启丢失，非 ES 权威）。

    **路径/Query**：无。

    **响应（200）**
    - `DatasetMetaResponse` 数组：`dataset_id`、`description`、`num_items`、`namespace`、`doc_name`。

    新集成请使用 `GET /rag/documents/meta` 或 `GET /rag/documents/overview`。
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
    """单篇文档在文档索引中的元数据一行。"""

    doc_name: str = Field(..., description="文档名")
    doc_version: str = Field("v1", description="文档版本")
    tenant_id: str | None = Field(None, description="租户 ID")
    dataset_id: str = Field(..., description="所属数据集 ID")
    namespace: str | None = Field(None, description="命名空间")
    source_type: str = Field("text", description="源类型")
    source_uri: str | None = Field(None, description="来源 URI")
    description: str | None = Field(None, description="文档简介（人读说明）")
    chunk_count: int = Field(0, description="关联 chunk 数量（若已统计）")
    pipeline_version: str | None = Field(None, description="摄入流水线版本")
    status: str | None = Field(None, description="文档状态：如 ready / failed 等")
    created_at: str | None = Field(None, description="创建时间")
    updated_at: str | None = Field(None, description="最后更新时间")
    last_job_id: str | None = Field(None, description="最近关联任务 ID")
    last_job_type: str | None = Field(None, description="最近任务类型")
    last_job_status: str | None = Field(None, description="最近任务状态")
    metadata: dict[str, Any] = Field(
        default_factory=dict,
        description=(
            "扩展元数据；含 namespace_kb_enabled、namespace_kb_priority（namespace 级启用与召回优先级）等。"
        ),
    )
    error: str | None = Field(None, description="失败时的错误摘要")


def _rag_ns_bucket(value: str | None) -> str | None:
    if value is None or value == "":
        return None
    return value


def _document_meta_item_from_payload(d: dict[str, Any]) -> DocumentMetaItem:
    return DocumentMetaItem(
        doc_name=d.get("doc_name", ""),
        doc_version=d.get("doc_version", "v1"),
        tenant_id=d.get("tenant_id"),
        dataset_id=d.get("dataset_id", ""),
        namespace=d.get("namespace"),
        source_type=d.get("source_type", "text"),
        source_uri=d.get("source_uri"),
        description=d.get("description"),
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


class UploadDocumentResponse(BaseModel):
    ok: bool = Field(True, description="是否成功")
    document: DocumentMetaItem = Field(..., description="上传后的文档登记（status=UPLOADED，未切块）")
    object_key: str = Field(..., description="稳定对象引用，摄入时作为 jobs/ingest 的 content")
    source_uri: str = Field(..., description="与 object_key 相同的稳定 URI")
    file_size: int = Field(..., description="原文件字节数")


@router.post(
    "/documents/upload",
    summary="上传知识原文（不摄入）",
    response_model=UploadDocumentResponse,
    response_description="写入对象存储并登记 docs（UPLOADED）；请再 POST /rag/jobs/ingest，content 用返回的 object_key。",
)
async def upload_document(
    file: Annotated[UploadFile, File(description="原文件")],
    namespace: Annotated[str, Form(description="必填。知识分类 / namespace，不允许为空")],
    dataset_id: Annotated[str | None, Form(description="数据集 ID；省略则用 RAG_DEFAULT_DATASET_ID")] = None,
    doc_name: Annotated[str | None, Form(description="文档逻辑名；省略则用文件名（去扩展名）")] = None,
    description: Annotated[str | None, Form(description="人读说明")] = None,
    doc_version: Annotated[str, Form(description="文档版本，默认 v1")] = "v1",
    tenant_id: Annotated[str | None, Form(description="租户 ID")] = None,
) -> UploadDocumentResponse:
    """
    仅上传原文到对象存储并写入 docs 登记（``status=UPLOADED``），**不**提交摄入任务。

    随后调用 ``POST /rag/jobs/ingest``，将 ``content`` / ``source_uri`` 设为响应中的 ``object_key``。
    """
    from pathlib import Path as _Path

    from app.rag.asset_storage import RagAssetStorage
    from app.rag.document_repository import make_document_storage_key
    from app.rag.models import utcnow_iso
    from app.rag.namespace_kb import merge_doc_metadata_for_record

    try:
        ns = _ensure_ingest_namespace(namespace, always=True)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e

    filename = _Path(file.filename or "upload.bin").name
    logical_name = (doc_name or "").strip() or _Path(filename).stem or "upload"
    cfg = get_app_config().rag
    ingest_cfg = cfg.ingestion
    ds = (dataset_id or "").strip() or ingest_cfg.default_dataset_id or "default"
    ver = (doc_version or "v1").strip() or "v1"
    td = (tenant_id or "").strip() or None
    max_bytes = int(cfg.content_fetch.max_bytes or 50 * 1024 * 1024)

    data = await file.read()
    if not data:
        raise HTTPException(status_code=400, detail="empty file upload")
    if len(data) > max_bytes:
        raise HTTPException(status_code=413, detail=f"file larger than {max_bytes} bytes")

    repo = _get_doc_repo()
    doc_key = make_document_storage_key(
        logical_name,
        namespace=ns,
        tenant_id=td,
        doc_version=ver,
        tenant_id_fallback=ingest_cfg.tenant_id_default or "default",
    )
    existing = repo.get(doc_key) or {}
    last_job_status = str(existing.get("last_job_status") or "")
    if last_job_status in {"PENDING", "RUNNING"}:
        raise HTTPException(status_code=409, detail="document has an in-progress ingestion job")

    source_type = guess_source_type(filename, file.content_type)
    try:
        stored = RagAssetStorage().upload_original(
            data=data,
            namespace=ns,
            doc_name=logical_name,
            doc_version=ver,
            filename=filename,
            content_type=(file.content_type or "application/octet-stream"),
        )
    except Exception as e:  # noqa: BLE001
        logger.exception("rag upload_document storage failed")
        raise HTTPException(status_code=500, detail=f"RAG upload failed: {e}") from e

    uri = str(stored.get("source_uri") or stored.get("object_key") or "")
    enabled, priority = resolve_namespace_kb_for_ingest(ns, None, None)
    meta = dict(existing.get("metadata") or {})
    meta.update(
        {
            META_OBJECT_KEY: uri,
            META_FILE_SIZE: int(stored.get("bytes") or len(data)),
            META_ORIGINAL_FILENAME: filename,
        }
    )
    doc = DocumentSource(
        dataset_id=ds,
        doc_name=logical_name,
        namespace=ns,
        content=uri,
        doc_version=ver,
        tenant_id=td,
        source_type=source_type,
        source_uri=uri,
        description=description if description is not None else existing.get("description"),
        metadata=meta,
        namespace_kb_enabled=enabled,
        namespace_kb_priority=priority,
    )
    created_at = existing.get("created_at") or utcnow_iso()
    payload = {
        "doc_name": logical_name,
        "doc_version": ver,
        "tenant_id": td,
        "dataset_id": ds,
        "namespace": ns,
        "source_type": source_type,
        "source_uri": uri,
        "description": doc.description,
        "chunk_count": int(existing.get("chunk_count") or 0),
        "pipeline_version": ingest_cfg.pipeline_version,
        "status": DOC_STATUS_UPLOADED,
        "created_at": created_at,
        "updated_at": utcnow_iso(),
        "last_job_id": existing.get("last_job_id"),
        "last_job_type": "upload",
        "last_job_status": DOC_STATUS_UPLOADED,
        "metadata": merge_doc_metadata_for_record(doc),
        "error": None,
    }
    repo.upsert(doc_key, payload)
    return UploadDocumentResponse(
        ok=True,
        document=_document_meta_item_from_payload(payload),
        object_key=uri,
        source_uri=uri,
        file_size=int(stored.get("bytes") or len(data)),
    )


class MoveDocumentNamespaceRequest(BaseModel):
    """将单篇文档从当前 namespace 迁到目标 namespace（同步更新向量 chunk + docs 索引）。"""

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "doc_name": "readme",
                "from_namespace": "docs",
                "to_namespace": "public",
                "tenant_id": "t1",
                "doc_version": "v1",
                "dataset_id": "company_kb",
                "repair_graph_async": True,
            }
        }
    )

    doc_name: str = Field(..., description="必填。文档名。")
    from_namespace: str | None = Field(
        None,
        description="可选。当前所在 namespace；省略或空字符串表示「默认分区」（与摄入时未传 namespace 一致）。",
    )
    to_namespace: str = Field(
        ...,
        description="必填。目标 namespace；空字符串表示迁回默认分区（存储为 null）。",
    )
    tenant_id: str | None = Field(None, description="可选。缩小匹配到指定租户。")
    doc_version: str | None = Field(None, description="可选。文档版本。")
    dataset_id: str | None = Field(None, description="可选。数据集 ID。")
    repair_graph_async: bool = Field(
        True,
        description=(
            "为 true 且开启 GraphRAG 时，在响应返回后异步删除旧 namespace 图数据并在新 namespace 重灌；"
            "需文档登记中含 `dataset_id`。"
        ),
    )


class MoveDocumentNamespaceResponse(BaseModel):
    ok: bool = Field(True, description="是否成功")
    chunks_updated: int = Field(..., description="向量库中更新的 chunk 条数")
    document: DocumentMetaItem = Field(..., description="迁移后的文档元数据视图")
    graph_repair_scheduled: bool = Field(
        False,
        description="是否已排队 GraphRAG 异步修复（仅当 GraphRAG 开启且满足 dataset_id 等条件时为 true）",
    )


@router.post(
    "/documents/namespace/move",
    summary="迁移单篇文档到新 namespace（向量 + docs 索引）",
    response_model=MoveDocumentNamespaceResponse,
    response_description="先更新 chunk 与 docs 登记；GraphRAG 在响应后异步修复（可关 `repair_graph_async`）。",
)
async def move_document_namespace(
    req: MoveDocumentNamespaceRequest,
    background_tasks: BackgroundTasks,
) -> MoveDocumentNamespaceResponse:
    """
    将单篇文档从源 namespace 迁到目标 namespace：同步更新向量 chunk 的 namespace 与文档登记索引；可选在响应返回后异步修复 GraphRAG。

    **执行概要**：1) 向量侧改写匹配 chunk 的 ``namespace``；2) 文档索引删除旧 ``doc_key``、写入新 ``doc_key``；
    3) 若 ``repair_graph_async=true`` 且开启 GraphRAG 且登记含 ``dataset_id``，则在返回后异步删旧图数据并重灌新 namespace。

    Args:
        req (MoveDocumentNamespaceRequest): JSON 请求体。
            - ``doc_name`` (str): 必填，待迁移的文档名。
            - ``from_namespace`` (str | None): 可选；当前所在 namespace，省略或空字符串表示默认分区（与摄入时未传 namespace 一致）。
            - ``to_namespace`` (str): 必填；目标 namespace，空字符串表示迁回默认分区。
            - ``tenant_id`` (str | None): 可选，缩小匹配到指定租户。
            - ``doc_version`` (str | None): 可选，文档版本。
            - ``dataset_id`` (str | None): 可选，数据集 ID；异步 Graph 修复依赖登记中的 ``dataset_id``。
            - ``repair_graph_async`` (bool): 默认 true；为 true 且 GraphRAG 开启且满足条件时，在**响应返回后**排队图修复（失败仅记日志）。
        background_tasks (BackgroundTasks): FastAPI 后台任务，用于挂载上述异步图修复。

    Returns:
        MoveDocumentNamespaceResponse: 200 时返回。
            - ``ok`` (bool): 是否成功完成同步步骤。
            - ``chunks_updated`` (int): 向量库中更新的 chunk 条数。
            - ``document`` (DocumentMetaItem): 迁移后的文档元数据视图。
            - ``graph_repair_scheduled`` (bool): 是否已排队 GraphRAG 异步修复。

    Raises:
        HTTPException: ``400`` — 源与目标解析为同一分区、或多条匹配等业务校验失败；
            ``404`` — 未找到唯一匹配的文档登记；
            ``409`` — 目标 namespace 已存在相同 tenant/doc_name/version 的登记；
            ``500`` — 向量或文档索引更新异常。请求体验证失败时由框架返回 ``422``。
    """
    from_ns = _rag_ns_bucket(req.from_namespace)
    to_ns = _rag_ns_bucket(req.to_namespace)
    try:
        if to_ns is None:
            _ensure_ingest_namespace(to_ns)
        else:
            to_ns = _ensure_ingest_namespace(to_ns) or to_ns
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    if from_ns == to_ns:
        raise HTTPException(
            status_code=400,
            detail="from_namespace and to_namespace resolve to the same partition",
        )
    try:
        chunks_updated = _get_service().reassign_namespace_for_doc(
            doc_name=req.doc_name,
            from_namespace=from_ns,
            to_namespace=to_ns,
            doc_version=req.doc_version,
        )
        payload = _get_doc_repo().move_document_to_namespace(
            req.doc_name,
            from_namespace=from_ns,
            to_namespace=to_ns,
            tenant_id=req.tenant_id,
            doc_version=req.doc_version,
            dataset_id=req.dataset_id,
        )
    except LookupError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    except ValueError as e:
        msg = str(e)
        if "target namespace already" in msg:
            raise HTTPException(status_code=409, detail=msg) from e
        raise HTTPException(status_code=400, detail=msg) from e
    except Exception as e:  # noqa: BLE001
        logger.exception("rag move_document_namespace failed: doc_name=%s", req.doc_name)
        raise HTTPException(status_code=500, detail=f"RAG move_document_namespace failed: {e}") from e

    graph_repair_scheduled = False
    if req.repair_graph_async and get_app_config().rag.graph.enabled and get_app_config().rag.graph.ingest_on_rag:
        ds = str(payload.get("dataset_id") or "").strip()
        if ds:
            background_tasks.add_task(
                run_graph_resync_after_namespace_move,
                doc_name=req.doc_name,
                from_namespace=from_ns,
                to_namespace=to_ns,
                doc_version=req.doc_version,
                dataset_id=ds,
            )
            graph_repair_scheduled = True
        else:
            logger.warning(
                "graph async repair skipped: doc record has no dataset_id doc_name=%s",
                req.doc_name,
            )

    return MoveDocumentNamespaceResponse(
        ok=True,
        chunks_updated=chunks_updated,
        document=_document_meta_item_from_payload(payload),
        graph_repair_scheduled=graph_repair_scheduled,
    )


class NamespaceKbConfigItem(BaseModel):
    namespace: str | None = Field(None, description="命名空间；null 表示默认分区")
    namespace_kb_enabled: bool = Field(..., description="是否启用")
    namespace_kb_priority: int = Field(..., description="优先级，数值越小越优先")
    document_count: int = Field(0, description="该 namespace 下文档数（docs 索引）")


class NamespaceKbConfigListResponse(BaseModel):
    ok: bool = Field(True, description="是否成功")
    namespaces: List[NamespaceKbConfigItem] = Field(default_factory=list, description="namespace 配置列表")


class PatchNamespaceKbConfigRequest(BaseModel):
    namespace_kb_enabled: bool = Field(..., description="是否启用该 namespace 知识库")
    namespace_kb_priority: int = Field(..., ge=1, description="优先级，数值越小越优先，须 >= 1")


class PatchNamespaceKbConfigResponse(BaseModel):
    ok: bool = Field(True, description="是否成功")
    namespace: str | None = Field(None, description="目标 namespace")
    namespace_kb_enabled: bool = Field(..., description="更新后的启用状态")
    namespace_kb_priority: int = Field(..., description="更新后的优先级")
    chunks_updated: int = Field(..., description="向量库更新 chunk 数")
    docs_updated: int = Field(..., description="docs 索引更新文档数")


@router.get(
    "/namespaces",
    summary="列出各 namespace 的 kb 启用/优先级配置",
    response_model=NamespaceKbConfigListResponse,
)
async def list_namespace_kb_configs() -> NamespaceKbConfigListResponse:
    """
    列出各 namespace 的 ``namespace_kb_enabled`` / ``namespace_kb_priority`` 配置（从 docs 索引聚合）。

    **路径/Query**：无。

    **响应体 `NamespaceKbConfigListResponse`（200）**
    - `ok`、`namespaces[]`：每项含 `namespace`、`namespace_kb_enabled`、`namespace_kb_priority`、`document_count`。
    - 默认分区在列表中 `namespace` 为 null。

    失败时 HTTP 5xx，`detail` 为错误信息。
    """
    try:
        rows = _get_doc_repo().list_namespace_kb_configs()
        return NamespaceKbConfigListResponse(
            ok=True,
            namespaces=[NamespaceKbConfigItem(**row) for row in rows],
        )
    except Exception as e:  # noqa: BLE001
        logger.exception("rag list_namespace_kb_configs failed")
        raise HTTPException(status_code=500, detail=f"RAG list_namespace_kb_configs failed: {e}") from e


@router.patch(
    "/namespaces/{namespace}/kb-config",
    summary="批量更新 namespace 下全部文档/chunk 的 kb 配置(是否启用namespace_kb_enabled，优先级namespace_kb_priority)",
    response_model=PatchNamespaceKbConfigResponse,
)
async def patch_namespace_kb_config(
    namespace: Annotated[
        str,
        Path(description=f"命名空间；默认分区请传 `{DEFAULT_NAMESPACE_PATH}`"),
    ],
    req: PatchNamespaceKbConfigRequest,
) -> PatchNamespaceKbConfigResponse:
    """
    批量更新指定 namespace 下**全部**文档与 chunk 的 kb 配置（无需重新摄入）。

    **路径参数**
    - `namespace`：目标分区；默认分区请传 ``__default__``。

    **请求体 `PatchNamespaceKbConfigRequest`**
    - `namespace_kb_enabled`：必填，是否启用该 namespace 知识库召回。
    - `namespace_kb_priority`：必填，优先级（数值越小越优先，须 >= 1）。

    **响应体 `PatchNamespaceKbConfigResponse`（200）**
    - `ok`、`namespace`、`namespace_kb_enabled`、`namespace_kb_priority`。
    - `chunks_updated`、`docs_updated`：向量库与 docs 索引实际更新条数。

    失败时 HTTP 5xx，`detail` 为错误信息。
    """
    ns = namespace_from_path_param(namespace)
    try:
        result = _get_service().update_namespace_kb_config(
            ns,
            enabled=req.namespace_kb_enabled,
            priority=req.namespace_kb_priority,
        )
        return PatchNamespaceKbConfigResponse(
            ok=True,
            namespace=ns,
            namespace_kb_enabled=req.namespace_kb_enabled,
            namespace_kb_priority=req.namespace_kb_priority,
            chunks_updated=int(result.get("chunks_updated", 0)),
            docs_updated=int(result.get("docs_updated", 0)),
        )
    except Exception as e:  # noqa: BLE001
        logger.exception("rag patch_namespace_kb_config failed namespace=%s", ns)
        raise HTTPException(status_code=500, detail=f"RAG patch_namespace_kb_config failed: {e}") from e


class PurgeNamespaceRequest(BaseModel):
    confirm: bool = Field(
        True,
        description="必须为 true 才执行清空（防误操作）；默认 true 便于脚本调用。",
    )


class PurgeNamespaceResponse(BaseModel):
    ok: bool = Field(True, description="是否成功")
    namespace: str | None = Field(None, description="被清空的 namespace；null 表示默认分区")
    chunks_deleted: int = Field(..., description="删除的向量 chunk 条数")
    doc_records_deleted: int = Field(..., description="删除的 docs 索引文档条数")
    documents_purged: int = Field(..., description="清空前该 namespace 下登记的文档数（用于 figure/graph 清理）")


@router.post(
    "/namespaces/{namespace}/purge",
    summary="按 namespace 整库清空（删除该分区下全部 chunk 与文档元数据）",
    response_model=PurgeNamespaceResponse,
)
async def purge_namespace_documents(
    namespace: Annotated[
        str,
        Path(description=f"命名空间；默认分区请传 `{DEFAULT_NAMESPACE_PATH}`"),
    ],
    req: PurgeNamespaceRequest,
) -> PurgeNamespaceResponse:
    """
    清空指定 namespace 下全部已摄入知识（整库删除，不可恢复）。

    与 ``POST /rag/documents/delete``（单 `doc_name`）互补。

    **路径参数**
    - `namespace`：目标分区；默认分区请传 ``__default__``。

    **请求体 `PurgeNamespaceRequest`**
    - `confirm`：必填须为 `true`，否则返回 400（防误操作）。

    **执行范围**
    - 删除向量库中该 namespace 的全部 chunk；
    - 删除 docs 索引中该 namespace 的全部文档登记；
    - 若开启 figure / GraphRAG，按登记文档逐篇清理关联资源。

    **响应体 `PurgeNamespaceResponse`（200）**
    - `ok`、`namespace`、`chunks_deleted`、`doc_records_deleted`、`documents_purged`。

    失败时 HTTP 5xx，`detail` 为错误信息。
    """
    if not req.confirm:
        raise HTTPException(status_code=400, detail="confirm must be true to purge namespace")
    ns = namespace_from_path_param(namespace)
    try:
        result = _get_service().delete_by_namespace(ns)
        return PurgeNamespaceResponse(
            ok=True,
            namespace=ns,
            chunks_deleted=int(result.get("chunks_deleted", 0)),
            doc_records_deleted=int(result.get("doc_records_deleted", 0)),
            documents_purged=int(result.get("documents_purged", 0)),
        )
    except Exception as e:  # noqa: BLE001
        logger.exception("rag purge_namespace_documents failed namespace=%s", ns)
        raise HTTPException(status_code=500, detail=f"RAG purge_namespace_documents failed: {e}") from e


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
    doc_name: Annotated[str | None, Query(description="按文档名精确过滤")] = None,
    doc_name_contains: Annotated[str | None, Query(description="按文档名包含匹配（管理台顶栏模糊搜索）")] = None,
) -> DocumentMetaListResponse:
    """
    分页查询文档元数据（管理面清单；数据来自文档索引，非 chunk 正文）。

    **Query**
    - `limit`：可选，默认 20，每页条数（1～500）。
    - `offset`：可选，默认 0。
    - `namespace`、`tenant_id`、`dataset_id`、`doc_name`：可选过滤条件。

    **响应体 `DocumentMetaListResponse`（200）**
    - `ok`、`limit`、`offset`、`namespace`（请求使用的过滤）、`documents`（`DocumentMetaItem` 列表）。
    - 每篇 `metadata` 可含 ``namespace_kb_enabled`` / ``namespace_kb_priority``；汇总各 namespace 配置请用 ``GET /rag/namespaces``。

    失败时 HTTP 5xx，`detail` 为错误信息。
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
                doc_name_contains=doc_name_contains,
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
                description=d.get("description"),
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
    doc_name: Annotated[str | None, Query(description="过滤文档名（精确）")] = None,
    doc_name_contains: Annotated[str | None, Query(description="按文档名包含匹配（管理台顶栏模糊搜索）")] = None,
) -> KnowledgeOverviewResponse:
    """
    知识库总览：当前过滤条件下的聚合统计 + 文档明细分页。

    **Query**
    - `limit`：可选，默认 20，明细每页条数（1～500）。
    - `offset`：可选，默认 0。
    - `namespace`、`tenant_id`、`dataset_id`、`doc_name`：可选过滤条件。

    **响应体 `KnowledgeOverviewResponse`（200）**
    - `ok`、回显过滤字段、`total_documents`、`total_doc_names`、
      `by_namespace` / `by_tenant` / `by_status`（分桶）、`documents`（`DocumentMetaItem` 分页列表）。
    - 各 namespace 的 kb 启用/优先级汇总请用 ``GET /rag/namespaces``；单篇明细见 `documents[].metadata`。

    失败时 HTTP 5xx，`detail` 为错误信息。
    """
    try:
        repo = _get_doc_repo()
        try:
            stats = repo.overview(
                namespace=namespace,
                tenant_id=tenant_id,
                dataset_id=dataset_id,
                doc_name_contains=doc_name_contains,
            )
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
                doc_name_contains=doc_name_contains,
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
                description=d.get("description"),
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
    知识库运营趋势（基于任务索引的成功/失败统计）。

    **Query**
    - `granularity`：可选，默认 day；`day` 或 `week`。
    - `days`：可选，默认 30，统计窗口天数（1～180）。

    **响应体 `KnowledgeTrendsResponse`（200）**
    - `ok`、`granularity`、`days`、`points`（`KnowledgeTrendPoint`：`bucket`、`created_success`、`updated_success`、`failed`）。

    **统计口径（`points` 内字段）**
    - `created_success`：FULL 成功数量（视为新增）。
    - `updated_success`：UPSERT 成功数量（视为更新）。
    - `failed`：FAILED 数量（任意 job_type）。

    失败时 HTTP 5xx，`detail` 为错误信息。
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
    """执行 chunks 索引迁移请求体。"""

    model_config = ConfigDict(json_schema_extra={"example": {"embedding_dim": 1024}})

    embedding_dim: int = Field(
        ..., description="必填。向量维度（如 768/1024），须与嵌入模型一致。"
    )


class RollbackChunksMigrationRequest(BaseModel):
    """chunks alias 回滚请求体。"""

    model_config = ConfigDict(
        json_schema_extra={"example": {"previous_index": "rag_knowledge_base_v1"}}
    )

    previous_index: str = Field(
        ..., description="必填。回滚目标物理索引名，例如 rag_knowledge_base_v1。"
    )


@router.post(
    "/migrations/chunks/run",
    summary="执行 chunks 索引迁移（创建并切换 alias）",
    response_model=ChunksMigrationRunResponse,
    response_description="创建新物理索引并切换 alias；old_indices 为被替换下的旧索引名列表。",
)
async def run_chunks_migration(req: RunChunksMigrationRequest) -> ChunksMigrationRunResponse:
    """
    创建新 chunks 物理索引并切换 alias（运维/升级向量维度时使用）。

    **路径/Query**：无。

    **请求体 `RunChunksMigrationRequest`**
    - 必填：`embedding_dim`（与嵌入模型维度一致）。

    **响应体 `ChunksMigrationRunResponse`（200）**
    - `ok`、`alias`、`new_index`、`old_indices`（被替换下的旧物理索引名列表）。

    失败时 HTTP 5xx，`detail` 为错误信息。
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
    将 chunks 逻辑 alias 指回指定物理索引。

    **路径/Query**：无。

    **请求体 `RollbackChunksMigrationRequest`**
    - 必填：`previous_index`（目标物理索引名）。

    **响应体 `ChunksMigrationRollbackResponse`（200）**
    - `ok`、`rolled_back_to`、`alias`。

    失败时 HTTP 5xx，`detail` 为错误信息。
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


@router.get(
    "/traces/{job_id}",
    summary="查询 RAG 摄入任务执行轨迹（统一 Store 别名）",
)
async def get_rag_ingest_trace(job_id: str):
    from app.api.trace_aliases import get_module_trace

    return get_module_trace(job_id, expected_module="rag_ingest")

