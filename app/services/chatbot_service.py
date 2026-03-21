from __future__ import annotations

from app.conversation.manager import ConversationManager
from app.core.logging import get_logger
from app.models.chatbot import ChatRequest, ChatResponse
from app.rag.rag_service import RAGService

logger = get_logger(__name__)


class ChatbotService:
    """
    智能客服基础服务（V1 占位版）。

    当前实现：
    - 可选使用 RAGService 检索上下文（占位逻辑）；
    - 使用 ConversationManager 追加与读取会话历史；
    - 生成一个简单的占位回答，用于打通 API 与会话/RAG 集成链路。

    后续将：
    - 接入 LangChain/LangGraph 构建真实的 Chatbot 链；
    - 使用大模型（通过 LLMClient）基于 query + RAG 上下文 + 会话历史生成回答。
    """

    def __init__(self, rag_service: RAGService | None = None, conv_manager: ConversationManager | None = None) -> None:
        self._rag = rag_service or RAGService()
        self._conv = conv_manager or ConversationManager()
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
                context_snippets = self._rag.retrieve_context(req.query)
                used_rag = len(context_snippets) > 0

            if req.enable_context:
                history = self._conv.get_recent_history(req.user_id, req.session_id)
                logger.info("chat history size=%s", len(history))

            # 占位回答：仅在未启用 LangChain 时使用
            answer_parts = ["这是占位回答，后续会由大模型生成。"]
            if used_rag:
                answer_parts.append(f"（已检索到 {len(context_snippets)} 条上下文片段用于参考）")
            answer = " ".join(answer_parts)

        # 记录助手消息
        self._conv.append_assistant_message(req.user_id, req.session_id, answer)

        return ChatResponse(answer=answer, used_rag=used_rag, context_snippets=context_snippets)

