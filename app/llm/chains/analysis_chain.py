from __future__ import annotations

"""
分析场景 LangChain 编排链（企业级骨架）。

- 使用 LangChain ChatOpenAI 调用 vLLM 的 OpenAI 兼容接口；
- 集成 PromptTemplateRegistry、RAGService 与 ConversationManager；
- 后续可扩展为 LangGraph 多步 Workflow（工具调用小模型、多模态数据等）。
"""

from typing import List, Optional

from app.conversation.manager import ConversationManager
from app.core.config import get_app_config
from app.core.logging import get_logger
from app.llm.prompt_registry import PromptTemplateRegistry
from app.llm.langsmith_tracker import LangSmithTracker
from app.models.analysis import AnalysisInput, AnalysisResult
from app.rag.rag_service import RAGService
from app.rag.agentic import AgenticRAGService, RAGContext, RAGMode

logger = get_logger(__name__)


class AnalysisChain:
    """
    基于 LangChain 的综合分析链路。
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
                "langchain-openai is required for AnalysisChain. "
                "Please install dependencies as described in docs/下一阶段工作清单-未完成说明-实现说明.md."
            ) from exc

        cfg = get_app_config()
        llm_cfg = cfg.llm
        default_model = llm_cfg.default_model
        model_cfg = llm_cfg.models[default_model]

        self._llm = ChatOpenAI(
            model=model_cfg.model_id,
            base_url=model_cfg.endpoint.rstrip("/"),
            api_key=model_cfg.api_key or "EMPTY",
            temperature=model_cfg.temperature,
        )
        base_rag = rag_service or RAGService()
        # 在综合分析场景下默认使用 Agentic RAG 基座，后续可在此处扩展多步规划与工具调用。
        self._rag = base_rag
        self._agentic_rag = AgenticRAGService(rag_service=base_rag, default_mode=RAGMode.BASIC)
        self._conv = conv_manager or ConversationManager()
        self._prompts = prompt_registry or PromptTemplateRegistry()
        self._ls_tracker = LangSmithTracker()

    async def run(self, data: AnalysisInput, prompt_version: Optional[str] = None) -> AnalysisResult:
        """
        执行一次综合分析：
        - 根据 scene=analysis + user_id 选择 Prompt 模板；
        - 可选使用 RAG 检索文本上下文；
        - 可选拼接历史会话；
        - 在提示中附加多模态 ID 占位信息；
        - 调用 LangChain LLM 生成结构化分析结果。
        """
        from langchain_core.messages import HumanMessage, SystemMessage  # type: ignore[import-not-found]

        # 0. Agent 级分析规划（可选步骤）
        plan_summary: Optional[str] = None
        try:
            plan_summary = await self._plan_analysis(data)
        except Exception:  # noqa: BLE001
            logger.exception("AnalysisChain: planning step failed, fallback to simple flow.")
            plan_summary = None

        # 1. Prompt 模板
        tpl = self._prompts.get_template(scene="analysis", user_id=data.user_id, version=prompt_version)
        system_prompt = tpl.content if tpl else (
            "你是一名综合分析助手，请根据提供的上下文和多模态数据，给出清晰的中文分析结论。"
        )

        messages: List[object] = [SystemMessage(content=system_prompt)]

        # 1.1 插入分析规划结果（若有）
        if plan_summary:
            messages.append(
                SystemMessage(
                    content=(
                        "以下是针对本次综合分析需求的预先规划，请在后续分析中参考：\n"
                        f"{plan_summary}"
                    )
                )
            )

        # 2. 历史上下文
        if data.enable_context:
            history = self._conv.get_recent_history(data.user_id, data.session_id, limit=10)
            for h in history:
                role = h.get("role", "user")
                content = h.get("content", "")
                if not content:
                    continue
                if role == "user":
                    messages.append(HumanMessage(content=content))
                else:
                    messages.append(SystemMessage(content=f"[历史助手回复]{content}"))

        # 3. RAG 检索（结合 AgenticRAGService）
        context_snippets: List[str] = []
        used_rag = False
        if data.enable_rag:
            # 使用 AgenticRAGService 统一检索入口，当前版本在 AGENTIC 模式下仍复用基础 RAG 实现。
            rag_ctx = RAGContext(user_id=data.user_id, session_id=data.session_id, scene="analysis")
            rag_result = await self._agentic_rag.retrieve(
                query=data.query,
                ctx=rag_ctx,
                mode=RAGMode.AGENTIC,
                top_k=None,
            )
            context_snippets = rag_result.context_snippets
            used_rag = len(context_snippets) > 0
            if context_snippets:
                ctx_text = "\n".join(f"- {t}" for t in context_snippets)
                messages.append(SystemMessage(content=f"以下是与分析需求相关的知识片段，请充分参考：\n{ctx_text}"))

        # 4. 多模态占位信息
        multimodal_summary = (
            f"本次分析关联图像 {len(data.image_ids)} 个、视频片段 {len(data.video_clip_ids)} 个、"
            f"GPS 数据 {len(data.gps_ids)} 条、传感器数据 {len(data.sensor_data_ids)} 条。"
            "你可以在分析中提及这些数据源的可能含义，但无需生成具体可视化。"
        )
        messages.append(SystemMessage(content=multimodal_summary))

        # 5. 当前分析需求
        messages.append(HumanMessage(content=data.query))

        resp = await self._llm.ainvoke(messages)
        answer = resp.content if hasattr(resp, "content") else str(resp)

        result = AnalysisResult(
            summary=answer,
            details=None,
            used_rag=used_rag,
            context_snippets=context_snippets,
        )

        # LangSmith trace（若启用）
        self._ls_tracker.log_run(
            name="analysis",
            run_type="llm",
            inputs={
                "user_id": data.user_id,
                "session_id": data.session_id,
                "query": data.query,
                "enable_rag": data.enable_rag,
                "enable_context": data.enable_context,
            },
            outputs={"summary": result.summary},
            metadata={"scene": "analysis"},
        )

        return result

    async def _plan_analysis(self, data: AnalysisInput) -> str:
        """
        综合分析场景的多步 Agent 规划步骤。

        当前版本：
        - 使用同一 LLM 实例生成一个简要的分析计划（目标 + 子任务 + 所需证据类型）；
        - 结果作为 SystemMessage 注入主分析链路，不单独返回给调用方。
        """
        from langchain_core.messages import HumanMessage, SystemMessage  # type: ignore[import-not-found]

        planner_system = (
            "你是一名综合分析规划助手。请根据用户的分析需求与多模态数据概览，"
            "用简短中文给出：1) 总体分析目标；2) 1~3 个子任务；3) 每个子任务需要重点关注的证据类型。"
        )
        multimodal_brief = (
            f"图像: {len(data.image_ids)} 个, 视频片段: {len(data.video_clip_ids)} 个, "
            f"GPS: {len(data.gps_ids)} 条, 传感器: {len(data.sensor_data_ids)} 条。"
        )
        messages: List[object] = [
            SystemMessage(content=planner_system),
            HumanMessage(
                content=(
                    f"用户ID: {data.user_id}, 会话ID: {data.session_id}\n"
                    f"多模态数据概览: {multimodal_brief}\n"
                    f"分析需求: {data.query}"
                )
            ),
        ]
        resp = await self._llm.ainvoke(messages)
        summary = resp.content if hasattr(resp, "content") else str(resp)
        logger.info("AnalysisChain planner summary: %s", summary)
        return summary

