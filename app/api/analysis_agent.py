from __future__ import annotations

"""
综合分析智能体 HTTP 接口（analysis_agent）。

**推荐入口**：`/analysis-agent/*`（本路由）。现网 `/analysis/*` 仅作兼容，不再新增特性。

服务配置前置条件（运维/开发）：
1) LLM：`LLM_DEFAULT_MODEL` / `LLM_DEFAULT_ENDPOINT`
2) NL2SQL：数据库与 `NL2SQL_*` 配置
3) RAG（可选）：`enable_rag=true` 时需 ES/RAG 可用
4) LangGraph checkpoint：`ANALYSIS_AGENT_CHECKPOINT_BACKEND=memory|redis`（HITL 须非 none）
5) 多 worker HITL：`ANALYSIS_AGENT_SESSION_STORE_BACKEND=redis` 且 `REDIS_URL` 可用
6) 开关：`ANALYSIS_AGENT_ENABLED=true`
"""

from fastapi import APIRouter, HTTPException, Path
from fastapi.responses import StreamingResponse

from app.models.analysis_agent import (
    AnalysisAgentResumeRequest,
    AnalysisAgentResult,
    AnalysisAgentRunRequest,
)
from app.services.analysis_agent_service import AnalysisAgentService

router = APIRouter()
service = AnalysisAgentService()


@router.post(
    "/run-stream",
    summary="综合分析智能体流式执行",
    response_description="SSE：analysis_agent_* 事件序列",
)
async def run_analysis_agent_stream(data: AnalysisAgentRunRequest) -> StreamingResponse:
    """
    按槽串行 LangGraph 编排；关键数据缺失时可 `analysis_agent_user_input_required`。

    参数说明（见 `AnalysisAgentRunRequest`）：
    - 必传：user_id、session_id、analysis_type、query
    - 可选：options（enable_rag、strict、chart_mode、enable_human_in_the_loop、use_react_agent 等）
    """
    return await service.run_stream(data)


@router.post(
    "/resume-stream",
    summary="人机协同恢复（SSE 续流）",
    response_description="从 interrupt 断点继续推送剩余槽位事件",
)
async def resume_analysis_agent_stream(data: AnalysisAgentResumeRequest) -> StreamingResponse:
    """
    在 `run-stream` 收到 `analysis_agent_user_input_required` 后调用。

    必传：resume_token、user_id、session_id、action（retry|skip_slot|abort|widen_time_range）
    """
    return await service.resume_stream(data)


@router.post(
    "/resume",
    summary="人机协同恢复（同步结果）",
    response_model=AnalysisAgentResult,
)
async def resume_analysis_agent(data: AnalysisAgentResumeRequest) -> AnalysisAgentResult:
    """等待恢复执行完成后一次性返回 `AnalysisAgentResult`（无中间 SSE）。"""
    try:
        return await service.resume(data)
    except RuntimeError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get(
    "/trace/{request_id}",
    summary="查询 analysis_agent trace",
)
async def get_analysis_agent_trace(
    request_id: str = Path(..., description="run-stream 返回的 request_id"),
) -> dict:
    trace = service.get_trace(request_id)
    if trace is None:
        raise HTTPException(status_code=404, detail="trace not found")
    return trace
