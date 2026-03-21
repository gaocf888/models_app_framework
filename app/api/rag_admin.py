from __future__ import annotations

"""
RAG 管理接口（对应《下一阶段工作清单》中的 TODO-P6）。

说明：
- 提供基础的文本知识摄入与数据集列表查询能力；
- 实际项目中可在此基础上扩展 Schema/业务知识/问答样例等多种类型的摄入接口。
"""

from typing import List

from fastapi import APIRouter
from pydantic import BaseModel, Field

from app.rag.ingestion import RAGDatasetMeta, RAGIngestionService

router = APIRouter()
service = RAGIngestionService()


class IngestTextsRequest(BaseModel):
    dataset_id: str = Field(..., description="数据集标识")
    description: str | None = Field(None, description="数据集描述")
    texts: List[str] = Field(..., description="要摄入的文本列表")
    namespace: str | None = Field(
        None,
        description="可选命名空间，例如 nl2sql_schema/nl2sql_biz_knowledge/nl2sql_qa_examples",
    )


@router.post("/ingest/texts", summary="摄入文本到 RAG 知识库")
async def ingest_texts(req: IngestTextsRequest) -> dict:
    service.ingest_texts(req.dataset_id, req.texts, description=req.description, namespace=req.namespace)
    return {"ok": True}


class DatasetMetaResponse(BaseModel):
    dataset_id: str
    description: str | None = None
    num_items: int
    namespace: str | None = None


@router.get("/datasets", response_model=List[DatasetMetaResponse], summary="列出已登记的 RAG 数据集")
async def list_datasets() -> List[DatasetMetaResponse]:
    metas: List[RAGDatasetMeta] = service.list_datasets()
    return [
        DatasetMetaResponse(
            dataset_id=m.dataset_id,
            description=m.description,
            num_items=m.num_items,
            namespace=m.namespace,
        )
        for m in metas
    ]

