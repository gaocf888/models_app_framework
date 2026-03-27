from __future__ import annotations

from app.conversation.manager import ConversationManager
from app.core.logging import get_logger
from app.models.analysis import AnalysisInput, AnalysisResult
from app.llm.client import VLLMHttpClient
from app.llm.prompt_registry import PromptTemplateRegistry
from app.rag.hybrid_rag_service import HybridRAGService
from app.rag.rag_service import RAGService

logger = get_logger(__name__)


class AnalysisService:
    """
    综合分析 Agent 服务。

    - 优先使用基于 LangChain 的 AnalysisChain（若依赖可用）；
    - 若未安装 LangChain，则回退到占位实现，仅使用 RAG + 会话记录；
    - 为后续接入 LangGraph、多模态工具调用预留扩展点。
    """

    def __init__(
        self,
        rag_service: RAGService | None = None,
        conv_manager: ConversationManager | None = None,
        llm_client: VLLMHttpClient | None = None,
        prompt_registry: PromptTemplateRegistry | None = None,
    ) -> None:
        self._rag = rag_service or RAGService()
        # 统一策略层入口：回退链路优先走 HybridRAGService。
        self._hybrid_rag = HybridRAGService(rag_service=self._rag)
        self._conv = conv_manager or ConversationManager()
        self._llm = llm_client or VLLMHttpClient()
        self._prompts = prompt_registry or PromptTemplateRegistry()
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

        # 回退实现：使用统一 LLM 客户端生成分析结果
        context_snippets: list[str] = []
        used_rag = False
        if data.enable_rag:
            context_snippets = self._hybrid_rag.retrieve(data.query)
            used_rag = len(context_snippets) > 0

        history = []
        if data.enable_context:
            history = self._conv.get_recent_history(data.user_id, data.session_id)
            logger.info("analysis history size=%s", len(history))

        tpl = self._prompts.get_template(scene="analysis", user_id=data.user_id, version=None)
        system_prompt = tpl.content if tpl else None

        parts: list[str] = []
        if system_prompt:
            parts.append(system_prompt)
        if context_snippets:
            ctx = "\n".join(f"- {c}" for c in context_snippets)
            parts.append(f"以下是与分析任务相关的知识片段，请参考：\n{ctx}")
        if history:
            hist_text = "\n".join(f"[历史]{h.get('role','user')}: {h.get('content','')}" for h in history)
            parts.append(f"以下是与用户的历史对话：\n{hist_text}")

        multimodal_info = (
            f"图像 {len(data.image_ids)} 个，视频片段 {len(data.video_clip_ids)} 个，"
            f"GPS 数据 {len(data.gps_ids)} 条，传感器数据 {len(data.sensor_data_ids)} 条。"
        )
        parts.append(f"用户当前分析需求：{data.query}\n相关多模态数据概览：{multimodal_info}")
        prompt = "\n\n".join(p for p in parts if p)

        try:
            summary = await self._llm.generate(model=None, prompt=prompt)  # type: ignore[arg-type]
        except Exception:  # noqa: BLE001
            logger.exception("AnalysisService: LLM 调用失败，退回占位结果。")
            summary = "这是综合分析占位结果（大模型暂不可用），后续会由 Agent + 大模型生成正式报告。"

        details = f"当前请求描述: {data.query}。{multimodal_info}"

        self._conv.append_assistant_message(data.user_id, data.session_id, summary)

        return AnalysisResult(
            summary=summary,
            details=details,
            used_rag=used_rag,
            context_snippets=context_snippets,
        )

