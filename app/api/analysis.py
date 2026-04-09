from __future__ import annotations

"""
综合分析 HTTP 接口（`/analysis/run`）。

职责：
    - 接收自然语言分析需求及可选多模态引用 ID，经 `AnalysisService` 编排：
      优先 LangChain `AnalysisChain`（若依赖可用），否则 RAG + 会话历史 + 统一 LLM 客户端回退。

鉴权与身份：
    - 请求头须携带 `Authorization: Bearer <SERVICE_API_KEY>`（密钥生成与配置见 `app/auth/keygen.py` 与
      `app/app-deploy/README-simple-deploy.md`「Service API Key」）；
    - `user_id`、`session_id` 由调用方在请求体传入，用于会话记录与 Prompt 分流。
"""

from fastapi import APIRouter

from app.models.analysis import AnalysisInput, AnalysisResult
from app.services.analysis_service import AnalysisService

router = APIRouter()
service = AnalysisService()


@router.post("/run", response_model=AnalysisResult, summary="综合分析执行（基础版）")
async def run_analysis(data: AnalysisInput) -> AnalysisResult:
    """
    触发一次综合分析任务并返回结构化结果。

    Args:
        data (AnalysisInput): 必填 `user_id`、`session_id`、`query`；
            可选 `image_ids`、`video_clip_ids`、`gps_ids`、`sensor_data_ids`（占位引用）；
            `enable_rag`、`enable_context` 控制检索与历史拼接。

    Returns:
        AnalysisResult: `summary` 为主结论，`details` 可选展开，`used_rag` 与 `context_snippets` 反映检索情况。

    Raises:
        HTTPException: 路由层不直接抛出；校验失败 422。
        ValueError: 服务层在 `user_id` 为空时可能抛出。
    """
    return await service.run_analysis(data)
