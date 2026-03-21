from __future__ import annotations

from fastapi import APIRouter

from app.models.chatbot import ChatRequest, ChatResponse
from app.services.chatbot_service import ChatbotService

router = APIRouter()
service = ChatbotService()


@router.post("/chat", response_model=ChatResponse, summary="智能客服对话（基础版）")
async def chat(req: ChatRequest) -> ChatResponse:
    """
    智能客服基础对话接口（V1，占位实现）。

    支持：
    - RAG 是否启用（enable_rag）
    - 会话上下文是否启用（enable_context）

    后续将：
    - 使用 LangChain/LangGraph + LLMClient 生成真实回答；
    - 支持流式响应（SSE/WebSocket）。
    """
    return await service.chat(req)

