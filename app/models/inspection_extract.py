from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, Field, field_validator, model_validator

from app.conversation.ids import validate_session_id, validate_user_id


class DetectionType(str, Enum):
    MEASUREMENT = "测厚"
    DEFECT = "缺陷"


class DefectType(str, Enum):
    HIGH_TEMP_CORROSION = "高温腐蚀"
    WEAR = "磨损"
    SLAGGING = "结渣"
    CREEP = "蠕变"
    PIPE_DEFORMATION = "管道变形"
    SURFACE_EROSION = "表面吹损"
    OXIDE_SCALE = "氧化皮堆积"
    MECHANICAL_DAMAGE = "机械损伤"


class ReplaceFlag(str, Enum):
    YES = "是"
    NO = "否"


class InspectionExtractRequest(BaseModel):
    user_id: str = Field(..., description="用户唯一标识")
    session_id: str = Field(..., description="会话唯一标识")
    content: str = Field(..., description="文档内容或本地文件路径")
    source_type: str = Field(..., description="文档类型：docx/pdf/markdown/text")
    doc_name: str | None = Field(default=None, description="文档名称（可选）")
    strict: bool | None = Field(default=None, description="是否严格模式；为空时走系统默认")
    return_evidence: bool = Field(default=True, description="是否返回证据片段")
    prompt_version: str | None = Field(default=None, description="可选模板版本")

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


class InspectionRecord(BaseModel):
    location: str = Field(..., alias="检测位置", description="检测位置")
    row_no: str = Field(..., alias="行号", description="行号")
    tube_no: str = Field(..., alias="管号", description="管号")
    thickness: float = Field(..., alias="壁厚", description="壁厚")
    detection_type: DetectionType = Field(..., alias="检测类型", description="检测类型")
    defect_type: DefectType | None = Field(default=None, alias="缺陷类型", description="缺陷类型")
    replaced: ReplaceFlag = Field(..., alias="是否换管", description="是否换管")
    evidence: str | None = Field(default=None, description="证据片段（可选）")
    warnings: list[str] = Field(default_factory=list, description="该条记录的告警信息")

    model_config = {"populate_by_name": True}

    @field_validator("location", "row_no", "tube_no")
    @classmethod
    def _v_non_empty_text(cls, v: str) -> str:
        text = (v or "").strip()
        if not text:
            raise ValueError("field must not be empty")
        return text

    @field_validator("thickness", mode="before")
    @classmethod
    def _v_thickness_float(cls, v: Any) -> float:
        if isinstance(v, (int, float)):
            return float(v)
        text = str(v or "").strip()
        if not text:
            raise ValueError("thickness is required")
        return float(text)

    @model_validator(mode="after")
    def _v_defect_consistency(self) -> InspectionRecord:
        if self.detection_type == DetectionType.MEASUREMENT and self.defect_type is not None:
            raise ValueError("defect_type must be empty when detection_type is 测厚")
        if self.detection_type == DetectionType.DEFECT and self.defect_type is None:
            raise ValueError("defect_type is required when detection_type is 缺陷")
        return self


class InspectionSummary(BaseModel):
    total: int = Field(0, description="总记录数")
    defect_count: int = Field(0, description="缺陷记录数")
    replace_count: int = Field(0, description="换管记录数")
    warnings: list[str] = Field(default_factory=list, description="汇总告警")


class InspectionExtractTrace(BaseModel):
    parse_route: str = Field(..., description="解析路径：docx/pdf_text/mineru/markdown/text")
    llm_model: str = Field(..., description="LLM 模型标识")
    prompt_version: str = Field(..., description="Prompt 版本")
    parse_latency_ms: int = Field(0, description="解析耗时（毫秒）")
    llm_latency_ms: int = Field(0, description="LLM 耗时（毫秒）")


class InspectionExtractResponse(BaseModel):
    ok: bool = Field(True, description="执行是否成功")
    records: list[InspectionRecord] = Field(default_factory=list, description="结构化记录")
    summary: InspectionSummary = Field(default_factory=InspectionSummary, description="统计摘要")
    trace: InspectionExtractTrace = Field(..., description="链路追踪信息")


class InspectionUploadResponse(BaseModel):
    ok: bool = Field(True, description="上传是否成功")
    file_name: str = Field(..., description="原始文件名")
    object_name: str = Field(..., description="MinIO 对象名")
    source_type: str = Field(..., description="推断出的文档类型")
    url: str = Field(..., description="可访问 URL（预签名）")
    bucket: str = Field(..., description="MinIO bucket")

