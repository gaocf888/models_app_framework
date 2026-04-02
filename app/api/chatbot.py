from __future__ import annotations

"""
智能客服接口。

服务配置前置条件（运维/开发）：
1) LLM 服务可用：
   - 需正确配置模型调用参数（如模型名称、服务地址、鉴权）。
2) 可选 RAG：
   - 若请求中 enable_rag=true，需先完成 RAG 向量库与嵌入模型配置（RAG_ES_* / EMBEDDING_MODEL_*）。
3) 可选会话上下文：
   - 若请求中 enable_context=true，需配置会话存储（如 REDIS_URL）。
"""

from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse
import json

from app.models.chatbot import ChatRequest, ChatResponse
from app.services.chatbot_service import ChatbotService

router = APIRouter()
service = ChatbotService()


@router.post("/chat", response_model=ChatResponse, summary="智能客服对话（基础版）", deprecated=True, include_in_schema=False)
async def chat(req: ChatRequest) -> ChatResponse:
    """
    智能客服对话接口。

    参数说明（见 ChatRequest）：
    - 必传：user_id、session_id、query
    - 可选：image_urls、enable_rag、enable_context
    - 默认行为：enable_rag / enable_context 默认均为 true
    """
    return await service.chat(req)


@router.post("/chat/stream", summary="智能客服对话（流式 SSE）")
async def chat_stream(req: ChatRequest, request: Request):
    """
    智能客服流式对话接口（SSE）。

    - 请求体与 `/chatbot/chat` 一致（支持 image_urls）；
    - 响应为 `text/event-stream; charset=utf-8`，每行 `data: {...}` 后以空行分隔；JSON 使用 `ensure_ascii=false`，中文不转义为 `\\uXXXX`。
    """

    async def event_generator():
        try:
            async for delta in service.stream_chat(req):
                if await request.is_disconnected():
                    return
                # ensure_ascii=False：delta 含中文时不转成 \uXXXX，便于前端与 Swagger 直接阅读
                payload = json.dumps({"delta": delta, "finished": False}, ensure_ascii=False)
                yield f"data: {payload}\n\n"
            yield f"data: {json.dumps({'finished': True}, ensure_ascii=False)}\n\n"
        except Exception as exc:  # noqa: BLE001
            err = json.dumps({"error": str(exc), "finished": True}, ensure_ascii=False)
            yield f"data: {err}\n\n"

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream; charset=utf-8",
    )

