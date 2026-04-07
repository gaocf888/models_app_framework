from __future__ import annotations

from app.conversation.manager import ConversationManager
from app.core.config import get_app_config
from app.core.logging import get_logger
from app.models.chatbot import ChatRequest, ChatResponse
from app.llm.client import VLLMHttpClient
from app.llm.graphs import ChatbotLangGraphRunner
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
        self._chatbot_cfg = get_app_config().chatbot
        self._graph_runner = ChatbotLangGraphRunner(
            rag_service=self._rag,
            conv_manager=self._conv,
            llm_client=self._llm,
            prompt_registry=self._prompts,
        )
        self._chain = None

        # 如果安装了 LangChain 相关依赖，则启用 ChatbotChain 作为编排层
        try:
            from app.llm.chains.chatbot_chain import ChatbotChain

            self._chain = ChatbotChain(rag_service=self._rag, conv_manager=self._conv)
            logger.info("ChatbotService: LangChain ChatbotChain enabled.")
        except ImportError:
            logger.warning("ChatbotService: LangChain not available, fallback to simple implementation.")

    async def chat(self, req: ChatRequest) -> ChatResponse:
        # 不在此处提前 append_user：须先取历史再组 messages，否则当前句已进 history，
        # _build_llm_messages / ChatbotChain 再追加本轮 query，会造成「双份当前用户句」且易干扰多轮理解。
        # 本轮 user/assistant 在得到 answer 后统一写入（见文末）。

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

        self._conv.append_user_message(req.user_id, req.session_id, req.query)
        self._conv.append_assistant_message(req.user_id, req.session_id, answer)

        return ChatResponse(answer=answer, used_rag=used_rag, context_snippets=context_snippets)

    async def stream_chat(self, req: ChatRequest) -> AsyncIterator[str]:
        """
        token 级流式输出（基于 vLLM OpenAI 兼容 stream）。

        会话写入顺序：先按「不含本轮」的历史组 messages，流式结束后再 append 本轮 user + assistant，
        与 `chat()` 非流式路径一致，避免历史里先插入当前用户句导致重复与上下文错乱。
        """
        async for ev in self.stream_chat_events(req):
            if ev.get("type") == "delta":
                yield str(ev.get("delta") or "")

    async def stream_chat_events(self, req: ChatRequest) -> AsyncIterator[Dict[str, Any]]:
        """
        结构化流式事件输出（供 API 层组装 SSE payload 使用）。

        事件类型：
        - delta: {"type": "delta", "delta": "..."}
        - finished: {"type": "finished", "meta": {...}}
        """
        # 显式关闭 graph：走 legacy 流式实现，确保开关语义符合部署预期。
        if not self._chatbot_cfg.graph_enabled:
            async for ev in self._stream_chat_legacy_events(req):
                yield ev
            return

        try:
            async for ev in self._graph_runner.run_stream_events(req):
                yield ev
            return
        except Exception:
            if not self._chatbot_cfg.fallback_legacy_on_error:
                raise
            logger.exception("ChatbotService.stream_chat_events graph failed, fallback to legacy path.")
            async for ev in self._stream_chat_legacy_events(req):
                yield ev

    async def _stream_chat_legacy_events(self, req: ChatRequest) -> AsyncIterator[Dict[str, Any]]:
        """
        旧版流式路径（兜底/回退专用）。

        说明：
        - 仅在 graph 关闭或 graph 运行异常且允许回退时启用；
        - 保持与历史行为一致：检索 -> 历史 -> 组 messages -> vLLM stream -> 会话写入。
        """
        context_snippets: list[str] = []
        if req.enable_rag:
            context_snippets = self._hybrid_rag.retrieve(req.query)

        history: list[dict] = []
        if req.enable_context:
            history = self._conv.get_recent_history(
                req.user_id,
                req.session_id,
                limit=max(1, int(self._chatbot_cfg.history_limit)),
            )

        messages = self._build_llm_messages(req=req, history=history, context_snippets=context_snippets)
        parts: list[str] = []
        async for delta in self._llm.stream_chat(model=None, messages=messages):  # type: ignore[arg-type]
            parts.append(delta)
            yield {"type": "delta", "delta": delta}

        answer = "".join(parts).strip()
        self._conv.append_user_message(req.user_id, req.session_id, req.query)
        if answer:
            self._conv.append_assistant_message(req.user_id, req.session_id, answer)
        yield {
            "type": "finished",
            "meta": {
                "used_rag": bool(context_snippets),
                "intent_label": "kb_qa",
                "retrieval_attempts": 1 if req.enable_rag else 0,
                "rag_engine": "hybrid" if req.enable_rag else None,
                "status": "answered",
                "duration_ms": None,
                "terminate_reason": None,
            },
        }

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

        # 模型层已过滤空串；此处再防御，避免任意路径带入空 URL 触发 vLLM「empty image」400
        image_urls = [u for u in req.image_urls if isinstance(u, str) and u.strip()]
        if image_urls:
            content_blocks: list[Dict[str, Any]] = [{"type": "text", "text": req.query}]
            for u in image_urls:
                content_blocks.append({"type": "image_url", "image_url": {"url": u}})
            messages.append({"role": "user", "content": content_blocks})
        else:
            messages.append({"role": "user", "content": req.query})
        return messages

