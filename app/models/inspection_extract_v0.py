"""
检修报告结构化提取 V0：请求/响应与异步任务模型。

与现网 `inspection_extract` 字段对齐，并扩展 Trace/Metrics 以承载版面+OCR 与 LangGraph 阶段信息。
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.conversation.ids import validate_session_id, validate_user_id
from app.models.inspection_extract import InspectionRecord, InspectionSummary, InspectionUploadResponse


class InspectionExtractV0Request(BaseModel):
    """POST /inspection-extract-v0/run 与 /run/async 的 JSON 体（与现网语义对齐）。"""

    user_id: str = Field(..., description="用户唯一标识")
    session_id: str = Field(..., description="会话唯一标识")
    content: str = Field(..., description="文档内容、本地文件路径或可下载 URL")
    source_type: str = Field(..., description="docx/doc/pdf/markdown/md/text/txt/html")
    doc_name: str | None = Field(default=None, description="文档名称（可选）")
    strict: bool | None = Field(default=None, description="是否严格模式；为空走 INSPECT_EXTRACT_V0_STRICT_DEFAULT")
    return_evidence: bool = Field(default=True, description="是否返回证据片段")
    prompt_version: str | None = Field(default=None, description="inspection_extract_v0_extract 模板版本，默认见配置")

    @field_validator("user_id")
    @classmethod
    def _v_uid(cls, v: str) -> str:
        return validate_user_id(v)

    @field_validator("session_id")
    @classmethod
    def _v_sid(cls, v: str) -> str:
        return validate_session_id(v)

    @field_validator("source_type")
    @classmethod
    def _v_source_type(cls, v: str) -> str:
        value = (v or "").strip().lower()
        if value not in {"docx", "doc", "pdf", "markdown", "md", "text", "txt", "html"}:
            raise ValueError("source_type must be one of: docx/doc/pdf/markdown/md/text/txt/html")
        return value


class InspectionExtractV0StageLatencyMs(BaseModel):
    """各阶段墙钟毫秒（可选，终态尽量填满）。"""

    preprocess: int | None = Field(default=None, description="预处理（渲染/清洗）")
    layout_ocr: int | None = Field(default=None, description="调用 paddleocr-layout-api")
    build_irt: int | None = Field(default=None, description="IRT 规范化与落盘")
    llm: int | None = Field(default=None, description="单阶段大模型")
    postprocess: int | None = Field(default=None, description="校验与业务归一")


class InspectionExtractV0Trace(BaseModel):
    """与现网 Trace 对齐并扩展 V0 诊断字段。"""

    parse_route: str = Field(..., description="irt_native_docx | irt_pdf_ocr | irt_text_fallback 等")
    llm_model: str = Field(..., description="LLM 模型标识")
    prompt_version: str = Field(..., description="如 inspection_extract_v0:v1")
    parse_latency_ms: int = Field(0, description="解析侧（含 ingest/preprocess）总耗时")
    llm_latency_ms: int = Field(0, description="单阶段 LLM 耗时")
    ocr_engine: str | None = Field(default=None, description="版面服务返回的 ocr_engine")
    layout_engine: str | None = Field(default=None, description="版面服务返回的 layout_engine")
    layout_api_version: str | None = Field(default=None, description="版面服务 engine_version")
    stage_latency_ms: InspectionExtractV0StageLatencyMs | None = Field(default=None, description="分阶段耗时")
    low_confidence: bool | None = Field(default=None, description="是否存在低置信 OCR/表格单元")
    review_flags: list[str] = Field(default_factory=list, description="需人工复核标记，如 ocr_conf_lt_threshold")


class InspectionExtractV0Response(BaseModel):
    ok: bool = Field(True, description="执行是否成功")
    records: list[InspectionRecord] = Field(default_factory=list, description="结构化记录")
    summary: InspectionSummary = Field(default_factory=InspectionSummary, description="统计摘要")
    trace: InspectionExtractV0Trace = Field(..., description="链路追踪")


class InspectionExtractV0AsyncSubmitResponse(BaseModel):
    ok: bool = True
    job_id: str = Field(..., description="异步任务 ID")
    job_status_path: str = Field(..., description="GET 状态路径，如 /inspection-extract-v0/jobs/{job_id}")


class InspectionExtractV0CancelResponse(BaseModel):
    ok: bool = Field(...)
    job_id: str = Field(...)
    outcome: str = Field(..., description="cancel_accepted | already_terminal | not_found")
    message: str = Field(default="", description="说明")


class InspectionExtractV0JobMetrics(BaseModel):
    model_config = ConfigDict(extra="ignore")

    pipeline: str | None = Field(default="v0", description="固定 v0")
    parse_route: str | None = None
    llm_model: str | None = None
    prompt_version: str | None = None
    parse_latency_ms: int | None = None
    llm_latency_ms: int | None = None
    irt_build_ms: int | None = None
    ocr_engine: str | None = None
    layout_engine: str | None = None
    layout_api_version: str | None = None
    langgraph_thread_id: str | None = None
    chunks_total: int | None = Field(default=1, description="LLM 分块数：无表为 1，多表为表数量")
    chunks_done: int | None = Field(default=0, description="完成时与 chunks_total 一致")


class InspectionExtractV0JobStatusResponse(BaseModel):
    job_id: str
    status: str = Field(..., description="pending | running | cancelling | cancelled | completed | failed")
    step: str = Field(..., description="v0: queued | running_graph | post_process | done 等")
    created_at: str
    updated_at: str
    finished_at: str | None = None
    error_code: str | None = None
    error_message: str | None = None
    metrics: InspectionExtractV0JobMetrics = Field(default_factory=InspectionExtractV0JobMetrics)
    result: InspectionExtractV0Response | None = None


class InspectionExtractV0ChunkListItem(BaseModel):
    work_idx: int
    status: str = Field(..., description="done | pending（与现网检修异步分块语义一致）")
    record_count: int = 0


class InspectionExtractV0ChunkListResponse(BaseModel):
    job_id: str
    chunks: list[InspectionExtractV0ChunkListItem] = Field(default_factory=list)


class InspectionExtractV0ChunkRecordsResponse(BaseModel):
    job_id: str
    work_idx: int
    records: list[dict[str, Any]] = Field(default_factory=list, description="该块记录（dict）")


# 上传与现网共用响应模型（MinIO 字段一致）
InspectionExtractV0UploadResponse = InspectionUploadResponse
