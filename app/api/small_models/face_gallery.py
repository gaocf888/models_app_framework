from __future__ import annotations

import json

from fastapi import APIRouter, File, Form, HTTPException, UploadFile

from app.models.face_gallery import (
    FaceBatchEnrollRequest,
    FaceEnrollResponse,
    FaceGalleryCreateRequest,
    FaceGallerySummary,
    FaceIdentifyResponse,
    FacePersonSummary,
    FaceVerifyResponse,
)
from app.services.small_models.face_gallery_service import FaceGalleryService

router = APIRouter()
service = FaceGalleryService()


@router.post("/gallery", summary="创建人脸库")
async def create_gallery(body: FaceGalleryCreateRequest) -> dict:
    try:
        return service.create_gallery(body.gallery_id, name=body.name, model_pack=body.model_pack)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.get("/galleries", response_model=list[FaceGallerySummary], summary="列出人脸库")
async def list_galleries() -> list[FaceGallerySummary]:
    return [FaceGallerySummary(**g) for g in service.list_galleries()]


@router.delete("/gallery/{gallery_id}", summary="删除人脸库")
async def delete_gallery(gallery_id: str) -> dict:
    if not service.delete_gallery(gallery_id):
        raise HTTPException(status_code=404, detail="gallery not found")
    return {"ok": True}


@router.get("/gallery/{gallery_id}/stats", summary="人脸库索引统计")
async def gallery_stats(gallery_id: str) -> dict:
    try:
        return service.gallery_stats(gallery_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/gallery/{gallery_id}/persons", response_model=list[FacePersonSummary], summary="列出库内人员")
async def list_persons(gallery_id: str) -> list[FacePersonSummary]:
    try:
        return [FacePersonSummary(**p) for p in service.list_persons(gallery_id)]
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.delete("/gallery/{gallery_id}/person/{person_id}", summary="删除人员")
async def delete_person(gallery_id: str, person_id: str) -> dict:
    try:
        if not service.delete_person(gallery_id, person_id):
            raise HTTPException(status_code=404, detail="person not found")
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"ok": True}


@router.post(
    "/gallery/{gallery_id}/enroll",
    response_model=FaceEnrollResponse,
    summary="上传图片录入人脸",
)
async def enroll_face(
    gallery_id: str,
    person_id: str = Form(..., description="人员 ID"),
    name: str | None = Form(None, description="姓名"),
    file: UploadFile = File(..., description="人脸图片"),
    device: str | None = Form(None),
) -> FaceEnrollResponse:
    data = await file.read()
    try:
        result = service.enroll_image_bytes(
            gallery_id,
            person_id=person_id,
            name=name,
            image_bytes=data,
            device=device,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return FaceEnrollResponse(**result)


@router.post("/gallery/{gallery_id}/enroll/batch", summary="批量录入（服务器本地路径）")
async def batch_enroll(gallery_id: str, body: FaceBatchEnrollRequest) -> dict:
    try:
        return service.batch_enroll(gallery_id, body.items)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post(
    "/identify",
    response_model=FaceIdentifyResponse,
    summary="单张图片 1:N 识别",
)
async def identify_face(
    gallery_id: str = Form(...),
    threshold: float = Form(0.45),
    unknown_alert: bool = Form(False),
    face_alert_mode: str | None = Form(None, description="identified | unknown | both"),
    roi_json: str | None = Form(None, description="ROI JSON，同 SmallModelRoiConfig"),
    file: UploadFile = File(...),
    device: str | None = Form(None),
) -> FaceIdentifyResponse:
    data = await file.read()
    roi = None
    if roi_json:
        try:
            roi = json.loads(roi_json)
        except json.JSONDecodeError as exc:
            raise HTTPException(status_code=400, detail=f"invalid roi_json: {exc}") from exc
    try:
        return service.identify_image_bytes(
            gallery_id,
            data,
            threshold=threshold,
            unknown_alert=unknown_alert,
            face_alert_mode=face_alert_mode,
            roi=roi,
            device=device,
            save_annotated_dir="data/face_galleries/_identify_preview",
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/verify", response_model=FaceVerifyResponse, summary="1:1 人脸核验（两张图片）")
async def verify_faces(
    threshold: float = Form(0.45),
    file_a: UploadFile = File(...),
    file_b: UploadFile = File(...),
    device: str | None = Form(None),
) -> FaceVerifyResponse:
    a = await file_a.read()
    b = await file_b.read()
    try:
        return service.verify_images_bytes(a, b, threshold=threshold, device=device)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post(
    "/gallery/{gallery_id}/verify/{person_id}",
    response_model=FaceVerifyResponse,
    summary="1:1 核验（图片 vs 库内人员）",
)
async def verify_person(
    gallery_id: str,
    person_id: str,
    threshold: float = Form(0.45),
    file: UploadFile = File(...),
    device: str | None = Form(None),
) -> FaceVerifyResponse:
    data = await file.read()
    try:
        return service.verify_person_image(
            gallery_id,
            person_id,
            data,
            threshold=threshold,
            device=device,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
