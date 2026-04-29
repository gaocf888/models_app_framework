from __future__ import annotations

from fastapi import APIRouter, File, HTTPException, UploadFile

from app.models.inspection_extract import InspectionExtractRequest, InspectionExtractResponse, InspectionUploadResponse
from app.services.inspection_extract_service import InspectionExtractService

router = APIRouter()
service = InspectionExtractService()


@router.post("/upload", response_model=InspectionUploadResponse, summary="上传检修报告到 MinIO")
async def upload_inspection_report(file: UploadFile = File(...)) -> InspectionUploadResponse:
    data = await file.read()
    if not data:
        raise HTTPException(status_code=400, detail="empty file upload")
    return await service.upload_file(file_name=file.filename or "inspection_report.bin", content=data, content_type=file.content_type)


@router.post(
    "/run",
    response_model=InspectionExtractResponse,
    response_model_exclude={"records": {"__all__": {"evidence", "warnings"}}},
    summary="检修报告结构化提取",
)
async def run_inspection_extract(req: InspectionExtractRequest) -> InspectionExtractResponse:
    return await service.extract_from_document(req)

