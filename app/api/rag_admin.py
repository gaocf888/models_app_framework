from __future__ import annotations

"""
RAG 管理接口（对应《下一阶段工作清单》中的 TODO-P6）。

说明：
- 提供基础的文本知识摄入与数据集列表查询能力；
- 实际项目中可在此基础上扩展 Schema/业务知识/问答样例等多种类型的摄入接口。
"""

from typing import List

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.rag.ingestion import RAGDatasetMeta, RAGIngestionService
from app.core.logging import get_logger

router = APIRouter()
service = RAGIngestionService()
logger = get_logger(__name__)


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
    try:
        service.ingest_texts(
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
    try:
        total_docs = 0
        total_chunks = 0
        for doc in req.documents:
            service.ingest_texts(
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


class DeleteDocumentRequest(BaseModel):
    doc_name: str = Field(..., description="文档名称")
    namespace: str | None = Field(None, description="命名空间；为空则跨命名空间删除")


@router.post("/documents/delete", summary="按文档名删除已摄入知识")
async def delete_document(req: DeleteDocumentRequest) -> dict:
    try:
        deleted = service.delete_by_doc_name(doc_name=req.doc_name, namespace=req.namespace)
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
    try:
        snippets = service.query(
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
    metas: List[RAGDatasetMeta] = service.list_datasets()
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

