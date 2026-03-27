from __future__ import annotations

"""
综合分析接口。

服务配置前置条件（运维/开发）：
1) LLM 服务可用：
   - 需正确配置模型调用参数（模型名称/服务地址/鉴权）。
2) 可选 RAG：
   - 若请求启用 RAG，需完成 RAG 存储与嵌入模型配置（RAG_ES_* / EMBEDDING_MODEL_*）。
3) 可选会话上下文：
   - 若请求启用上下文，需配置会话存储（如 REDIS_URL）。
"""

from fastapi import APIRouter

from app.models.analysis import AnalysisInput, AnalysisResult
from app.services.analysis_service import AnalysisService

router = APIRouter()
service = AnalysisService()


@router.post("/run", response_model=AnalysisResult, summary="综合分析执行（基础版）")
async def run_analysis(data: AnalysisInput) -> AnalysisResult:
    """
    综合分析执行接口。

    参数说明（见 AnalysisInput）：
    - 必传：user_id、session_id、query
    - 可选：image_ids、video_clip_ids、gps_ids、sensor_data_ids、enable_rag、enable_context
    - 默认行为：enable_rag / enable_context 默认均为 true
    """
    return await service.run_analysis(data)

