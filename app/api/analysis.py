from __future__ import annotations

from fastapi import APIRouter

from app.models.analysis import AnalysisInput, AnalysisResult
from app.services.analysis_service import AnalysisService

router = APIRouter()
service = AnalysisService()


@router.post("/run", response_model=AnalysisResult, summary="综合分析执行（基础版）")
async def run_analysis(data: AnalysisInput) -> AnalysisResult:
    """
    综合分析接口（V1，占位实现）。

    - 支持文本描述 + 多模态数据引用 ID（图像/视频/GPS/传感器等）的输入；
    - 支持 RAG 与会话上下文可配置开关；
    - 当前结果为占位文本，后续将接入 Agentic RAG + 大模型 + 工具调用。
    """
    return await service.run_analysis(data)

