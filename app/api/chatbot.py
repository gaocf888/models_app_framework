from __future__ import annotations

"""
智能客服 HTTP 接口模块。

部署前置条件（运维/开发）：
    1) LLM 服务可用：正确配置模型名称、服务地址与鉴权。
    2) 可选 RAG：请求中 enable_rag=true 时需完成 RAG 与嵌入配置（RAG_ES_*、EMBEDDING_MODEL_*）。
    3) 可选会话上下文：enable_context=true 时建议配置 REDIS_URL，否则多 worker 不共享历史。
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
    智能客服对话接口（基础版，已弃用）

    非流式返回完整回答；逻辑委托 ChatbotService（可选 LangChain 链路或内置 RAG+LLM）。

    Args:
        req (ChatRequest): JSON 请求体（application/json），结构如下：
            user_id (str): 必填。用户唯一标识。
            session_id (str): 必填。会话唯一标识，由前端或调用方管理。
            query (str): 必填。用户本轮输入文本。
            image_urls (list[str]): 可选，默认 []。本轮关联图片 URL；空串、仅空白、null 元素会被丢弃，全空则按纯文本处理。
            enable_rag (bool): 可选，默认 true。为 true 时走 HybridRAGService 检索；false 不调向量库。
            enable_context (bool): 可选，默认 true。为 true 时拼接历史对话；false 则每轮独立。多实例建议配 REDIS_URL。

    Returns:
        ChatResponse: JSON 响应体（application/json），结构如下：
            answer (str): 助手完整回复正文。
            used_rag (bool): 本轮与 RAG 相关的标记（无检索命中时仍可能为 false）。
            context_snippets (list[str]): 注入提示词前的检索片段列表，与 /rag/query 的 snippets 同源语义。

    Raises:
        无：请求体验证失败由 FastAPI/Pydantic 以 HTTP 422 返回；业务降级时由服务层返回占位文案而非本函数显式抛异常。
    """
    return await service.chat(req)


@router.post("/chat/stream", summary="智能客服对话（流式 SSE）")
async def chat_stream(req: ChatRequest, request: Request):
    """
    智能客服流式对话接口（SSE）

    使用 Server-Sent Events 逐段推送模型生成内容；客户端断开时服务端停止继续生成。

    Args:
        req (ChatRequest): JSON 请求体（application/json），字段如下：
            user_id (str): 必填。用户唯一标识。
            session_id (str): 必填。会话唯一标识，由前端或调用方管理。
            query (str): 必填。用户本轮输入文本。
            image_urls (list[str]): 可选，默认 []。本轮关联图片 URL；空串、仅空白、null 元素会被丢弃，全空则按纯文本处理。
            enable_rag (bool): 可选，默认 true。为 true 时走 HybridRAGService 检索；false 不调向量库。
            enable_context (bool): 可选，默认 true。为 true 时拼接历史对话；false 则每轮独立。多实例建议配 REDIS_URL。
        request (Request): 框架注入的当前请求对象；用于 await request.is_disconnected() 判断客户端是否已断开。

    Returns:
        StreamingResponse: 流式 HTTP 响应，整体结构如下：
            HTTP 层:
                Content-Type: text/event-stream; charset=utf-8
                Body: 按 SSE 规范连续输出的文本流（非单一 JSON 对象）。
            SSE 帧格式（每一则事件）:
                单行前缀: 固定为 "data: "
                载荷: 一个 JSON 对象（序列化时使用 ensure_ascii=false，中文不转义为 \\uXXXX）
                事件结束: 载荷行后紧跟一个空行（即 "\\n\\n"），表示本事件结束。
            载荷 JSON 对象按阶段分为三类（字段层级一致列出）:
                1) 生成中（可出现多次）:
                    delta (str): 本段新增文本（增量片段）。
                    finished (bool): 固定为 false。
                2) 正常结束（最后一则成功事件，仅一次）:
                    finished (bool): 固定为 true。
                    （不含 delta；不含 error。）
                3) 异常结束（生成过程中抛错时，至多一次）:
                    error (str): 异常信息字符串。
                    finished (bool): 固定为 true。
                    （通常不含 delta；与正常结束互斥。）

    Raises:
        无：请求体字段校验失败由 FastAPI/Pydantic 返回 HTTP 422；生成期异常不保证向上抛出，而以第 3 类 SSE 事件的 error + finished 下发。
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
