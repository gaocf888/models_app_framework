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
    enable_rag: bool = Field(True, description="是否启用 RAG 检索")
    enable_context: bool = Field(True, description="是否启用会话上下文")


class ChatResponse(BaseModel):
    answer: str = Field(..., description="助手回答")
    used_rag: bool = Field(..., description="是否实际使用了 RAG")
    context_snippets: list[str] = Field(default_factory=list, description="检索到的上下文片段摘要")

