from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class FaceGalleryCreateRequest(BaseModel):
    gallery_id: str = Field(..., description="人脸库唯一 ID")
    name: str | None = Field(None, description="展示名称")
    model_pack: str = Field("buffalo_l", description="InsightFace 模型包，录入与识别须一致")


class FaceGallerySummary(BaseModel):
    gallery_id: str
    name: str
    model_pack: str
    person_count: int
    sample_count: int
    created_at: str
    updated_at: str


class FacePersonSummary(BaseModel):
    person_id: str
    name: str
    metadata: dict[str, Any] = Field(default_factory=dict)
    sample_count: int = 0


class FaceEnrollResponse(BaseModel):
    gallery_id: str
    person_id: str
    name: str
    sample_id: str
    source_image: str | None = None


class FaceMatchItem(BaseModel):
    bbox_xyxy: list[int] | None = None
    person_id: str | None = None
    person_name: str | None = None
    match_type: str
    similarity: float | None = None
    det_score: float | None = None
    label: str | None = None
    alert: bool | None = None


class FaceIdentifyResponse(BaseModel):
    gallery_id: str
    face_count: int
    face_count_in_roi: int | None = None
    matches: list[FaceMatchItem]
    alert_types: list[str] = Field(default_factory=list)
    face_alerts: list[FaceMatchItem] = Field(default_factory=list)
    annotated_image_path: str | None = None


class FaceVerifyResponse(BaseModel):
    verified: bool
    similarity: float
    threshold: float


class FaceBatchEnrollItem(BaseModel):
    person_id: str
    name: str | None = None
    image_path: str = Field(..., description="服务器可访问的本地图片路径")


class FaceBatchEnrollRequest(BaseModel):
    items: list[FaceBatchEnrollItem]
