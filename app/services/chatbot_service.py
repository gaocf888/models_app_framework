from __future__ import annotations

from app.conversation.manager import ConversationManager
from app.core.logging import get_logger
from app.models.chatbot import ChatRequest, ChatResponse
from app.llm.client import VLLMHttpClient
from app.llm.prompt_registry import PromptTemplateRegistry
from app.rag.hybrid_rag_service import HybridRAGService
from app.rag.rag_service import RAGService
from typing import AsyncIterator, Dict, Any

logger = get_logger(__name__)


class ChatbotService:
    """
    智能客服主业务服务。

    当前实现（可用于生产）：
    - 默认通过 ConversationManager 管理会话历史（支持内存/Redis）；
    - 可选通过 HybridRAGService 使用向量 RAG / GraphRAG 进行知识检索；
    - 使用统一的大模型客户端 VLLMHttpClient 调用 vLLM/OpenAI 兼容服务，支持多模态与流式输出；
    - 若安装了 LangChain 相关依赖，则优先通过 ChatbotChain 走多步编排链路；
    - 在大模型调用异常时，返回带明显标记的占位回答作为降级策略。
    """

    def __init__(
        self,
        rag_service: RAGService | None = None,
        conv_manager: ConversationManager | None = None,
        llm_client: VLLMHttpClient | None = None,
        prompt_registry: PromptTemplateRegistry | None = None,
    ) -> None:
        self._rag = rag_service or RAGService()
        # 统一策略层入口：回退链路优先走 HybridRAGService（内部根据配置选择 vector/graph/hybrid）。
        self._hybrid_rag = HybridRAGService(rag_service=self._rag)
        self._conv = conv_manager or ConversationManager()
        self._llm = llm_client or VLLMHttpClient()
        self._prompts = prompt_registry or PromptTemplateRegistry()
        self._chain = None

        # 如果安装了 LangChain 相关依赖，则启用 ChatbotChain 作为编排层
        try:
            from app.llm.chains.chatbot_chain import ChatbotChain

            self._chain = ChatbotChain(rag_service=self._rag, conv_manager=self._conv)
            logger.info("ChatbotService: LangChain ChatbotChain enabled.")
        except ImportError:
            logger.warning("ChatbotService: LangChain not available, fallback to simple implementation.")

    async def chat(self, req: ChatRequest) -> ChatResponse:
        # 记录用户消息
        self._conv.append_user_message(req.user_id, req.session_id, req.query)

        # 优先使用 LangChain ChatbotChain（若可用）
        if self._chain is not None:
            answer = await self._chain.run(
                user_id=req.user_id,
                session_id=req.session_id,
                query=req.query,
                enable_rag=req.enable_rag,
                enable_context=req.enable_context,
            )
            # 目前链路内部已处理 RAG 与上下文，外部仅标记 used_rag 为请求开关
            used_rag = req.enable_rag
            context_snippets: list[str] = []
        else:
            context_snippets = []
            used_rag = False
            if req.enable_rag:
                context_snippets = self._hybrid_rag.retrieve(req.query)
                used_rag = len(context_snippets) > 0

            history = []
            if req.enable_context:
                history = self._conv.get_recent_history(req.user_id, req.session_id)
                logger.info("chat history size=%s", len(history))

            # 使用统一 LLM 客户端生成回答（多模态 message）
            messages = self._build_llm_messages(req=req, history=history, context_snippets=context_snippets)

            try:
                answer = await self._llm.chat(model=None, messages=messages)  # type: ignore[arg-type]
            except Exception:  # noqa: BLE001
                logger.exception("ChatbotService: LLM 调用失败，退回占位回答。")
                base = "这是占位回答（大模型暂不可用）。"
                if used_rag:
                    base += f"（已检索到 {len(context_snippets)} 条上下文片段用于参考）"
                answer = base

        # 记录助手消息
        self._conv.append_assistant_message(req.user_id, req.session_id, answer)

        return ChatResponse(answer=answer, used_rag=used_rag, context_snippets=context_snippets)

    async def stream_chat(self, req: ChatRequest) -> AsyncIterator[str]:
        """
        token 级流式输出（基于 vLLM OpenAI 兼容 stream）。
        """
        self._conv.append_user_message(req.user_id, req.session_id, req.query)

        context_snippets: list[str] = []
        if req.enable_rag:
            context_snippets = self._hybrid_rag.retrieve(req.query)

        history = []
        if req.enable_context:
            history = self._conv.get_recent_history(req.user_id, req.session_id)

        messages = self._build_llm_messages(req=req, history=history, context_snippets=context_snippets)

        parts: list[str] = []
        async for delta in self._llm.stream_chat(model=None, messages=messages):  # type: ignore[arg-type]
            parts.append(delta)
            yield delta

        # 流式完成后统一回写完整助手消息，保障会话持久化一致性。
        answer = "".join(parts).strip()
        if answer:
            self._conv.append_assistant_message(req.user_id, req.session_id, answer)

    def _build_llm_messages(
        self,
        req: ChatRequest,
        history: list[dict],
        context_snippets: list[str],
    ) -> list[Dict[str, Any]]:
        """
        构建发送给 vLLM/OpenAI 兼容接口的 messages。
        若提供 image_urls，则使用多模态 content（text + image_url）。
        """
        messages: list[Dict[str, Any]] = []
        tpl = self._prompts.get_template(scene="chatbot", user_id=req.user_id, version=None)
        if tpl and tpl.content:
            messages.append({"role": "system", "content": tpl.content})
        if context_snippets:
            ctx = "\n".join(f"- {c}" for c in context_snippets)
            messages.append({"role": "system", "content": f"以下是与用户问题相关的知识片段，请优先参考：\n{ctx}"})
        for h in history:
            role = h.get("role", "user")
            raw_c = h.get("content", "")
            content = raw_c if isinstance(raw_c, str) else (str(raw_c) if raw_c is not None else "")
            if content:
                messages.append({"role": role, "content": content})

        if req.image_urls:
            # 多模态输入：将文本与多图一起放入 user content 列表。
            content_blocks: list[Dict[str, Any]] = [{"type": "text", "text": req.query}]
            for u in req.image_urls:
                content_blocks.append({"type": "image_url", "image_url": {"url": u}})
            messages.append({"role": "user", "content": content_blocks})
        else:
            messages.append({"role": "user", "content": req.query})
        return messages

