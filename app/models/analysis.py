from __future__ import annotations

from typing import List, Optional

from pydantic import BaseModel, Field


class AnalysisInput(BaseModel):
    """
    综合分析请求的输入数据。

    目前以占位形式支持文本描述和多模态数据的引用 ID，后续可扩展为实际上传/存储方案。
    """

    user_id: str = Field(..., description="用户唯一标识")
    session_id: str = Field(..., description="会话唯一标识")
    query: str = Field(..., description="分析需求的自然语言描述")
    image_ids: List[str] = Field(default_factory=list, description="相关图像数据的标识符")
    video_clip_ids: List[str] = Field(default_factory=list, description="相关视频片段的标识符")
    gps_ids: List[str] = Field(default_factory=list, description="相关 GPS 数据的标识符")
    sensor_data_ids: List[str] = Field(default_factory=list, description="相关传感器数据的标识符")
    enable_rag: bool = Field(True, description="是否启用 RAG 检索")
    enable_context: bool = Field(True, description="是否启用会话上下文")


class AnalysisResult(BaseModel):
    """
    综合分析结果。
    """

    summary: str = Field(..., description="综合分析报告或结论的摘要")
    details: Optional[str] = Field(None, description="更详细的分析说明（可选）")
    used_rag: bool = Field(..., description="是否实际使用 RAG")
    context_snippets: List[str] = Field(default_factory=list, description="检索到的文本上下文片段摘要")

