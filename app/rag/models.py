from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional


def utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class IngestionJobStatus(str, Enum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    SUCCESS = "SUCCESS"
    FAILED = "FAILED"
    PARTIAL = "PARTIAL"


class IngestionJobType(str, Enum):
    """任务类型（设计稿 §3 IngestionJob.job_type）。"""

    FULL = "full"
    UPSERT = "upsert"
    DELETE = "delete"
    REINDEX = "reindex"


@dataclass
class RetrievedChunk:
    """
    标准检索分片结果（设计稿 §3 Chunk / §E 检索输出）。
    用于上层 trace、NL2SQL 与统一推理链路。
    """

    text: str
    doc_name: str | None = None
    namespace: str | None = None
    chunk_id: str | None = None
    score: float | None = None
    section_path: str | None = None
    doc_version: str | None = None
    pipeline_version: str | None = None
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class DocumentSource:
    dataset_id: str
    doc_name: str
    namespace: Optional[str]
    content: str
    doc_version: str = "v1"
    tenant_id: Optional[str] = None
    source_type: str = "text"  # text/markdown/html/pdf/docx/xlsx
    source_uri: Optional[str] = None
    description: Optional[str] = None
    replace_if_exists: bool = True
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class ChunkRecord:
    chunk_id: str
    chunk_index: int
    text: str
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class IngestionJob:
    job_id: str
    status: IngestionJobStatus
    step: str
    documents: List[DocumentSource]
    job_type: IngestionJobType = IngestionJobType.UPSERT
    idempotency_key: Optional[str] = None
    created_at: str = field(default_factory=utcnow_iso)
    updated_at: str = field(default_factory=utcnow_iso)
    finished_at: Optional[str] = None
    error_code: Optional[str] = None
    error_message: Optional[str] = None
    metrics: Dict[str, Any] = field(default_factory=dict)
    operator: Optional[str] = None

