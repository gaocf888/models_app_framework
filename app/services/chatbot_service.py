from __future__ import annotations

import asyncio
from typing import Any, AsyncIterator, Dict

from app.conversation.manager import ConversationManager
from app.conversation.message_id import build_conversation_message_id
from app.core.config import get_app_config
from app.core.logging import get_logger
from app.llm.client import VLLMHttpClient
from app.llm.graphs import ChatbotLangGraphRunner
from app.llm.prompt_registry import PromptTemplateRegistry
from app.models.chatbot import ChatRequest
from app.rag.rag_service import RAGService
from app.rag.service_registry import get_rag_service
from app.services.chatbot_image_preprocessor import ChatbotImagePreprocessor
from app.services.chatbot_outline import ChatbotOutlineStore
from app.services.chatbot_stream_control import ChatbotStreamControl

logger = get_logger(__name__)


class ChatbotService:
    """
    智能客服主业务服务（Stream-only + LangGraph-only）。

    - 唯一对话入口：``stream_chat_events`` → ``ChatbotLangGraphRunner``
    - 会话：ConversationManager；可选 RAG / 多模态 / Outline「第 N 点」旁路
    """

    def __init__(
        self,
        rag_service: RAGService | None = None,
        conv_manager: ConversationManager | None = None,
        llm_client: VLLMHttpClient | None = None,
        prompt_registry: PromptTemplateRegistry | None = None,
    ) -> None:
        self._rag = rag_service or get_rag_service()
        self._conv = conv_manager or ConversationManager()
        self._llm = llm_client or VLLMHttpClient()
        self._prompts = prompt_registry or PromptTemplateRegistry()
        self._chatbot_cfg = get_app_config().chatbot
        self._image_preprocessor = ChatbotImagePreprocessor(self._chatbot_cfg)
        self._outline_store = ChatbotOutlineStore(self._chatbot_cfg)
        self._stream_ctrl = ChatbotStreamControl()
        self._graph_runner = ChatbotLangGraphRunner(
            rag_service=self._rag,
            conv_manager=self._conv,
            llm_client=self._llm,
            prompt_registry=self._prompts,
            outline_store=self._outline_store,
        )

    async def stream_chat(self, req: ChatRequest) -> AsyncIterator[str]:
        if not req.user_id:
            raise ValueError("user_id is required (must be provided by the caller).")
        async for ev in self.stream_chat_events(req):
            if ev.get("type") == "delta":
                yield str(ev.get("delta") or "")

    async def stream_chat_events(self, req: ChatRequest) -> AsyncIterator[Dict[str, Any]]:
        original_image_urls = self._clean_image_urls(req.image_urls)
        req = await self._preprocess_request_images(req)
        model_req = await self._apply_structured_reference(req)
        if not req.user_id:
            raise ValueError("user_id is required (must be provided by the caller).")

        stream_id = self._stream_ctrl.begin_stream(req.user_id, req.session_id)
        yield {"type": "started", "stream_id": stream_id}
        try:
            async for ev in self._graph_runner.run_stream_events(
                req,
                model_req=model_req,
                stream_id=stream_id,
                cancel_checker=self._stream_ctrl.is_cancelled,
                original_image_urls=original_image_urls,
            ):
                yield ev
        finally:
            await self._stream_ctrl.clear_stream(req.user_id, req.session_id, stream_id)

    async def _preprocess_request_images(self, req: ChatRequest) -> ChatRequest:
        imgs = [u for u in req.image_urls if isinstance(u, str) and u.strip()]
        if not imgs:
            return req
        new_urls = await self._image_preprocessor.preprocess_urls(imgs)
        return req.model_copy(update={"image_urls": new_urls})

    @staticmethod
    def _clean_image_urls(image_urls: list[str]) -> list[str]:
        return [u for u in image_urls if isinstance(u, str) and u.strip()]

    async def stop_stream(self, user_id: str, session_id: str, stream_id: str) -> None:
        await self._stream_ctrl.cancel_stream(user_id, session_id, stream_id)

    async def _apply_structured_reference(self, req: ChatRequest) -> ChatRequest:
        if not self._outline_store.enabled:
            return req
        ref = await self._outline_store.resolve_reference(
            user_id=req.user_id, session_id=req.session_id, query=req.query
        )
        if not ref:
            return req
        idx = int(ref.get("index") or 0)
        gist = str(ref.get("gist") or "").strip()
        if idx <= 0 or not gist:
            return req
        overlay = (
            "[resolved_reference]\n"
            f"上文第{idx}点：{gist}\n"
            "请优先基于该点回答，并明确与该点对应关系。\n"
        )
        return req.model_copy(update={"query": f"{overlay}\n用户问题：{req.query}"})

    def _schedule_outline_index(self, user_id: str, session_id: str) -> None:
        if not self._outline_store.enabled or not self._chatbot_cfg.outline_async_enabled:
            return
        history = self._conv.get_recent_history(user_id, session_id, limit=1)
        if not history:
            return
        last = history[-1]
        role = str(last.get("role", ""))
        content = str(last.get("content", ""))
        if role != "assistant" or not content:
            return
        message_id = build_conversation_message_id(user_id, session_id, role, content, last.get("ts"))
        try:
            asyncio.create_task(
                self._outline_store.save_outline(
                    user_id=user_id,
                    session_id=session_id,
                    assistant_message_id=message_id,
                    answer_text=content,
                )
            )
        except Exception:
            pass
