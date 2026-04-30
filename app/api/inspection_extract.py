from __future__ import annotations

from fastapi import APIRouter, File, HTTPException, Query, UploadFile

from app.models.inspection_extract import (
    InspectionExtractAsyncSubmitResponse,
    InspectionExtractChunkListResponse,
    InspectionExtractChunkRecordsResponse,
    InspectionExtractJobStatusResponse,
    InspectionExtractRequest,
    InspectionExtractResponse,
    InspectionUploadResponse,
)
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


@router.post(
    "/run/async",
    response_model=InspectionExtractAsyncSubmitResponse,
    summary="异步检修提取（持久化 + 按块落盘，可断点续跑）",
)
async def run_inspection_extract_async(req: InspectionExtractRequest) -> InspectionExtractAsyncSubmitResponse:
    return service.submit_async_job(req)


@router.get(
    "/jobs/{job_id}",
    response_model=InspectionExtractJobStatusResponse,
    response_model_exclude={"result": {"records": {"__all__": {"evidence", "warnings"}}}},
    summary="查询异步任务状态（默认不包含终态大结果，避免轮询大包体）",
)
async def get_inspection_extract_job(
    job_id: str,
    include_result: bool = Query(False, description="为 true 时在 completed 状态附带与 /run 一致的 result"),
) -> InspectionExtractJobStatusResponse:
    data = service.get_job_status(job_id)
    if data is None:
        raise HTTPException(status_code=404, detail="job not found")
    if not include_result:
        return data.model_copy(update={"result": None})
    return data


@router.get(
    "/jobs/{job_id}/chunks",
    response_model=InspectionExtractChunkListResponse,
    summary="列出各含表分块 parse 完成情况",
)
async def list_inspection_extract_job_chunks(job_id: str) -> InspectionExtractChunkListResponse:
    data = service.list_job_chunks(job_id)
    if data is None:
        raise HTTPException(status_code=404, detail="job not found")
    return data


@router.get(
    "/jobs/{job_id}/chunks/{work_idx}",
    response_model=InspectionExtractChunkRecordsResponse,
    summary="获取单块 parse 记录（仅该块落盘后可读）",
)
async def get_inspection_extract_job_chunk(job_id: str, work_idx: int) -> InspectionExtractChunkRecordsResponse:
    if work_idx < 1:
        raise HTTPException(status_code=400, detail="work_idx must be >= 1")
    data = service.get_job_chunk_records(job_id, work_idx)
    if data is None:
        st = service.get_job_status(job_id)
        if st is None:
            raise HTTPException(status_code=404, detail="job not found")
        raise HTTPException(status_code=404, detail="chunk not available yet")
    return data

