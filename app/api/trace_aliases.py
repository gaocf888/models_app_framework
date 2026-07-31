from __future__ import annotations

"""模块别名 traces：内部转调统一 Store，不改业务逻辑。"""

from fastapi import HTTPException

from app.models.execution_trace import ExecutionTraceDetailResponse
from app.services.execution_trace_service import ExecutionTraceService

_service = ExecutionTraceService()


def get_module_trace(request_id: str, *, expected_module: str | None = None) -> ExecutionTraceDetailResponse:
    detail = _service.get_detail(request_id)
    if detail is None:
        raise HTTPException(status_code=404, detail=f"trace not found: {request_id}")
    if expected_module and detail.trace.module != expected_module:
        # 仍返回（可能双写延迟），但若完全不同模块则 404
        if detail.trace.module not in {expected_module}:
            raise HTTPException(status_code=404, detail=f"trace not found for module={expected_module}")
    return detail
