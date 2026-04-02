from __future__ import annotations

from pydantic import BaseModel, Field


class ChatMessage(BaseModel):
    role: str = Field(..., description="消息角色，例如 user/assistant/system")
    content: str = Field(..., description="消息内容")


class ChatRequest(BaseModel):
    user_id: str = Field(..., description="用户唯一标识")
    session_id: str = Field(..., description="会话唯一标识（由前端或调用方管理）")
    query: str = Field(..., description="用户本轮输入内容")
    image_urls: list[str] = Field(default_factory=list, description="本轮关联的图片 URL 列表（可选）")
    enable_rag: bool = Field(
        True,
        description=(
            "是否启用 RAG：为 true 时由 HybridRAGService 检索知识库（默认语义+关键词+RRF+重排，"
            "场景参数见 RAG_SCENE_CHATBOT_*）；为 false 则不调向量库"
        ),
    )
    enable_context: bool = Field(True, description="是否启用会话上下文（Redis 历史）")


class ChatResponse(BaseModel):
    answer: str = Field(..., description="助手回答全文")
    used_rag: bool = Field(..., description="本轮是否实际走了检索（无命中时也可能为 false）")
    context_snippets: list[str] = Field(
        default_factory=list,
        description="注入到提示词前的检索片段文本列表（与 /rag/query 的 snippets 同源业务数据，封装形态不同）",
    )

