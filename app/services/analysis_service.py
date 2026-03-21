from __future__ import annotations

from app.conversation.manager import ConversationManager
from app.core.logging import get_logger
from app.models.analysis import AnalysisInput, AnalysisResult
from app.rag.rag_service import RAGService

logger = get_logger(__name__)


class AnalysisService:
    """
    综合分析 Agent 服务。

    - 优先使用基于 LangChain 的 AnalysisChain（若依赖可用）；
    - 若未安装 LangChain，则回退到占位实现，仅使用 RAG + 会话记录；
    - 为后续接入 LangGraph、多模态工具调用预留扩展点。
    """

    def __init__(self, rag_service: RAGService | None = None, conv_manager: ConversationManager | None = None) -> None:
        self._rag = rag_service or RAGService()
        self._conv = conv_manager or ConversationManager()
        self._chain = None

        try:
            from app.llm.chains.analysis_chain import AnalysisChain

            self._chain = AnalysisChain(rag_service=self._rag, conv_manager=self._conv)
            logger.info("AnalysisService: LangChain AnalysisChain enabled.")
        except ImportError:
            logger.warning("AnalysisService: LangChain not available, fallback to simple implementation.")

    async def run_analysis(self, data: AnalysisInput) -> AnalysisResult:
        # 记录用户请求到会话
        self._conv.append_user_message(data.user_id, data.session_id, data.query)

        # 优先使用 LangChain AnalysisChain
        if self._chain is not None:
            result = await self._chain.run(data)
            # 记录摘要到会话
            self._conv.append_assistant_message(data.user_id, data.session_id, result.summary)
            return result

        # 回退占位实现
        context_snippets: list[str] = []
        used_rag = False
        if data.enable_rag:
            context_snippets = self._rag.retrieve_context(data.query)
            used_rag = len(context_snippets) > 0

        if data.enable_context:
            history = self._conv.get_recent_history(data.user_id, data.session_id)
            logger.info("analysis history size=%s", len(history))

        summary = "这是综合分析占位结果，后续会由 Agent + 大模型结合多模态数据生成正式报告。"
        details = (
            f"当前请求描述: {data.query}。"
            f" 已关联图像 {len(data.image_ids)} 个、视频片段 {len(data.video_clip_ids)} 个、"
            f"GPS 数据 {len(data.gps_ids)} 条、传感器数据 {len(data.sensor_data_ids)} 条。"
        )

        self._conv.append_assistant_message(data.user_id, data.session_id, summary)

        return AnalysisResult(
            summary=summary,
            details=details,
            used_rag=used_rag,
            context_snippets=context_snippets,
        )

