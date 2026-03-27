from __future__ import annotations

from typing import List, Optional

from app.conversation.manager import ConversationManager
from app.core.logging import get_logger
from app.llm.client import VLLMHttpClient
from app.llm.config_registry import LLMConfigRegistry
from app.llm.prompt_registry import PromptTemplateRegistry
from app.llm.langsmith_tracker import LangSmithTracker
from app.models.llm import ChatMessage, LLMInferenceRequest, LLMInferenceResponse
from app.rag.hybrid_rag_service import HybridRAGService
from app.rag.rag_service import RAGService
from app.rag.agentic import AgenticRAGService, RAGContext, RAGMode

logger = get_logger(__name__)


class LLMInferenceService:
    """
    通用大模型推理服务。

    设计目标：
    - 统一处理 /llm/infer 接口的业务逻辑；
    - 根据配置与入参决定是否启用 RAG 与上下文；
    - 尽可能通过 LangChain 进行编排，未安装相关依赖时回退到直接调用 VLLMHttpClient。
    """

    def __init__(
        self,
        rag_service: RAGService | None = None,
        conv_manager: ConversationManager | None = None,
        prompt_registry: PromptTemplateRegistry | None = None,
        llm_client: VLLMHttpClient | None = None,
    ) -> None:
        base_rag = rag_service or RAGService()
        self._rag = base_rag
        # 统一策略入口：基础检索默认走 HybridRAGService（内部按配置决定 vector/graph/hybrid）。
        self._hybrid_rag = HybridRAGService(rag_service=base_rag)
        # 为通用推理服务接入 Agentic RAG 基座，当前仍以 BASIC 行为为主，仅通过 rag_mode 预留扩展能力。
        # 该能力继续保留：当 rag_mode=agentic 时，仍由 AgenticRAGService 执行多步计划检索。
        self._agentic_rag = AgenticRAGService(rag_service=base_rag, default_mode=RAGMode.BASIC)
        self._conv = conv_manager or ConversationManager()
        self._prompts = prompt_registry or PromptTemplateRegistry()
        self._llm_client = llm_client or VLLMHttpClient()
        self._cfg_registry = LLMConfigRegistry()
        self._ls_tracker = LangSmithTracker()

        # 可选的 LangChain LLM（若依赖存在）
        self._lc_chat_model = None
        try:
            from langchain_openai import ChatOpenAI  # type: ignore[import-not-found]

            model_cfg = self._cfg_registry.get_model()
            self._lc_chat_model = ChatOpenAI(
                model=model_cfg.model_id,
                base_url=model_cfg.endpoint.rstrip("/"),
                api_key=model_cfg.api_key or "EMPTY",
                temperature=model_cfg.temperature,
            )
            logger.info("LLMInferenceService: LangChain ChatOpenAI enabled.")
        except Exception:  # noqa: BLE001
            logger.warning("LLMInferenceService: LangChain not available, fallback to VLLMHttpClient.")

    async def infer(self, req: LLMInferenceRequest) -> LLMInferenceResponse:
        """
        执行一次大模型推理。
        """
        model_name = req.model or self._cfg_registry.default_model

        # 记录用户消息（用于会话上下文）
        user_content = self._get_user_visible_content(req)
        self._conv.append_user_message(req.user_id, req.session_id, user_content)

        # 1. Agentic 预处理（仅在 rag_mode=agentic 且 LangChain 可用时启用）
        planner_summary: Optional[str] = None
        if (req.rag_mode or "").lower() == "agentic" and self._lc_chat_model is not None:
            try:
                planner_summary = await self._analyze_intent_and_plan(user_content=user_content, req=req)
            except Exception:  # noqa: BLE001
                logger.exception("LLMInferenceService: agentic planner failed, fallback to basic flow.")
                planner_summary = None

        # 2. 决定是否启用 RAG 与上下文
        context_snippets: List[str] = []
        used_rag = False

        if req.enable_rag:
            mode = RAGMode.AGENTIC if (req.rag_mode or "").lower() == "agentic" else RAGMode.BASIC
            rag_query = user_content
            if planner_summary:
                rag_query = f"【问题诊断】{planner_summary}\n【用户问题】{user_content}"
            if mode == RAGMode.AGENTIC:
                # 保持原有 AgenticRAGService + RAGService 多步检索策略。
                rag_ctx = RAGContext(user_id=req.user_id, session_id=req.session_id, scene="llm_inference")
                rag_result = await self._agentic_rag.retrieve(
                    query=rag_query,
                    ctx=rag_ctx,
                    mode=mode,
                    top_k=None,
                )
                context_snippets = rag_result.context_snippets
            else:
                # BASIC 模式切到统一策略入口（HybridRAGService -> RetrievalPolicy）。
                context_snippets = self._hybrid_rag.retrieve(rag_query, top_k=None, namespace=None)
            used_rag = len(context_snippets) > 0

        history_messages: List[ChatMessage] = []
        if req.enable_context:
            history = self._conv.get_recent_history(req.user_id, req.session_id, limit=10)
            for h in history:
                role = h.get("role", "user")
                content = h.get("content", "")
                if not content:
                    continue
                history_messages.append(ChatMessage(role=role, content=content))

        # 3. 获取 Prompt 模板
        tpl = self._prompts.get_template(scene="llm_inference", user_id=req.user_id, version=req.prompt_version)
        system_prompt: Optional[str] = tpl.content if tpl else None
        used_prompt_version = tpl.version if tpl else None

        # 4. 调用底层大模型（优先通过 LangChain ChatOpenAI）
        if self._lc_chat_model is not None:
            answer = await self._infer_via_langchain(
                user_content=user_content,
                system_prompt=system_prompt,
                history=history_messages,
                ctx_snippets=context_snippets,
            )
        else:
            answer = await self._infer_via_llm_client(
                model_name=model_name,
                user_content=user_content,
                system_prompt=system_prompt,
                history=history_messages,
                ctx_snippets=context_snippets,
            )

        # 5. 记录助手回复
        self._conv.append_assistant_message(req.user_id, req.session_id, answer)

        resp = LLMInferenceResponse(
            answer=answer,
            model=model_name,
            prompt_version=used_prompt_version,
            used_rag=used_rag,
            context_snippets=context_snippets,
        )

        # 6. LangSmith trace（若启用）
        self._ls_tracker.log_run(
            name="llm_inference",
            run_type="llm",
            inputs={
                "user_id": req.user_id,
                "session_id": req.session_id,
                "model": model_name,
                "prompt_version": used_prompt_version,
                "enable_rag": req.enable_rag,
                "enable_context": req.enable_context,
            },
            outputs={"answer": resp.answer},
            metadata={"scene": "llm_inference"},
        )

        return resp

    async def _analyze_intent_and_plan(self, user_content: str, req: LLMInferenceRequest) -> str:
        """
        Agentic 模式下的轻量问题诊断与规划步骤。

        说明：
        - 当前实现仅在 LangChain 可用时启用；
        - 返回一段简要的分析总结文本，用于后续 RAG 检索与 Prompt 增强。
        """
        from langchain_core.messages import HumanMessage, SystemMessage  # type: ignore[import-not-found]

        planner_system = (
            "你是一个用于分析用户问题并给出简要分类与子任务规划的助手。"
            "请用简短中文，总结：问题类型（如：问答/闲聊/分析/NL2SQL 候选/其他），"
            "是否建议使用知识库（RAG），以及最多 3 个子问题要点。"
        )
        messages: List[object] = [
            SystemMessage(content=planner_system),
            HumanMessage(
                content=(
                    f"用户ID: {req.user_id}, 会话ID: {req.session_id}\n"
                    f"当前问题: {user_content}"
                )
            ),
        ]
        resp = await self._lc_chat_model.ainvoke(messages)  # type: ignore[union-attr]
        summary = resp.content if hasattr(resp, "content") else str(resp)
        logger.info("LLMInferenceService planner summary: %s", summary)
        return summary

    @staticmethod
    def _get_user_visible_content(req: LLMInferenceRequest) -> str:
        """
        提取用于 RAG 与会话记录的用户可见内容。
        """
        if req.messages:
            # 使用最后一条 user 消息作为当前问题
            for msg in reversed(req.messages):
                if msg.role == "user" and msg.content:
                    return msg.content
            # 回退：若找不到 user 消息，则使用全部内容拼接
            return "\n".join(m.content for m in req.messages if m.content)
        if req.prompt:
            return req.prompt
        return ""

    async def _infer_via_langchain(
        self,
        user_content: str,
        system_prompt: Optional[str],
        history: List[ChatMessage],
        ctx_snippets: List[str],
    ) -> str:
        """
        使用 LangChain ChatOpenAI 执行推理。
        """
        from langchain_core.messages import HumanMessage, SystemMessage  # type: ignore[import-not-found]

        messages: List[object] = []
        if system_prompt:
            messages.append(SystemMessage(content=system_prompt))

        for h in history:
            if h.role == "user":
                messages.append(HumanMessage(content=h.content))
            else:
                messages.append(SystemMessage(content=f"[历史助手回复]{h.content}"))

        if ctx_snippets:
            ctx_text = "\n".join(f"- {t}" for t in ctx_snippets)
            messages.append(SystemMessage(content=f"以下是与用户问题相关的知识片段，请优先参考：\n{ctx_text}"))

        messages.append(HumanMessage(content=user_content))

        resp = await self._lc_chat_model.ainvoke(messages)  # type: ignore[union-attr]
        return resp.content if hasattr(resp, "content") else str(resp)

    async def _infer_via_llm_client(
        self,
        model_name: str,
        user_content: str,
        system_prompt: Optional[str],
        history: List[ChatMessage],
        ctx_snippets: List[str],
    ) -> str:
        """
        使用内部 VLLMHttpClient 执行推理（在未安装 LangChain 时的回退实现）。
        """
        parts: List[str] = []
        if system_prompt:
            parts.append(system_prompt)
        for h in history:
            prefix = "用户" if h.role == "user" else "助手"
            parts.append(f"{prefix}: {h.content}")
        if ctx_snippets:
            parts.append("以下是与问题相关的知识片段：")
            parts.extend(f"- {t}" for t in ctx_snippets)
        parts.append(f"当前问题：{user_content}")

        prompt = "\n".join(parts)
        return await self._llm_client.generate(model=model_name, prompt=prompt)

