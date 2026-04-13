from __future__ import annotations

"""
Chatbot LangChain 编排链（企业级骨架实现）。

设计目标：
- 基于 LangChain 构建一条清晰的聊天链路；
- 统一接入 PromptTemplateRegistry、RAGService 与 ConversationManager；
- 使用 OpenAI 兼容协议调用 vLLM（通过 langchain-openai）。

说明：
- 本模块依赖 langchain 及 langchain-openai 等第三方库，需按
  `docs/下一阶段工作清单-未完成说明-实现说明.md` 中说明安装依赖；
- 若运行环境未安装相关库，导入时会抛出 ImportError，由上层服务捕获并回退到占位实现。
"""

from typing import List

from app.conversation.manager import ConversationManager
from app.core.config import get_app_config
from app.core.logging import get_logger
from app.llm.prompt_registry import PromptTemplateRegistry
from app.llm.langsmith_tracker import LangSmithTracker
from app.rag.rag_service import RAGService
from app.rag.agentic import AgenticRAGService, RAGContext, RAGMode

logger = get_logger(__name__)


class ChatbotChain:
    """
    基于 LangChain 的智能客服链路。

    当前版本：
    - 使用 langchain-openai.ChatOpenAI 调用 vLLM（假定 vLLM 暴露 OpenAI 兼容接口）；
    - 使用 PromptTemplateRegistry 获取系统级提示词前缀；
    - 可选根据 RAG 检索结果拼接上下文。
    """

    def __init__(
        self,
        rag_service: RAGService | None = None,
        conv_manager: ConversationManager | None = None,
        prompt_registry: PromptTemplateRegistry | None = None,
    ) -> None:
        try:
            from langchain_openai import ChatOpenAI  # type: ignore[import-not-found]
        except Exception as exc:  # noqa: BLE001
            raise ImportError(
                "langchain-openai is required for ChatbotChain. "
                "Please install dependencies as described in docs/下一阶段工作清单-未完成说明-实现说明.md."
            ) from exc

        cfg = get_app_config()
        llm_cfg = cfg.llm
        default_model = llm_cfg.default_model
        model_cfg = llm_cfg.models[default_model]

        # 使用 ChatOpenAI 适配 vLLM 的 OpenAI 兼容接口
        self._llm = ChatOpenAI(
            model=model_cfg.model_id,
            base_url=model_cfg.endpoint.rstrip("/"),
            api_key=model_cfg.api_key or "EMPTY",
            temperature=model_cfg.temperature,
        )
        base_rag = rag_service or RAGService()
        self._rag = base_rag
        # 为 Chatbot 场景接入 Agentic RAG 基座，当前版本在 AGENTIC 模式下仍复用基础 RAG 实现。
        self._agentic_rag = AgenticRAGService(rag_service=base_rag, default_mode=RAGMode.BASIC)
        self._conv = conv_manager or ConversationManager()
        self._prompts = prompt_registry or PromptTemplateRegistry()
        self._ls_tracker = LangSmithTracker()

    async def run(
        self,
        user_id: str,
        session_id: str,
        query: str,
        enable_rag: bool = True,
        enable_context: bool = True,
        prompt_version: str | None = None,
    ) -> str:
        """
        执行一次聊天链路（多步 Agent Workflow 骨架）：
        - Step1: 意图识别与路由规划；
        - Step2: 按意图选择是否启用 RAG 并检索上下文；
        - Step3: 结合上下文与规划结果调用 LLM 生成回答。
        """
        from langchain_core.messages import AIMessage, HumanMessage, SystemMessage  # type: ignore[import-not-found]

        from app.core.config import get_app_config

        cfg_cb = get_app_config().chatbot
        if prompt_version:
            tpl = self._prompts.get_template(scene="chatbot", user_id=user_id, version=prompt_version)
        else:
            tpl = self._prompts.get_template(
                scene="chatbot",
                user_id=user_id,
                version=None,
                default_version=cfg_cb.default_prompt_version,
            )
        system_prompt = tpl.content if tpl else "你是一个专业的中文智能客服助手。"

        messages: List[object] = [SystemMessage(content=system_prompt)]

        # 1.1 意图识别与路由规划（Agentic 轻量步骤）
        intent_summary: str | None = None
        try:
            intent_summary = await self._analyze_intent(user_id=user_id, session_id=session_id, query=query)
        except Exception:  # noqa: BLE001
            logger.exception("ChatbotChain: intent analysis failed, fallback to simple flow.")
            intent_summary = None
        if intent_summary:
            messages.append(
                SystemMessage(
                    content=(
                        "以下是对当前轮用户意图与建议处理策略的内部规划，请在回答时参考但不要直接暴露给用户：\n"
                        f"{intent_summary}"
                    )
                )
            )

        # 2. 可选拼接会话上下文摘要（当前简单使用最近若干轮原文）
        if enable_context:
            history = self._conv.get_recent_history(user_id, session_id, limit=10)
            for h in history:
                role = h.get("role", "user")
                content = h.get("content", "")
                if not content:
                    continue
                if role == "user":
                    messages.append(HumanMessage(content=content))
                else:
                    # 助手轮次用 AIMessage，避免塞进 System 导致模型不按「多轮对话」理解事实（如用户自称姓名）
                    messages.append(AIMessage(content=content))

        # 3. 可选使用 RAG 检索相关上下文（通过 AgenticRAGService 统一入口）
        if enable_rag:
            rag_ctx = RAGContext(user_id=user_id, session_id=session_id, scene="chatbot")
            rag_result = await self._agentic_rag.retrieve(
                query=query,
                ctx=rag_ctx,
                mode=RAGMode.AGENTIC,
                top_k=None,
            )
            ctx_snippets = rag_result.context_snippets
            if ctx_snippets:
                ctx_text = "\n".join(f"- {t}" for t in ctx_snippets)
                messages.append(SystemMessage(content=f"以下是与用户问题相关的知识片段，请优先参考：\n{ctx_text}"))

        # 4. 当前用户问题
        messages.append(HumanMessage(content=query))

        # 5. 调用 LangChain LLM
        resp = await self._llm.ainvoke(messages)
        answer = resp.content if hasattr(resp, "content") else str(resp)

        # 6. LangSmith trace（若启用）
        self._ls_tracker.log_run(
            name="chatbot",
            run_type="llm",
            inputs={
                "user_id": user_id,
                "session_id": session_id,
                "query": query,
                "enable_rag": enable_rag,
                "enable_context": enable_context,
            },
            outputs={"answer": answer},
            metadata={"scene": "chatbot"},
        )
        return answer

    async def _analyze_intent(self, user_id: str, session_id: str, query: str) -> str:
        """
        Chatbot 场景的轻量意图识别与路由规划步骤。

        当前版本：
        - 使用同一 LLM 实例输出一段简要文本，概括：意图类别、是否建议使用 RAG、可能的处理策略；
        - 结果仅作为 SystemMessage 注入主链路，不直接返回给调用方。
        """
        from langchain_core.messages import HumanMessage, SystemMessage  # type: ignore[import-not-found]

        planner_system = (
            "你是一个对话意图识别与路由规划助手。请用简短中文总结："
            "1) 当前用户意图类型（如：FAQ/文档问答/闲聊/NL2SQL 候选/其他）；"
            "2) 是否建议使用知识库（RAG）；3) 建议回答策略要点。"
        )
        messages: List[object] = [
            SystemMessage(content=planner_system),
            HumanMessage(
                content=(
                    f"用户ID: {user_id}, 会话ID: {session_id}\n"
                    f"当前问题: {query}"
                )
            ),
        ]
        resp = await self._llm.ainvoke(messages)
        summary = resp.content if hasattr(resp, "content") else str(resp)
        logger.info("ChatbotChain intent summary: %s", summary)
        return summary

