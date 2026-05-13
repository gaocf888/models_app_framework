from __future__ import annotations

"""
检修报告结构化提取 V0（LangGraph + 版面 OCR 侧车）。

路由前缀 `/inspection-extract-v0`，**始终挂载**；未开启时依赖返回 **503**（避免误以为是路径错误导致 404）。
业务与异步队列仅在 `INSPECT_EXTRACT_V0_ENABLED=true` 时于启动阶段初始化。
"""

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile

from app.core.config import get_app_config
from app.models.inspection_extract_v0 import (
    InspectionExtractV0AsyncSubmitResponse,
    InspectionExtractV0CancelResponse,
    InspectionExtractV0ChunkListResponse,
    InspectionExtractV0ChunkRecordsResponse,
    InspectionExtractV0JobStatusResponse,
    InspectionExtractV0Request,
    InspectionExtractV0Response,
    InspectionExtractV0UploadResponse,
)
from app.services.inspection_extract_v0_service import InspectionExtractV0Service


def require_inspection_extract_v0_enabled() -> None:
    """未开启 V0 时拒绝业务请求（503），与「未挂载路由 → 404」区分，便于联调。"""
    if not get_app_config().inspection_extract_v0.enabled:
        raise HTTPException(
            status_code=503,
            detail="INSPECT_EXTRACT_V0_ENABLED is not true; set it to true and restart to use /inspection-extract-v0/*.",
        )


router = APIRouter(dependencies=[Depends(require_inspection_extract_v0_enabled)])
service = InspectionExtractV0Service()


@router.post("/upload", response_model=InspectionExtractV0UploadResponse, summary="上传检修报告到 MinIO（与现网字段一致）")
async def upload_inspection_report_v0(file: UploadFile = File(...)) -> InspectionExtractV0UploadResponse:
    data = await file.read()
    if not data:
        raise HTTPException(status_code=400, detail="empty file upload")
    return await service.upload_file(file_name=file.filename or "inspection_report.bin", content=data, content_type=file.content_type)


@router.post(
    "/run",
    response_model=InspectionExtractV0Response,
    response_model_exclude={"records": {"__all__": {"evidence", "warnings"}}},
    summary="检修 V0 同步结构化提取",
)
async def run_inspection_extract_v0(req: InspectionExtractV0Request) -> InspectionExtractV0Response:
    return await service.extract_from_document(req)


@router.post("/run/async", response_model=InspectionExtractV0AsyncSubmitResponse, summary="检修 V0 异步任务提交")
async def run_inspection_extract_v0_async(req: InspectionExtractV0Request) -> InspectionExtractV0AsyncSubmitResponse:
    return service.submit_async_job(req)


@router.delete("/jobs/{job_id}", response_model=InspectionExtractV0CancelResponse, summary="取消 V0 异步任务")
async def delete_inspection_extract_v0_job(job_id: str) -> InspectionExtractV0CancelResponse:
    return service.cancel_async_job(job_id)


@router.get(
    "/jobs/{job_id}",
    response_model=InspectionExtractV0JobStatusResponse,
    response_model_exclude={"result": {"records": {"__all__": {"evidence", "warnings"}}}},
    summary="查询 V0 异步任务状态",
)
async def get_inspection_extract_v0_job(
    job_id: str,
    include_result: bool = Query(False, description="为 true 时在 completed 状态附带 result"),
) -> InspectionExtractV0JobStatusResponse:
    data = service.get_job_status(job_id)
    if data is None:
        raise HTTPException(status_code=404, detail="job not found")
    if not include_result:
        return data.model_copy(update={"result": None})
    return data


@router.get("/jobs/{job_id}/chunks", response_model=InspectionExtractV0ChunkListResponse, summary="V0 单段任务分块列表")
async def list_inspection_extract_v0_job_chunks(job_id: str) -> InspectionExtractV0ChunkListResponse:
    data = service.list_job_chunks(job_id)
    if data is None:
        raise HTTPException(status_code=404, detail="job not found")
    return data


@router.get(
    "/jobs/{job_id}/chunks/{work_idx}",
    response_model=InspectionExtractV0ChunkRecordsResponse,
    summary="读取 V0 单段记录（work_idx 恒为 1）",
)
async def get_inspection_extract_v0_job_chunk(job_id: str, work_idx: int) -> InspectionExtractV0ChunkRecordsResponse:
    data = service.get_job_chunk_records(job_id, work_idx)
    if data is None:
        raise HTTPException(status_code=404, detail="chunk not found")
    return data
