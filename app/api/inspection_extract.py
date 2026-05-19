from __future__ import annotations

"""
检修报告结构化提取接口（同步 + 异步任务）。

设计要点：
- 同步：`POST /inspection-extract/run`，一次请求返回完整结构化结果；
- 异步：`POST /inspection-extract/run/async` 提交任务，随后轮询任务状态并按块读取 parse 结果；
- 取消：`DELETE /inspection-extract/jobs/{job_id}` 协作取消未终态任务；
- 上传：`POST /inspection-extract/upload`，将本地文件上传到 MinIO 后返回可访问 URL。

鉴权：
- 由应用统一中间件处理，需在请求头携带：
  `Authorization: Bearer <SERVICE_API_KEY>`。
"""

from fastapi import APIRouter, File, HTTPException, Query, UploadFile

from app.inspection_extract.legacy_doc_guard import LEGACY_DOC_UPLOAD_MESSAGE, LegacyWordDocNotSupportedError
from app.models.inspection_extract import (
    InspectionExtractAsyncSubmitResponse,
    InspectionExtractCancelResponse,
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


@router.post(
    "/upload",
    response_model=InspectionUploadResponse,
    summary="上传检修报告到 MinIO",
    description=(
        "上传本地检修报告文件到 MinIO，并返回预签名 URL。\n\n"
        "入参（multipart/form-data）：\n"
        "- `file`: 必填，文件本体（doc/docx/pdf/md/txt 等）。\n\n"
        "出参（200）：\n"
        "- `InspectionUploadResponse`: 包含 `url`、`object_name`、`source_type`、`bucket`。\n\n"
        "常见错误：\n"
        "- `400`: 文件为空（`empty file upload`）。\n"
        "- `400`: 上传了老式 `.doc` 文件（`LEGACY_DOC_NOT_SUPPORTED`），请另存为 `.docx` 后重试。"
    ),
    responses={
        200: {"description": "上传成功，返回 MinIO 预签名 URL。"},
        400: {"description": "文件为空、为老式 .doc，或上传请求无效。"},
    },
)
async def upload_inspection_report(file: UploadFile = File(...)) -> InspectionUploadResponse:
    data = await file.read()
    if not data:
        raise HTTPException(status_code=400, detail="empty file upload")
    try:
        return await service.upload_file(file_name=file.filename or "inspection_report.bin", content=data, content_type=file.content_type)
    except LegacyWordDocNotSupportedError as exc:
        raise HTTPException(
            status_code=400,
            detail={
                "error": {
                    "code": "LEGACY_DOC_NOT_SUPPORTED",
                    "message": LEGACY_DOC_UPLOAD_MESSAGE,
                    "file_name": exc.file_name or (file.filename or ""),
                }
            },
        ) from exc


@router.post(
    "/run",
    response_model=InspectionExtractResponse,
    response_model_exclude={"records": {"__all__": {"evidence", "warnings"}}},
    summary="检修报告结构化提取",
    description=(
        "同步执行检修报告结构化提取，返回最终 JSON。\n\n"
        "入参（JSON，`InspectionExtractRequest`）：\n"
        "- `user_id` / `session_id`: 必填，会话标识；\n"
        "- `source_type`: 必填，`docx/doc/pdf/markdown/text/html`；\n"
        "- `content`: 必填，文档内容、本地路径或可下载 URL(/upload接口返回url，或其他可可下载文件url)；\n"
        "- `strict`: 可选；\n"
        "- `return_evidence`: 可选，是否返回证据字段；\n"
        "- `prompt_version`: 可选。\n\n"
        "出参（200，`InspectionExtractResponse`）：\n"
        "- `ok`、`records`、`summary`、`trace`。\n"
        "- 本接口对外响应会排除 `records[].evidence` 与 `records[].warnings`。"
    ),
    responses={
        200: {"description": "同步提取成功，返回结构化结果。"},
        422: {"description": "请求参数校验失败。"},
        500: {"description": "服务内部错误。"},
    },
)
async def run_inspection_extract(req: InspectionExtractRequest) -> InspectionExtractResponse:
    return await service.extract_from_document(req)


@router.post(
    "/run/async",
    response_model=InspectionExtractAsyncSubmitResponse,
    summary="异步检修提取（持久化 + 按块落盘，可断点续跑）",
    description=(
        "提交异步检修提取任务，立即返回 `job_id`。\n\n"
        "入参与 `/run` 一致（`InspectionExtractRequest`）。\n\n"
        "出参（200，`InspectionExtractAsyncSubmitResponse`）：\n"
        "- `ok`: 是否提交成功；\n"
        "- `job_id`: 异步任务 ID；\n"
        "- `job_status_path`: 状态查询相对路径。\n\n"
        "后续建议调用：\n"
        "- `GET /inspection-extract/jobs/{job_id}` 查询状态；\n"
        "- `GET /inspection-extract/jobs/{job_id}/chunks` 查看分块进度；\n"
        "- `GET /inspection-extract/jobs/{job_id}/chunks/{work_idx}` 获取单块 parse 结果。"
    ),
    responses={
        200: {"description": "异步任务提交成功，返回 job_id。"},
        422: {"description": "请求参数校验失败。"},
        500: {"description": "任务提交失败。"},
    },
)
async def run_inspection_extract_async(req: InspectionExtractRequest) -> InspectionExtractAsyncSubmitResponse:
    return service.submit_async_job(req)


@router.delete(
    "/jobs/{job_id}",
    response_model=InspectionExtractCancelResponse,
    summary="取消异步检修解析任务",
    description=(
        "对已提交的 `job_id` 请求取消：\n\n"
        "- 若任务仍在 **pending**（未进入 running），将从 Redis 队列移除并立即标记 **cancelled**；\n"
        "- 若已在 **running**，写入协作取消标记，worker 在分块/classify/repair 边界停止；\n"
        "  已进入的单次 LLM HTTP 请求仍可能直至超时结束。\n\n"
        "多副本部署时建议配置 **REDIS_URL**（取消信号会写入 Redis），异步任务目录建议共享卷。\n\n"
        "终态 `completed` / `failed` / `cancelled` 再次调用返回 `already_terminal`。"
    ),
    responses={
        200: {"description": "受理结果见 `InspectionExtractCancelResponse`。"},
    },
)
async def delete_inspection_extract_job(job_id: str) -> InspectionExtractCancelResponse:
    return service.cancel_async_job(job_id)


@router.get(
    "/jobs/{job_id}",
    response_model=InspectionExtractJobStatusResponse,
    response_model_exclude={"result": {"records": {"__all__": {"evidence", "warnings"}}}},
    summary="查询异步任务状态（默认不包含终态大结果，避免轮询大包体）",
    description=(
        "查询异步任务状态与进度。\n\n"
        "路径参数：\n"
        "- `job_id`: 异步任务 ID。\n\n"
        "Query 参数：\n"
        "- `include_result`（默认 `false`）：\n"
        "  - `false`: 仅返回状态与 metrics（推荐轮询）；\n"
        "  - `true`: 在任务 `completed` 时附带 `result`。\n\n"
        "出参（200，`InspectionExtractJobStatusResponse`）：\n"
        "- `status`: `pending/running/completed/failed`；\n"
        "- `step`: 当前阶段；\n"
        "- `metrics`: 含 `chunks_total`/`chunks_done`、`chunks_parse_wall_ms`（全部含表分块 Parse 落盘用时）、`llm_latency_ms`（Parse+classify+repair）等；\n"
        "- `result`: 仅在终态且 `include_result=true` 时返回。"
    ),
    responses={
        200: {"description": "查询成功。"},
        404: {"description": "job 不存在。"},
    },
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
    description=(
        "返回异步任务中“含表分块”的执行进度列表。\n\n"
        "路径参数：\n"
        "- `job_id`: 异步任务 ID。\n\n"
        "出参（200，`InspectionExtractChunkListResponse`）：\n"
        "- `chunks[]`：每块的 `work_idx`、`status`、`record_count`。"
    ),
    responses={
        200: {"description": "查询成功，返回分块状态列表。"},
        404: {"description": "job 不存在。"},
    },
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
    description=(
        "读取指定分块（`work_idx`）的 parse 结果。\n\n"
        "路径参数：\n"
        "- `job_id`: 异步任务 ID；\n"
        "- `work_idx`: 分块序号（>=1）。\n\n"
        "出参（200，`InspectionExtractChunkRecordsResponse`）：\n"
        "- `records`: 该块解析结果（已去除 `evidence`/`warnings` 对外字段）。\n\n"
        "错误：\n"
        "- `400`: `work_idx < 1`；\n"
        "- `404`: job 不存在，或该块尚未完成落盘。"
    ),
    responses={
        200: {"description": "查询成功，返回单块 parse 记录。"},
        400: {"description": "work_idx 非法。"},
        404: {"description": "job 不存在或 chunk 尚不可用。"},
    },
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

