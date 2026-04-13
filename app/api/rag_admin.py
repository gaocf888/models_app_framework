from __future__ import annotations

"""
RAG 管理接口（对应《下一阶段工作清单》中的 TODO-P6）。

说明：
- 提供文本摄入、批量摄入、按文档删除、单篇文档 namespace 迁移、检索查询、数据集列表查询等管理能力；
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

from fastapi import APIRouter, BackgroundTasks, HTTPException, Path, Query
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
            "② `pdf`/`docx`/`xlsx`/`xlsm`：内联已抽取文本，或本地绝对路径/`file://...`，或（同上开关开启时）`http(s)://` 下载到临时文件再解析。"
            "URL 拉取受 `RAG_CONTENT_FETCH_ALLOW_HOSTS`、私网解析拦截等约束（防 SSRF）；`source_uri` 仍不用于下载。"
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
        description="可选，默认 text。格式/解析方式：text、markdown、html、pdf、docx、xlsx/xlsm；pdf 扫描件需 MinerU（见配置）。",
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

    **要点**：`content` 可为内联正文、本地/`file://` 路径，或在 `RAG_CONTENT_FETCH_ENABLED=true` 时的 http(s) 文件 URL；`source_uri` 仅溯源、不拉取正文。

    **各字段释义与必填以 OpenAPI Schema 为准**，以下为速查。

    **路径/Query**：无。

    **`documents[]` 每篇文档（模型 `IngestionJobDocumentRequest`）**
    - `content`：**必填**。内联正文、本地/`file://` 路径；若开启 `RAG_CONTENT_FETCH_ENABLED`，可为 `http(s)://` 文件 URL（按类型下载为文本或临时文件）。不会用 `source_uri` 下载正文。
    - `dataset_id`：必填，数据集划分与过滤。[可作为知识库一级分区]
    - `doc_name`：必填，文档逻辑名（更新主键之一）。
    - `doc_version`：可选默认 v1，版本治理与按版本删除。
    - `tenant_id`：可选，多租户 ID，写入元数据供隔离/过滤。
    - `namespace`：可选，逻辑分区，与检索 namespace 一致。[可作为知识库二级分区]
    - `source_type`：可选默认 text，决定如何解析 `content`。
    - `source_uri`：可选，**仅元数据**（链接/URI 字符串），溯源展示；**不用于抓取正文**。
    - `description`：可选，人读摘要。
    - `replace_if_exists`：可选默认 true，同名先删后灌。
    - `metadata`：可选，自定义扩展字段写入索引。[可作为知识库三级级及以下分区]

    **任务级（模型 `IngestionJobRequest` 根字段）**
    - `operator`：可选，操作者标识，仅审计。
    - `idempotency_key`：可选；**仅传入时**若已有同键且任务仍为 PENDING/RUNNING 则返回原 `job_id`，否则每次新建任务。
    - `chunk_size` / `chunk_overlap` / `min_chunk_size`：可选，默认 500 / 80 / 40，作用于本任务全部文档。

    **响应体 `IngestionJobSubmitResponse`（200）**
    - `ok`、`job_id`（新任务或幂等命中时的已有任务）。

    失败时 HTTP 5xx，`detail` 为错误信息。
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
    - `ok`、`job_id`、`documents`（`JobDocumentItem` 列表：dataset_id、doc_name、doc_version 等）。
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
    """同步写入单文档。`content` 含义与 `POST /rag/jobs/ingest` 中单篇文档相同（内联正文或 pdf/docx/xlsx 路径）。"""

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
        description="可选，默认 text。解析方式：text、markdown、html、pdf、docx、xlsx/xlsm。",
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
    - `chunk_size`、`chunk_overlap`、`min_chunk_size`：可选切块参数。
    - 扫描 PDF：需 `MINERU_ENABLED` 与 mineru-api，与异步任务一致。

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
        tmp_fetched = None
        try:
            doc, tmp_fetched = materialize_document_content_from_url(doc)
            doc, _ = prepare_pdf_document_for_pipeline(doc)
            chunks, stats = pipeline.process_document(doc)
        finally:
            if tmp_fetched is not None:
                tmp_fetched.unlink(missing_ok=True)
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

    **路径/Query**：无。

    **请求体 `DeleteDocumentRequest`**
    - 必填：`doc_name`。
    - 可选：`namespace`、`doc_version`（传入则仅删匹配版本）。

    **响应体 `DeleteDocumentResponse`（200）**
    - `ok`、`deleted`（向量 chunk 删除条数，无匹配时可为 0）。
    - `doc_records_deleted`（docs 索引中的文档元数据删除条数；管理面 overview 依赖此项）。

    失败时 HTTP 5xx，`detail` 为错误信息。
    """
    try:
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
    - 可选：`top_k`、`namespace`、`scene`（默认 llm_inference）。

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
    metadata: dict[str, Any] = Field(default_factory=dict, description="扩展元数据")
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
    if req.repair_graph_async and get_app_config().rag.graph.enabled:
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
    分页查询文档元数据（管理面清单；数据来自文档索引，非 chunk 正文）。

    **Query**
    - `limit`：可选，默认 20，每页条数（1～500）。
    - `offset`：可选，默认 0。
    - `namespace`、`tenant_id`、`dataset_id`、`doc_name`：可选过滤条件。

    **响应体 `DocumentMetaListResponse`（200）**
    - `ok`、`limit`、`offset`、`namespace`（请求使用的过滤）、`documents`（`DocumentMetaItem` 列表）。

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
    doc_name: Annotated[str | None, Query(description="过滤文档名")] = None,
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

    失败时 HTTP 5xx，`detail` 为错误信息。
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

