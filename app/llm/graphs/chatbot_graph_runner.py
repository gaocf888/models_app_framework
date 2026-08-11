from __future__ import annotations

import re
import time
import asyncio
from typing import Any, AsyncIterator, Dict, List, Optional

from app.conversation.manager import ConversationManager
from app.core.config import get_app_config
from app.core.logging import get_logger
from app.llm.client import VLLMHttpClient
from app.llm.graphs.langgraph_redis_checkpointer import open_langgraph_redis_saver
from app.llm.langsmith_tracker import LangSmithTracker
from app.llm.prompt_registry import PromptTemplateRegistry
from app.models.chatbot import ChatRequest
from app.rag.agentic import AgenticRAGService, RAGContext, RAGMode
from app.rag.hybrid_rag_service import HybridRAGService
from app.rag.rag_service import RAGService

from .chatbot_citation_stream import CitationStreamParser, citation_stream_enabled, max_citation_ref_index
from .chatbot_follow_up import build_suggested_questions
from .chatbot_graph_state import ChatbotGraphState
from .chatbot_hitl import (
    ChatbotHitlValidationError,
    apply_chatbot_hitl_action,
    build_hitl_sse_event,
    hitl_button_label,
    hitl_globally_enabled,
    prepare_intent_disambiguation_hitl_patch,
    prepare_intent_hitl_patch,
    prepare_nl2sql_hitl_patch,
    should_trigger_intent_hitl,
    should_trigger_nl2sql_hitl,
)
from .chatbot_hitl_display import (
    ACTION_ROUTE_CLARIFY,
    HITL_KIND_INTENT_DISAMBIGUATION,
    build_hitl_interrupt_payload,
    format_hitl_assistant_message,
    format_hitl_user_choice_message,
)
from .chatbot_intent_disambiguation import (
    generate_intent_disambiguation,
    intent_disambiguation_enabled,
)
from .chatbot_hitl_session_store import (
    create_chatbot_hitl_resume_session,
    delete_chatbot_hitl_resume_session,
    get_chatbot_hitl_resume_session,
)
from .chatbot_intent import classify_chatbot_intent_async
from .chatbot_nl2sql_answer import (
    Nl2sqlAnalysisStreamPlan,
    finalize_streamed_nl2sql_analysis,
    iter_analysis_llm_deltas,
    run_chatbot_nl2sql_query,
    strip_nl2sql_analysis_section_headings,
)
from .chatbot_rag_citations import chunks_to_rag_context, filter_rag_citation_dicts
from .chatbot_retrieval_query import build_retrieval_query_with_anaphora, format_rag_snippets_system_block
from .chatbot_faq_soft_direct import (
    evaluate_faq_soft_direct,
    format_rag_snippets_for_generation,
    snippets_for_llm_generation,
)
from .chatbot_rag_scope import augment_retrieval_query_for_plant_kb, resolve_rag_namespace
from .chatbot_dialogue_anchor import build_dialogue_anchor_block
from .chatbot_anaphora_detect import classify_anaphora_rules
from .chatbot_anaphora_llm import maybe_apply_coref_llm
from .chatbot_anaphora_store import get_anaphora_slots, slot_bullets_list, update_anaphora_slots_after_assistant
from .chatbot_similar_cases import (
    FaultCaseGateInput,
    format_similar_cases_block,
    retrieve_similar_case_snippets,
    run_fault_case_gate_decision,
)
from app.llm.context_budget import ensure_chatbot_stream_max_tokens
from .chatbot_llm_messages import assemble_chatbot_llm_messages, trim_history_and_build_chatbot_messages
from app.services.nl2sql_service import NL2SQLService
from app.services.chatbot_image_utils import build_user_message_with_images
from app.services.chatbot_outline import ChatbotOutlineStore
from app.conversation.message_id import build_conversation_message_id

logger = get_logger(__name__)


class ChatbotLangGraphRunner:
    """
    智能客服 LangGraph 运行器（流式主链路）。

    这层负责“编排”，不负责“模型协议”：
    - 编排侧：意图判断、RAG 路由、C-RAG 重试、消息组装、终止语义；
    - 执行侧：仍使用现有 `VLLMHttpClient.stream_chat` 发起模型流式调用。

    为什么这样分层：
    - 保持与历史实现兼容（不重写 vLLM 协议栈）；
    - 便于灰度：编排可随时切换，底层推理调用稳定不动；
    - 排障时能明确区分“图路由问题”与“模型服务问题”。
    """

    def __init__(
        self,
        rag_service: RAGService,
        conv_manager: ConversationManager,
        llm_client: VLLMHttpClient,
        prompt_registry: PromptTemplateRegistry,
        outline_store: ChatbotOutlineStore | None = None,
    ) -> None:
        self._rag = rag_service
        self._hybrid_rag = HybridRAGService(rag_service=rag_service)
        self._agentic_rag = AgenticRAGService(rag_service=rag_service, default_mode=RAGMode.BASIC)
        self._conv = conv_manager
        self._llm = llm_client
        self._prompts = prompt_registry
        self._outline_store = outline_store
        self._nl2sql = NL2SQLService(conv_manager=self._conv)
        self._ls = LangSmithTracker()

        cfg = get_app_config().chatbot
        self._graph_enabled = cfg.graph_enabled
        self._intent_enabled = cfg.intent_enabled
        self._intent_output_labels = {x.strip().lower() for x in (cfg.intent_output_labels or []) if x.strip()}
        self._crag_enabled = cfg.crag_enabled
        self._persist_partial = cfg.persist_partial_on_disconnect
        self._max_graph_latency_ms = max(1000, int(cfg.max_graph_latency_ms))
        self._history_limit = max(1, int(cfg.history_limit))
        self._max_attempts = max(1, int(cfg.crag_max_attempts))
        self._min_score = max(0.0, min(1.0, float(cfg.crag_min_score)))
        self._rag_mode = (cfg.rag_engine_mode or "agentic").lower()
        self._rag_fallback = (cfg.rag_engine_fallback or "hybrid").lower()
        self._rewrite_max_len = max(20, int(cfg.max_rewrite_query_length))
        self._checkpoint_backend = (cfg.checkpoint_backend or "none").lower()
        self._checkpoint_redis_url = cfg.checkpoint_redis_url
        self._checkpoint_namespace = cfg.checkpoint_namespace or "chatbot_graph"
        self._similar_case_enabled = bool(cfg.similar_case_enabled)
        self._similar_case_namespace = (cfg.similar_case_namespace or "事故案例").strip() or "事故案例"
        self._similar_case_top_k = max(1, int(cfg.similar_case_top_k))
        self._fault_detect_enabled = bool(cfg.fault_detect_enabled)
        self._fault_vision_enabled = bool(cfg.fault_vision_enabled)
        self._fault_detect_mode = (cfg.fault_detect_mode or "hybrid").lower()
        self._fault_min_confidence = max(0.0, min(1.0, float(cfg.fault_min_confidence)))
        self._nl2sql_route_enabled_cfg = bool(cfg.nl2sql_route_enabled)
        self._main_llm_temperature = cfg.main_llm_temperature
        self._default_prompt_version = (cfg.default_prompt_version or "boiler_v1").strip()
        self._suggested_questions_enabled = bool(cfg.suggested_questions_enabled)
        self._suggested_questions_max = max(1, min(10, int(cfg.suggested_questions_max)))

        self._anaphora_config_path = cfg.anaphora_config_path
        self._anaphora_retrieval_fusion = bool(cfg.anaphora_retrieval_fusion_enabled)
        self._anaphora_fusion_max_chars = max(800, int(cfg.anaphora_fusion_max_chars))
        self._anaphora_anchor_enabled = bool(cfg.anaphora_anchor_block_enabled)
        self._anaphora_anchor_max_chars = max(400, int(cfg.anaphora_anchor_max_chars))
        self._anaphora_slots_enabled = bool(cfg.anaphora_slots_enabled)
        self._anaphora_slots_max_bullets = max(2, min(20, int(cfg.anaphora_slots_max_bullets)))
        self._anaphora_llm_gate = bool(cfg.anaphora_llm_gate_enabled)
        self._anaphora_llm_timeout = float(cfg.anaphora_llm_timeout_sec)
        self._anaphora_llm_model = cfg.anaphora_llm_model
        self._anaphora_expose_meta = bool(cfg.anaphora_expose_meta)
        self._plant_kb_enabled = bool(cfg.plant_kb_enabled)
        self._plant_kb_namespace = (cfg.plant_kb_namespace or "Power_plant_knowledge").strip()
        self._plant_kb_query_boost = (cfg.plant_kb_query_boost_name or "").strip()
        self._plant_kb_fallback_on_empty = bool(cfg.plant_kb_fallback_on_empty)
        self._plant_kb_history_continuation = bool(cfg.plant_kb_history_continuation)
        # 高分 FAQ 软直通：生成阶段跳过 history_messages，避免旧 assistant 答案带偏复述（默认开）
        self._faq_soft_direct_enabled = bool(cfg.faq_soft_direct_enabled)
        self._faq_soft_direct_min_score = float(cfg.faq_soft_direct_min_score)
        self._faq_soft_direct_snippet_top_n = max(1, int(cfg.faq_soft_direct_snippet_top_n))
        self._llm_context_total_tokens = max(2048, int(cfg.llm_context_total_tokens))
        self._llm_completion_slack_tokens = max(64, int(cfg.llm_completion_budget_slack_tokens))
        self._history_trim_enabled = bool(cfg.history_trim_enabled)
        self._history_trim_min_keep = max(0, int(cfg.history_trim_min_keep))
        llm_cfg = get_app_config().llm
        default_model = llm_cfg.default_model
        model_entry = llm_cfg.models.get(default_model)
        self._main_llm_max_tokens = max(64, int(getattr(model_entry, "max_tokens", 2048) or 2048))

        self._hitl_enabled = hitl_globally_enabled()
        self._nl2sql_hitl_max_retries = max(0, int(cfg.nl2sql_hitl_max_retries))

        self._graph = None
        if self._graph_enabled:
            self._graph = self._build_graph()

    def _build_graph(self):
        try:
            from langgraph.graph import END, StateGraph  # type: ignore[import-not-found]
        except Exception as exc:  # noqa: BLE001
            logger.warning("ChatbotLangGraphRunner: langgraph unavailable, fallback to sequential. err=%s", exc)
            return None

        # 图编排分三段（请保持该顺序，避免行为回退）：
        # 1) 输入预处理：模板/历史/意图
        # 2) 知识检索：引擎选择 -> 召回 -> 质量判定 -> 可选 C-RAG 重试
        # 3) 生成收敛：构建模型消息或直接澄清，最终统一 finalize
        graph = StateGraph(ChatbotGraphState)
        graph.add_node("load_prompt_template", self._node_load_prompt_template)
        graph.add_node("load_history", self._node_load_history)
        graph.add_node("intent_classify", self._node_intent_classify)
        graph.add_node("fault_case_gate", self._node_fault_case_gate)
        # 预留分支：先放节点，默认不放量（由 intent 输出标签控制）。
        graph.add_node("unsafe_guard", self._node_unsafe_guard)
        graph.add_node("handoff_human", self._node_handoff_human)
        graph.add_node("smalltalk_generate", self._node_smalltalk_generate)
        graph.add_node("select_rag_engine", self._node_select_rag_engine)
        graph.add_node("rag_scope_resolve", self._node_rag_scope_resolve)
        graph.add_node("kb_retrieve", self._node_kb_retrieve)
        graph.add_node("kb_quality_check", self._node_kb_quality_check)
        graph.add_node("kb_rewrite_query", self._node_kb_rewrite_query)
        graph.add_node("kb_build_messages", self._node_kb_build_messages)
        graph.add_node("clarify_build_response", self._node_clarify_build_response)
        graph.add_node("nl2sql_answer", self._node_nl2sql_answer)
        graph.add_node("hybrid_acquire", self._node_hybrid_acquire)
        graph.add_node("hybrid_synthesize", self._node_hybrid_synthesize)
        graph.add_node("finalize", self._node_finalize)

        # 入口固定为模板加载：
        # - 保证每轮都有一致 system prompt；
        # - 避免后续节点重复处理“模板缺失”分支。
        graph.set_entry_point("load_prompt_template")
        graph.add_edge("load_prompt_template", "load_history")
        graph.add_edge("load_history", "intent_classify")
        graph.add_edge("intent_classify", "fault_case_gate")
        # 意图路由（在故障域/相似案例门控之后，状态已含 need_similar_cases 等）：
        # - 首版仅放开 kb_qa/clarify；
        # - 其它标签（unsafe/handoff/smalltalk）先占位，不在本版本放量。
        graph.add_conditional_edges(
            "fault_case_gate",
            self._route_by_intent,
            {
                "kb_qa": "select_rag_engine",
                "data_query": "nl2sql_answer",
                "hybrid_qa": "hybrid_acquire",
                "clarify": "clarify_build_response",
                "unsafe": "unsafe_guard",
                "handoff_human": "handoff_human",
                "smalltalk": "smalltalk_generate",
            },
        )
        graph.add_edge("unsafe_guard", "finalize")
        graph.add_edge("handoff_human", "finalize")
        graph.add_edge("smalltalk_generate", "finalize")
        graph.add_edge("nl2sql_answer", "finalize")
        graph.add_edge("hybrid_acquire", "hybrid_synthesize")
        graph.add_edge("hybrid_synthesize", "finalize")
        graph.add_edge("select_rag_engine", "rag_scope_resolve")
        graph.add_edge("rag_scope_resolve", "kb_retrieve")
        graph.add_edge("kb_retrieve", "kb_quality_check")
        # 质量路由（C-RAG 核心）：
        # - retry: 低分且重试预算未耗尽
        # - clarify: 低分且预算耗尽（避免继续“硬答”）
        # - build: 质量达标，进入生成阶段
        graph.add_conditional_edges(
            "kb_quality_check",
            self._route_after_quality_check,
            {
                "retry": "kb_rewrite_query",
                "build": "kb_build_messages",
                "clarify": "clarify_build_response",
            },
        )
        graph.add_edge("kb_rewrite_query", "kb_retrieve")
        graph.add_edge("kb_build_messages", "finalize")
        graph.add_edge("clarify_build_response", "finalize")
        # 所有分支统一收敛到 finalize，再结束。
        # 好处：可统一写 status/终止原因，SSE 结束 meta 与埋点口径一致。
        graph.add_edge("finalize", END)
        checkpointer = self._build_checkpointer()
        if checkpointer is not None:
            return graph.compile(checkpointer=checkpointer)
        return graph.compile()

    def _build_checkpointer(self):
        """
        构建 LangGraph checkpoint（可选）。

        当前策略：
        - none：不启用（默认）；
        - memory：进程内 checkpoint（仅开发/测试）；
        - redis：尝试使用 redis checkpointer，依赖缺失时降级 none。
        """
        backend = self._checkpoint_backend
        if backend == "none":
            return None
        if backend == "memory":
            try:
                from langgraph.checkpoint.memory import MemorySaver  # type: ignore[import-not-found]

                logger.info("ChatbotLangGraphRunner: memory checkpoint enabled.")
                return MemorySaver()
            except Exception as exc:  # noqa: BLE001
                logger.warning("ChatbotLangGraphRunner: memory checkpointer unavailable: %s", exc)
                return None
        if backend == "redis":
            if not self._checkpoint_redis_url:
                logger.warning("ChatbotLangGraphRunner: redis checkpoint backend selected but URL missing.")
                return None
            saver = open_langgraph_redis_saver(
                self._checkpoint_redis_url,
                log_prefix="ChatbotLangGraphRunner",
            )
            if saver is None:
                return None
            logger.info(
                "ChatbotLangGraphRunner: redis checkpoint enabled namespace=%s",
                self._checkpoint_namespace,
            )
            return saver
        logger.warning("ChatbotLangGraphRunner: unknown checkpoint backend=%s, disable checkpoint.", backend)
        return None

    async def run_stream(self, req: ChatRequest) -> AsyncIterator[str]:
        """
        运行图并流式返回文本增量。

        行为约定：
        - 正常：落库 user + assistant；
        - 异常：落库 user，不落 assistant；
        - 客户端断开（由上层中断迭代）：若已产生部分文本，按配置决定是否落 partial。
        """
        async for event in self.run_stream_events(req):
            if event.get("type") == "delta":
                yield str(event.get("delta") or "")

    async def run_stream_events(
        self,
        req: ChatRequest,
        model_req: ChatRequest | None = None,
        stream_id: str | None = None,
        cancel_checker: Any | None = None,
        original_image_urls: List[str] | None = None,
    ) -> AsyncIterator[Dict[str, Any]]:
        """
        运行图并输出结构化事件。

        事件类型：
        - delta: 增量文本
        - citation: 知识引用 ``{"ref_index": n}``（SSE 映射为 ``citation_ref``）
        - finished: 完成事件（含 meta）
        """
        # state 在一次请求生命周期内共享；每个节点只增量更新自己负责字段。
        state = self._initial_state(req, model_req=model_req, original_image_urls=original_image_urls)
        start_ts = time.perf_counter()
        try:
            state = await self._run_graph(state)
            self._ensure_within_latency(start_ts)

            if state.get("pending_hitl"):
                async for ev in self._emit_hitl_events(state, req, start_ts, stream_id):
                    yield ev
                return

            if state.get("nl2sql_analysis_stream_plan"):
                async for ev in self._emit_nl2sql_analysis_stream(
                    state, req, start_ts, stream_id, cancel_checker, persist_mode="success"
                ):
                    yield ev
                return

            pre_answer = (state.get("answer_text") or "").strip()
            llm_messages = state.get("llm_messages") or []
            # 意图澄清、或仅有固定话术而无 llm_messages（如检索触发的澄清/占位分支）
            no_stream_path = (
                state.get("intent_label") == "clarify"
                or state.get("status") == "awaiting_hitl"
                or (bool(pre_answer) and not llm_messages)
            )
            if no_stream_path:
                if await self._is_cancelled(req, stream_id, cancel_checker):
                    self._persist_disconnect(state, req, "")
                    state["status"] = "aborted"
                    state["terminate_reason"] = "user_cancelled"
                    yield {"type": "finished", "meta": self._build_finished_meta(state, start_ts, stream_id)}
                    return
                answer = pre_answer
                extra = self._maybe_similar_cases_extra(state)
                state["similar_cases_appended"] = bool(extra)
                full = (answer + extra).strip()
                await self._fill_suggested_questions(state, req, full)
                self._persist_success(state, req, full, is_partial=False, terminate_reason=None)
                if answer:
                    yield {"type": "delta", "delta": answer}
                if extra:
                    yield {"type": "delta", "delta": extra}
                yield {"type": "finished", "meta": self._build_finished_meta(state, start_ts, stream_id)}
                return

            parts: List[str] = []
            cite_parser: CitationStreamParser | None = None
            if citation_stream_enabled(list(state.get("rag_citations") or [])):
                cite_parser = CitationStreamParser(
                    max_ref_index=max_citation_ref_index(list(state.get("rag_citations") or []))
                )
            requested_max = int(state.get("llm_max_tokens") or self._main_llm_max_tokens)
            safe_max = ensure_chatbot_stream_max_tokens(
                list(llm_messages),
                requested_max_tokens=requested_max,
                context_total_tokens=self._llm_context_total_tokens,
                slack_tokens=self._llm_completion_slack_tokens,
            )
            if safe_max < requested_max:
                logger.info(
                    "chatbot.stream_max_tokens adjusted requested=%s safe=%s",
                    requested_max,
                    safe_max,
                )
            stream_kw: Dict[str, Any] = {"max_tokens": safe_max}
            if self._main_llm_temperature is not None:
                stream_kw["temperature"] = float(self._main_llm_temperature)
            try:
                async for delta in self._llm.stream_chat(model=None, messages=llm_messages, **stream_kw):  # type: ignore[arg-type]
                    if await self._is_cancelled(req, stream_id, cancel_checker):
                        partial = "".join(parts).strip()
                        self._persist_disconnect(state, req, partial)
                        state["status"] = "aborted"
                        state["terminate_reason"] = "user_cancelled"
                        yield {"type": "finished", "meta": self._build_finished_meta(state, start_ts, stream_id)}
                        return
                    self._ensure_within_latency(start_ts)
                    parts.append(delta)
                    state["answer_parts"] = list(parts)
                    if cite_parser is None:
                        yield {"type": "delta", "delta": delta}
                    else:
                        for ev in cite_parser.feed(delta):
                            yield ev
                if cite_parser is not None:
                    for ev in cite_parser.flush():
                        yield ev
            except TimeoutError as exc:
                # 与 MAX_GRAPH_LATENCY_MS 同源：总耗时（图+RAG+流式）超预算。
                # 若已向前端输出过 delta，再抛给上层会触发 legacy 全量重跑，表现为「停几秒后又答一遍」且会话里可能重复 user。
                if "latency budget exceeded" not in str(exc):
                    raise
                partial = "".join(parts).strip()
                logger.warning(
                    "chatbot.stream stopped by latency budget: partial_chars=%s budget_ms=%s",
                    len(partial),
                    self._max_graph_latency_ms,
                )
                state["terminate_reason"] = "latency_budget_exceeded"
                state["similar_cases_appended"] = False
                if partial:
                    await self._fill_suggested_questions(state, req, partial)
                    self._persist_success(
                        state,
                        req,
                        partial,
                        is_partial=True,
                        terminate_reason="latency_budget_exceeded",
                    )
                else:
                    await self._fill_suggested_questions(state, req, "")
                    state["status"] = "failed"
                    self._persist_failure(state, req)
                yield {"type": "finished", "meta": self._build_finished_meta(state, start_ts, stream_id)}
                return

            answer = "".join(parts).strip()
            extra = self._maybe_similar_cases_extra(state)
            state["similar_cases_appended"] = bool(extra)
            if extra:
                yield {"type": "delta", "delta": extra}
            full_stream = (answer + extra).strip()
            await self._fill_suggested_questions(state, req, full_stream)
            self._persist_success(state, req, full_stream, is_partial=False, terminate_reason=None)
            yield {"type": "finished", "meta": self._build_finished_meta(state, start_ts, stream_id)}
        except GeneratorExit:
            # 客户端主动断开：
            # - 这是“正常中断”而非服务异常；
            # - 按配置决定是否落 partial，便于下一轮会话续接。
            partial = "".join(state.get("answer_parts") or []).strip()
            self._persist_disconnect(state, req, partial)
            state["status"] = "aborted"
            state["terminate_reason"] = "client_disconnect"
            raise
        except Exception as exc:  # noqa: BLE001
            logger.exception("ChatbotLangGraphRunner.run_stream failed: %s", exc)
            state["status"] = "failed"
            state["error"] = str(exc)
            self._persist_failure(state, req)
            raise
        finally:
            if self._ls.enabled:
                self._ls.log_run(
                    name="chatbot_langgraph_stream",
                    run_type="chain",
                    inputs={
                        "user_id": req.user_id,
                        "session_id": req.session_id,
                        "query": req.query,
                        "enable_rag": req.enable_rag,
                        "enable_context": req.enable_context,
                    },
                    outputs=self._build_finished_meta(state, start_ts, stream_id),
                    metadata={
                        "error": state.get("error"),
                        "prompt_variant": state.get("prompt_variant"),
                        "terminate_reason": state.get("terminate_reason"),
                    },
                )

    def _initial_state(
        self,
        req: ChatRequest,
        model_req: ChatRequest | None = None,
        original_image_urls: List[str] | None = None,
    ) -> ChatbotGraphState:
        original_urls = [u for u in (original_image_urls or []) if isinstance(u, str) and u.strip()]
        eff_req = model_req or req
        return {
            "user_id": req.user_id,
            "session_id": req.session_id,
            "query": eff_req.query,
            "original_image_urls": original_urls,
            "image_urls": [u for u in req.image_urls if isinstance(u, str) and u.strip()],
            "enable_rag": bool(req.enable_rag),
            "enable_context": bool(req.enable_context),
            "enable_nl2sql_route": bool(req.enable_nl2sql_route) and self._nl2sql_route_enabled_cfg,
            "client_prompt_version": (str(req.prompt_version).strip() if req.prompt_version else None),
            "history_limit": self._history_limit,
            "context_snippets": [],
            "rag_citations": [],
            "retrieval_score": 0.0,
            "retrieval_attempts": 0,
            "rag_namespace": None,
            "rag_scope_reason": "",
            "rag_scope_fallback": False,
            "rag_query_boost": None,
            "intent_label": "kb_qa",
            "intent_confidence": 0.0,
            "intent_reason": "",
            "status": "started",
            "used_rag": False,
            "used_nl2sql": False,
            "nl2sql_sql": "",
            "nl2sql_failed": False,
            "nl2sql_error_code": None,
            "nl2sql_analysis": None,
            "nl2sql_analysis_stream_plan": None,
            "hybrid_degraded": "",
            "suggested_questions": [],
            "error": None,
            "answer_parts": [],
            "answer_text": "",
            "need_similar_cases": False,
            "case_rag_query": "",
            "fault_detect_sources": [],
            "fault_detect_confidence": 0.0,
            "enable_fault_vision": req.enable_fault_vision,
            "similar_cases_appended": False,
            "confirmed_route": "",
            "pending_hitl": False,
            "hitl_kind": "",
            "hitl_original_query": "",
            "hitl_resume_action": "",
            "hitl_choice_label": "",
            "intent_hitl_round": 0,
            "disambiguation_analysis": "",
            "disambiguation_options": [],
            "disambiguation_source": "",
            "human_interactions": [],
            "nl2sql_retry_count": 0,
            "nl2sql_skip_cache": False,
            "nl2sql_retry_hint": "",
            "nl2sql_fail_reason": "",
        }

    @staticmethod
    def _merge_graph_state(base: ChatbotGraphState, patch: ChatbotGraphState) -> ChatbotGraphState:
        merged = dict(base)
        merged.update(patch)
        return merged  # type: ignore[return-value]

    def _should_append_similar_cases(self, state: ChatbotGraphState) -> bool:
        if state.get("intent_label") == "data_query":
            return False
        if not state.get("need_similar_cases"):
            return False
        if state.get("intent_label") == "clarify":
            return False
        if state.get("terminate_reason") == "need_clarify":
            return False
        return True

    def _maybe_similar_cases_extra(self, state: ChatbotGraphState) -> str:
        if not self._similar_case_enabled or not self._should_append_similar_cases(state):
            return ""
        q = (state.get("case_rag_query") or state.get("query") or "").strip()
        snippets = retrieve_similar_case_snippets(
            self._hybrid_rag,
            query=q,
            namespace=self._similar_case_namespace,
            top_k=self._similar_case_top_k,
        )
        return format_similar_cases_block(snippets)

    def _ensure_within_latency(self, start_ts: float) -> None:
        elapsed_ms = int((time.perf_counter() - start_ts) * 1000)
        if elapsed_ms > self._max_graph_latency_ms:
            raise TimeoutError(
                f"chatbot graph latency budget exceeded: elapsed_ms={elapsed_ms}, budget_ms={self._max_graph_latency_ms}"
            )

    async def _run_graph(self, state: ChatbotGraphState, *, resume: bool = False) -> ChatbotGraphState:
        # HITL 续跑与窄触发确认走顺序执行，与 LangGraph 编译图语义对齐且可中断。
        if self._hitl_enabled or resume or self._graph is None:
            return await self._run_graph_sequential(state, resume=resume)
        return await self._graph.ainvoke(state)  # type: ignore[union-attr]

    async def _run_graph_sequential(self, state: ChatbotGraphState, *, resume: bool = False) -> ChatbotGraphState:
        if resume:
            return await self._run_graph_resume(state)
        m = self._merge_graph_state
        state = m(state, await self._node_load_prompt_template(state))
        state = m(state, await self._node_load_history(state))
        state = m(state, await self._node_intent_classify(state))
        state = m(state, await self._node_fault_case_gate(state))
        if should_trigger_intent_hitl(state):
            state = m(state, prepare_intent_hitl_patch(state))
            return state
        route = self._route_by_intent(state)
        return await self._execute_route(state, route)

    async def _run_graph_resume(self, state: ChatbotGraphState) -> ChatbotGraphState:
        m = self._merge_graph_state
        kind = str(state.get("hitl_kind") or "")
        action = str(state.get("hitl_resume_action") or "")
        if kind == HITL_KIND_INTENT_DISAMBIGUATION:
            # 点选消歧选项后：已写入 confirmed_route，直接路由，不再意图 HITL
            route = self._route_by_intent(state)
            return await self._execute_route(state, route)
        if kind == "intent_route_confirm":
            if action == ACTION_ROUTE_CLARIFY:
                state = m(state, await self._node_intent_classify(state))
                state = m(state, await self._node_fault_case_gate(state))
                if should_trigger_intent_hitl(state):
                    if intent_disambiguation_enabled():
                        disamb = await generate_intent_disambiguation(
                            query=str(state.get("query") or ""),
                            intent_label=str(state.get("intent_label") or ""),
                            intent_confidence=float(state.get("intent_confidence") or 0.0),
                            intent_reason=str(state.get("intent_reason") or ""),
                            history_summary=str(state.get("intent_history_summary") or ""),
                            llm_client=self._llm,
                            user_id=str(state.get("user_id") or "") or None,
                        )
                        state = m(
                            state,
                            prepare_intent_disambiguation_hitl_patch(
                                state,
                                analysis=str(disamb.get("analysis") or ""),
                                options=list(disamb.get("options") or []),
                                source=str(disamb.get("source") or "unknown"),
                            ),
                        )
                        return state
                    # 消歧关闭：已走过一轮意图 HITL，不再重复三路由，默认走知识库
                    state = dict(state)
                    state["confirmed_route"] = "kb_qa"
                    state["intent_label"] = "kb_qa"
                    state["intent_hitl_round"] = max(
                        2, int(state.get("intent_hitl_round") or 0)
                    )
                    return await self._execute_route(state, "kb_qa")
                route = self._route_by_intent(state)
                return await self._execute_route(state, route)
            route = self._route_by_intent(state)
            return await self._execute_route(state, route)
        if kind == "nl2sql_gen_failed":
            if action == "nl2sql_retry":
                state = m(state, await self._node_nl2sql_answer(state))
                if state.get("pending_hitl"):
                    return state
                return m(state, await self._node_finalize(state))
            if action == "fallback_kb_qa":
                state = dict(state)
                state["used_nl2sql"] = False
                return await self._run_kb_path(state)  # type: ignore[arg-type]
        return m(state, await self._node_finalize(state))

    async def _execute_route(self, state: ChatbotGraphState, route: str) -> ChatbotGraphState:
        m = self._merge_graph_state
        if route == "clarify":
            state = m(state, await self._node_clarify_build_response(state))
            return m(state, await self._node_finalize(state))
        if route == "data_query":
            state = m(state, await self._node_nl2sql_answer(state))
            if state.get("pending_hitl"):
                return state
            return m(state, await self._node_finalize(state))
        if route == "hybrid_qa":
            state = m(state, await self._node_hybrid_acquire(state))
            state = m(state, await self._node_hybrid_synthesize(state))
            return m(state, await self._node_finalize(state))
        if route in {"unsafe", "handoff_human", "smalltalk"}:
            node_map = {
                "unsafe": self._node_unsafe_guard,
                "handoff_human": self._node_handoff_human,
                "smalltalk": self._node_smalltalk_generate,
            }
            state = m(state, await node_map[route](state))
            return m(state, await self._node_finalize(state))
        return await self._run_kb_path(state)

    async def _run_kb_path(self, state: ChatbotGraphState) -> ChatbotGraphState:
        m = self._merge_graph_state
        state = m(state, await self._node_select_rag_engine(state))
        state = m(state, await self._node_rag_scope_resolve(state))
        while True:
            state = m(state, await self._node_kb_retrieve(state))
            state = m(state, await self._node_kb_quality_check(state))
            route = self._route_after_quality_check(state)
            if route == "retry":
                state = m(state, await self._node_kb_rewrite_query(state))
                continue
            if route == "clarify":
                state = m(state, await self._node_clarify_build_response(state))
            else:
                state = m(state, await self._node_kb_build_messages(state))
            return m(state, await self._node_finalize(state))

    async def _node_load_prompt_template(self, state: ChatbotGraphState) -> ChatbotGraphState:
        # 模板策略入口：
        # - 继续复用 PromptTemplateRegistry，保持与历史模板策略兼容；
        # - 若模板缺失，使用固定兜底 system_prompt，防止下游节点判空分叉。
        client_ver = state.get("client_prompt_version")
        if client_ver:
            tpl = self._prompts.get_template(scene="chatbot", user_id=state["user_id"], version=str(client_ver))
        else:
            tpl = self._prompts.get_template(
                scene="chatbot",
                user_id=state["user_id"],
                version=None,
                default_version=self._default_prompt_version,
            )
        out: ChatbotGraphState = {}
        if tpl and tpl.content:
            out["system_prompt"] = tpl.content
            out["prompt_template_id"] = str(getattr(tpl, "id", "") or "")
            out["prompt_version"] = str(getattr(tpl, "version", "") or "")
            out["prompt_variant"] = str(getattr(tpl, "name", "") or "")
        else:
            out["system_prompt"] = "你是一个专业的中文智能客服助手。"
            out["prompt_template_id"] = None
            out["prompt_version"] = None
            out["prompt_variant"] = None
        return out

    async def _node_load_history(self, state: ChatbotGraphState) -> ChatbotGraphState:
        # 关闭上下文时跳过历史读取，保持“每轮独立”语义。
        # 注意：是否写入本轮消息由持久化节点决定，这里只控制“读历史”。
        if not state.get("enable_context", True):
            return {"status": "started"}
        history = self._conv.get_recent_history(
            state["user_id"],
            state["session_id"],
            limit=int(state.get("history_limit", self._history_limit)),
        )
        return {"history_messages": history}

    async def _node_intent_classify(self, state: ChatbotGraphState) -> ChatbotGraphState:
        if not self._intent_enabled:
            return {"intent_label": "kb_qa", "intent_confidence": 1.0, "intent_reason": "intent_disabled", "status": "intented"}
        q = (state.get("query") or "").strip()
        imgs = [u for u in (state.get("image_urls") or []) if isinstance(u, str) and u.strip()]
        hist = state.get("history_messages") if state.get("enable_context", True) else None
        ir = await classify_chatbot_intent_async(
            q,
            enable_nl2sql_route=bool(state.get("enable_nl2sql_route")),
            image_urls=imgs,
            history_messages=list(hist) if hist else None,
        )
        label, reason, conf = ir.intent_label, ir.intent_reason, ir.intent_confidence
        ctx_summary, prev_task = ir.history_summary, ir.prev_task_type
        if label not in self._intent_output_labels:
            reason = f"label_not_enabled:{label}|{reason}"
            label = "kb_qa"
            conf = min(conf, 0.6)
        logger.info(
            "chatbot.intent decision backend=%s label=%s reason=%s conf=%.3f enable_nl2sql=%s has_images=%s "
            "query_len=%s prev_task=%s ctx_chars=%s",
            get_app_config().chatbot.intent_backend,
            label,
            reason,
            conf,
            bool(state.get("enable_nl2sql_route")),
            bool(imgs),
            len(q),
            prev_task,
            len(ctx_summary),
        )
        return {
            "intent_label": label,
            "intent_reason": reason,
            "intent_confidence": conf,
            "intent_history_summary": ctx_summary[:1200] if ctx_summary else "",
            "intent_prev_task_type": prev_task,
            "status": "intented",
        }

    async def _node_nl2sql_answer(self, state: ChatbotGraphState) -> ChatbotGraphState:
        """结构化问数：NL2SQL + 结果自然语言化（会话写入由 Runner 层统一 persist）。

        有数据且开启收紧分析时：仅完成查数并挂上 stream_plan，正文由 run_stream_events 流式推送。
        """
        q = str(state.get("query") or "")
        outcome = await run_chatbot_nl2sql_query(
            self._nl2sql,
            self._llm,
            user_id=state["user_id"],
            session_id=state["session_id"],
            question=q,
            skip_sql_cache=bool(state.get("nl2sql_skip_cache")),
            nl2sql_retry_hint=(state.get("nl2sql_retry_hint") or None),
            defer_analysis_stream=True,
        )
        stream_plan = outcome.analysis_stream_plan
        patch: ChatbotGraphState = {
            "answer_text": outcome.answer_text,
            "used_nl2sql": True,
            "nl2sql_sql": outcome.nl2sql_sql or "",
            "nl2sql_analysis": outcome.nl2sql_analysis,
            "nl2sql_analysis_stream_plan": stream_plan.to_state_dict() if stream_plan else None,
            "used_rag": False,
            "llm_messages": [],
            "context_snippets": [],
        }
        if outcome.gen_failed:
            merged = {**state, **patch}
            if should_trigger_nl2sql_hitl(merged, gen_failed=True):
                patch.update(
                    prepare_nl2sql_hitl_patch(merged, fail_reason=outcome.gen_fail_reason)
                )
                return patch
            patch["answer_text"] = (
                "未能生成有效的 SQL 查询。请换一种方式描述要查的台账或记录条件，或改用知识库问答。"
            )
            patch["nl2sql_failed"] = True
            patch["terminate_reason"] = outcome.terminate_reason or "nl2sql_gen_failed"
            return patch
        if outcome.nl2sql_failed:
            patch["nl2sql_failed"] = True
            patch["nl2sql_error_code"] = outcome.nl2sql_error_code
            patch["terminate_reason"] = outcome.terminate_reason
        return patch

    async def _node_hybrid_acquire(self, state: ChatbotGraphState) -> ChatbotGraphState:
        """并行 NL2SQL + RAG 臂，为 Hybrid 综合准备证据；RAG 侧简化（不做 C-RAG 重试）。"""
        q = str(state.get("query") or "")

        async def _rag_arm() -> ChatbotGraphState:
            m = self._merge_graph_state
            s = m(state, await self._node_select_rag_engine(state))
            s = m(s, await self._node_rag_scope_resolve(s))
            s = m(s, await self._node_kb_retrieve(s))
            return {
                "used_rag": bool(s.get("used_rag")),
                "context_snippets": list(s.get("context_snippets") or []),
                "rag_citations": list(s.get("rag_citations") or []),
                "retrieval_score": float(s.get("retrieval_score") or 0.0),
                "retrieval_attempts": int(s.get("retrieval_attempts") or 0),
                "rag_engine": s.get("rag_engine"),
                "rag_namespace": s.get("rag_namespace"),
                "rag_scope_reason": str(s.get("rag_scope_reason") or ""),
                "rag_scope_fallback": bool(s.get("rag_scope_fallback")),
                "rag_query_boost": s.get("rag_query_boost"),
                "anaphora_type": s.get("anaphora_type"),
                "anaphora_rule_type": s.get("anaphora_rule_type"),
                "anaphora_confidence": s.get("anaphora_confidence"),
                "anaphora_score_gap": s.get("anaphora_score_gap"),
                "anaphora_source": s.get("anaphora_source"),
                "anaphora_slot_bullets": list(s.get("anaphora_slot_bullets") or []),
                "status": s.get("status"),
            }

        outcome, rag_patch = await asyncio.gather(
            run_chatbot_nl2sql_query(
                self._nl2sql,
                self._llm,
                user_id=state["user_id"],
                session_id=state["session_id"],
                question=q,
                defer_analysis_stream=False,
            ),
            _rag_arm(),
        )

        snippets = list(rag_patch.get("context_snippets") or [])
        rag_ok = len(snippets) > 0
        enable_rag = bool(state.get("enable_rag", True))
        nl2sql_fail = bool(outcome.gen_failed or outcome.nl2sql_failed)
        nl2sql_ok = not nl2sql_fail

        if nl2sql_ok and rag_ok:
            hybrid_degraded = ""
        elif nl2sql_fail and rag_ok:
            hybrid_degraded = "nl2sql"
        elif nl2sql_ok and enable_rag and not rag_ok:
            hybrid_degraded = "rag"
        else:
            hybrid_degraded = "both"

        patch: ChatbotGraphState = {
            "used_nl2sql": True,
            "nl2sql_sql": outcome.nl2sql_sql or "",
            "nl2sql_analysis": outcome.nl2sql_analysis,
            "nl2sql_analysis_stream_plan": None,
            "hybrid_degraded": hybrid_degraded,
            **rag_patch,
        }
        if outcome.gen_failed:
            patch["nl2sql_failed"] = True
            patch["terminate_reason"] = outcome.terminate_reason or "nl2sql_gen_failed"
        elif outcome.nl2sql_failed:
            patch["nl2sql_failed"] = True
            patch["nl2sql_error_code"] = outcome.nl2sql_error_code
            patch["terminate_reason"] = outcome.terminate_reason

        if nl2sql_ok and not rag_ok:
            patch["answer_text"] = outcome.answer_text
        elif nl2sql_fail and rag_ok:
            patch["answer_text"] = ""
        elif nl2sql_ok and rag_ok:
            patch["answer_text"] = outcome.answer_text
        elif not nl2sql_ok and not rag_ok:
            patch["answer_text"] = (
                "抱歉，当前既未能完成数据查询，也未能从知识库检索到可用内容，请稍后再试或换一种问法。"
            )
            patch["terminate_reason"] = patch.get("terminate_reason") or "hybrid_both_failed"
        return patch

    async def _node_hybrid_synthesize(self, state: ChatbotGraphState) -> ChatbotGraphState:
        snippets = list(state.get("context_snippets") or [])
        rag_ok = len(snippets) > 0 and bool(state.get("used_rag"))
        nl2sql_ok = bool(state.get("used_nl2sql")) and not bool(state.get("nl2sql_failed"))
        answer_text = (state.get("answer_text") or "").strip()

        if nl2sql_ok and not rag_ok:
            return {"llm_messages": [], "used_rag": False, "answer_text": answer_text}

        if rag_ok and not nl2sql_ok:
            return await self._node_kb_build_messages(state)

        if nl2sql_ok and rag_ok:
            nl2sql_block = answer_text
            if len(nl2sql_block) > 4000:
                nl2sql_block = nl2sql_block[:3990] + "\n…(truncated)"
            rag_block = format_rag_snippets_system_block(snippets)
            synth_block = (
                "【综合回答要求】请结合下方「查数结果」与「知识库摘录」作答："
                "数值与列表以查数结果为准，机理/标准/处置以知识库为准，禁止臆造表中不存在的数据。\n"
                f"【查数结果】\n{nl2sql_block}\n"
                f"【知识库摘录】\n{rag_block}"
            )
            system_chunks: List[str] = []
            sp = str(state.get("system_prompt") or "").strip()
            if sp:
                system_chunks.append(sp)
            system_chunks.append(synth_block)
            query = str(state.get("query") or "")
            image_urls = [u for u in (state.get("image_urls") or []) if isinstance(u, str) and u.strip()]
            hist = list(state.get("history_messages") or []) if state.get("enable_context", True) else []

            def build_messages(h: List[Dict[str, Any]], _snippets: List[str]) -> List[Dict[str, Any]]:
                return assemble_chatbot_llm_messages(
                    system_chunks=system_chunks,
                    history=h,
                    query=query,
                    image_urls=image_urls,
                )

            build_result = trim_history_and_build_chatbot_messages(
                hist,
                build_messages=build_messages,
                rag_snippets=[],
                context_total_tokens=self._llm_context_total_tokens,
                requested_max_tokens=self._main_llm_max_tokens,
                slack_tokens=self._llm_completion_slack_tokens,
                trim_enabled=self._history_trim_enabled and bool(hist),
                min_keep=self._history_trim_min_keep,
            )
            return {
                "llm_messages": build_result.messages,
                "llm_max_tokens": build_result.max_tokens,
                "history_trim_dropped": build_result.history_dropped,
                "used_rag": True,
                "used_nl2sql": True,
                "answer_text": "",
            }

        if not (state.get("answer_text") or "").strip():
            return {
                "answer_text": (
                    "抱歉，当前既未能完成数据查询，也未能从知识库检索到可用内容，请稍后再试或换一种问法。"
                ),
                "llm_messages": [],
                "terminate_reason": state.get("terminate_reason") or "hybrid_both_failed",
            }
        return {"llm_messages": [], "used_rag": False}

    async def _emit_nl2sql_analysis_stream(
        self,
        state: ChatbotGraphState,
        req: ChatRequest,
        start_ts: float,
        stream_id: str | None,
        cancel_checker: Any | None = None,
        *,
        persist_mode: str = "success",
    ) -> AsyncIterator[Dict[str, Any]]:
        """NL2SQL 收紧分析：stream_chat 推 delta；失败/空输出回退 Markdown 表。"""
        plan = Nl2sqlAnalysisStreamPlan.from_state_dict(state.get("nl2sql_analysis_stream_plan"))
        state["nl2sql_analysis_stream_plan"] = None
        if plan is None:
            answer = (state.get("answer_text") or "").strip()
            if answer:
                yield {"type": "delta", "delta": answer}
            if persist_mode == "success":
                await self._fill_suggested_questions(state, req, answer)
                self._persist_success(state, req, answer, is_partial=False, terminate_reason=None)
            yield {"type": "finished", "meta": self._build_finished_meta(state, start_ts, stream_id)}
            return

        parts: List[str] = []
        try:
            async for delta in iter_analysis_llm_deltas(
                self._llm,
                system=plan.system,
                user_content=plan.user_content,
            ):
                if await self._is_cancelled(req, stream_id, cancel_checker):
                    partial = strip_nl2sql_analysis_section_headings("".join(parts).strip())
                    if persist_mode == "success":
                        self._persist_disconnect(state, req, partial)
                    else:
                        # resume：用户选择已落库，勿再 append user
                        self._persist_resume_partial(state, req, partial)
                    state["status"] = "aborted"
                    state["terminate_reason"] = "user_cancelled"
                    yield {"type": "finished", "meta": self._build_finished_meta(state, start_ts, stream_id)}
                    return
                parts.append(delta)
                yield {"type": "delta", "delta": delta}
        except Exception:
            logger.warning("chatbot.nl2sql_analysis stream failed", exc_info=True)
            parts = []

        streamed = "".join(parts).strip()
        if streamed:
            finalized = finalize_streamed_nl2sql_analysis(plan, streamed)
            answer = finalized.answer_text
            state["answer_text"] = answer
            state["nl2sql_analysis"] = finalized.analysis_meta
        else:
            # 流式失败/空输出：回退 Markdown 表并一次性下发
            finalized = finalize_streamed_nl2sql_analysis(plan, "")
            answer = finalized.answer_text
            state["answer_text"] = answer
            state["nl2sql_analysis"] = finalized.analysis_meta
            if answer:
                yield {"type": "delta", "delta": answer}

        extra = self._maybe_similar_cases_extra(state)
        state["similar_cases_appended"] = bool(extra)
        full = (answer + extra).strip()
        if persist_mode == "success":
            await self._fill_suggested_questions(state, req, full)
            self._persist_success(state, req, full, is_partial=False, terminate_reason=None)
        else:
            # resume 路径：自行 append assistant
            await self._fill_suggested_questions(state, req, full)
            if full:
                rc_list = state.get("rag_citations")
                rag_kw = [x for x in rc_list if isinstance(x, dict)] if isinstance(rc_list, list) else None
                self._conv.append_assistant_message(req.user_id, req.session_id, full, rag_citations=rag_kw)
            state["status"] = "answered"
        if extra:
            yield {"type": "delta", "delta": extra}
        yield {"type": "finished", "meta": self._build_finished_meta(state, start_ts, stream_id)}

    async def _node_fault_case_gate(self, state: ChatbotGraphState) -> ChatbotGraphState:
        """锅炉/管材故障域判定 + 是否在本轮末尾追加相似案例（检索在 Runner 层执行）。"""
        inp = FaultCaseGateInput(
            similar_case_enabled=self._similar_case_enabled,
            fault_detect_enabled=self._fault_detect_enabled,
            fault_vision_enabled=self._fault_vision_enabled,
            fault_detect_mode=self._fault_detect_mode,
            fault_min_confidence=self._fault_min_confidence,
            intent_label=str(state.get("intent_label") or "kb_qa"),
            query=str(state.get("query") or ""),
            image_urls=[u for u in (state.get("image_urls") or []) if isinstance(u, str) and u.strip()],
            enable_fault_vision=state.get("enable_fault_vision"),
        )
        res = await run_fault_case_gate_decision(self._llm, inp)
        return {
            "need_similar_cases": res.need_similar_cases,
            "case_rag_query": res.case_rag_query if res.need_similar_cases else "",
            "fault_detect_sources": res.fault_detect_sources,
            "fault_detect_confidence": res.fault_detect_confidence,
        }

    async def _node_unsafe_guard(self, state: ChatbotGraphState) -> ChatbotGraphState:
        return {
            "answer_text": "当前问题涉及安全策略，暂不支持直接回答。请联系人工客服进一步处理。",
            "status": "answered",
            "terminate_reason": "unsafe_guard",
        }

    async def _node_handoff_human(self, state: ChatbotGraphState) -> ChatbotGraphState:
        return {
            "answer_text": "该问题建议转人工处理。请提供联系方式与问题详情，我们将尽快协助你。",
            "status": "answered",
            "terminate_reason": "handoff_human",
        }

    async def _node_smalltalk_generate(self, state: ChatbotGraphState) -> ChatbotGraphState:
        return {
            "answer_text": "你好，我在这里。你可以告诉我你想咨询的具体业务问题，我会尽力帮你解决。",
            "status": "answered",
            "terminate_reason": "smalltalk",
        }

    async def _node_select_rag_engine(self, state: ChatbotGraphState) -> ChatbotGraphState:
        # 防御性兜底：
        # 配置值异常时强制回落 hybrid，避免因配置错误导致全链路不可用。
        engine = self._rag_mode if self._rag_mode in {"agentic", "hybrid"} else "hybrid"
        return {"rag_engine": engine}

    async def _node_rag_scope_resolve(self, state: ChatbotGraphState) -> ChatbotGraphState:
        """
        解析主 RAG namespace（仅 kb_qa 路径进入本节点）。
        结果写入 state；C-RAG 重试走 kb_rewrite_query → kb_retrieve，不再经过本节点，故 namespace 保持不变。
        """
        if state.get("rag_scope_reason"):
            return {}
        query = str(state.get("query") or "")
        hist = list(state.get("history_messages") or []) if state.get("enable_context", True) else []
        scope = resolve_rag_namespace(
            query,
            enabled=self._plant_kb_enabled,
            plant_kb_namespace=self._plant_kb_namespace,
            history_messages=hist,
            enable_context=bool(state.get("enable_context", True)),
            history_continuation=self._plant_kb_history_continuation,
            query_boost_name=self._plant_kb_query_boost or None,
        )
        logger.info(
            "chatbot.rag_scope namespace=%s reason=%s query_len=%s",
            scope.rag_namespace,
            scope.rag_scope_reason,
            len(query),
        )
        return {
            "rag_namespace": scope.rag_namespace,
            "rag_scope_reason": scope.rag_scope_reason,
            "rag_scope_fallback": False,
            "rag_query_boost": scope.query_boost,
        }

    async def _retrieve_kb_payload(
        self,
        rag_query: str,
        *,
        engine: str,
        rag_namespace: str | None,
        user_id: str | None = None,
        session_id: str | None = None,
    ) -> tuple[List[str], List[Dict[str, Any]], str]:
        """执行主 RAG 召回；返回 snippets、citations、实际使用的 engine。"""
        snippets: List[str] = []
        citations: List[Dict[str, Any]] = []
        graph_active = bool(
            get_app_config().rag.graph.enabled and getattr(self._hybrid_rag, "_graph_query", None) is not None
        )
        effective_engine = engine
        try:
            if engine == "agentic":
                ctx = RAGContext(
                    user_id=user_id,
                    session_id=session_id,
                    scene="chatbot",
                )
                res = await self._agentic_rag.retrieve(
                    query=rag_query,
                    ctx=ctx,
                    mode=RAGMode.AGENTIC,
                    namespace=rag_namespace,
                )
                chunks = list(res.chunks) if res.chunks else self._rag.retrieve_chunks(
                    rag_query, scene="chatbot", namespace=rag_namespace
                )
                snippets, citations = chunks_to_rag_context(chunks)
            elif not graph_active:
                chunks = self._rag.retrieve_chunks(rag_query, scene="chatbot", namespace=rag_namespace)
                snippets, citations = chunks_to_rag_context(chunks)
            else:
                chunks = self._rag.retrieve_chunks(rag_query, scene="chatbot", namespace=rag_namespace)
                snippets, citations = chunks_to_rag_context(chunks)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "kb_retrieve failed on engine=%s ns=%s fallback=%s err=%s",
                engine,
                rag_namespace,
                self._rag_fallback,
                exc,
            )
            if engine != self._rag_fallback and self._rag_fallback == "hybrid":
                effective_engine = "hybrid"
                graph_active = bool(
                    get_app_config().rag.graph.enabled
                    and getattr(self._hybrid_rag, "_graph_query", None) is not None
                )
                if not graph_active:
                    chunks = self._rag.retrieve_chunks(rag_query, scene="chatbot", namespace=rag_namespace)
                    snippets, citations = chunks_to_rag_context(chunks)
                else:
                    chunks = self._rag.retrieve_chunks(rag_query, scene="chatbot", namespace=rag_namespace)
                    snippets, citations = chunks_to_rag_context(chunks)
        return snippets, citations, effective_engine

    async def _node_kb_retrieve(self, state: ChatbotGraphState) -> ChatbotGraphState:
        query = str(state.get("query") or "")
        hist = list(state.get("history_messages") or [])
        enable_ctx = bool(state.get("enable_context", True))

        slot_bullets: List[str] = []
        if self._anaphora_slots_enabled:
            slots = get_anaphora_slots(state["user_id"], state["session_id"])
            slot_bullets = slot_bullets_list(slots)

        rule = classify_anaphora_rules(
            query,
            hist,
            enable_context=enable_ctx,
            config_path=self._anaphora_config_path,
        )
        final_type, src = rule.anaphora_type, "rule"
        if self._anaphora_llm_gate:
            final_type, src = await maybe_apply_coref_llm(
                self._llm,
                user_id=state["user_id"],
                session_id=state["session_id"],
                query=query,
                history_messages=hist,
                rule=rule,
                enable_context=enable_ctx,
                llm_gate_enabled=True,
                config_path=self._anaphora_config_path,
                model_name=self._anaphora_llm_model,
                timeout_sec=self._anaphora_llm_timeout,
            )

        rag_query, _, eff_type = build_retrieval_query_with_anaphora(
            query,
            hist,
            enable_context=enable_ctx,
            fusion_enabled=self._anaphora_retrieval_fusion,
            fusion_max_chars=self._anaphora_fusion_max_chars,
            config_path=self._anaphora_config_path,
            anaphora_type=final_type,
            rule_result=rule,
        )
        anaphora_patch: ChatbotGraphState = {
            "anaphora_type": eff_type,
            "anaphora_rule_type": rule.anaphora_type,
            "anaphora_confidence": float(rule.confidence),
            "anaphora_score_gap": float(rule.score_gap),
            "anaphora_source": src,
            "anaphora_slot_bullets": list(slot_bullets),
        }
        if rag_query != query:
            logger.info(
                "chatbot.kb_retrieve retrieval_query_augmented anaphora=%s eff=%s src=%s query_len=%s rag_q_len=%s",
                rule.anaphora_type,
                eff_type,
                src,
                len(query),
                len(rag_query),
            )

        rag_namespace = state.get("rag_namespace")
        if rag_namespace:
            rag_query = augment_retrieval_query_for_plant_kb(
                rag_query,
                query_boost=state.get("rag_query_boost"),
            )

        if not state.get("enable_rag", True):
            return {
                "context_snippets": [],
                "used_rag": False,
                "retrieval_score": 0.0,
                "rag_citations": [],
                **anaphora_patch,
            }

        attempts = int(state.get("retrieval_attempts", 0)) + 1
        engine = str(state.get("rag_engine") or "hybrid")
        snippets, citations, engine = await self._retrieve_kb_payload(
            rag_query,
            engine=engine,
            rag_namespace=rag_namespace,
            user_id=state.get("user_id"),
            session_id=state.get("session_id"),
        )
        scope_fallback = False
        if (
            rag_namespace
            and self._plant_kb_fallback_on_empty
            and attempts == 1
            and not snippets
        ):
            logger.info(
                "chatbot.kb_retrieve plant_kb empty, fallback to all namespaces ns=%s",
                rag_namespace,
            )
            snippets, citations, engine = await self._retrieve_kb_payload(
                rag_query,
                engine=engine,
                rag_namespace=None,
                user_id=state.get("user_id"),
                session_id=state.get("session_id"),
            )
            scope_fallback = True

        # 轻量质量分（首版）：
        # 命中条数越多分越高。后续可以替换为“分数+覆盖率”混合评分，
        # 但请保持 retrieval_score 的 0~1 语义，避免路由阈值配置失效。
        score = min(1.0, float(len(snippets)) / 6.0) if snippets else 0.0
        return {
            "context_snippets": snippets,
            "rag_citations": citations,
            "used_rag": len(snippets) > 0,
            "retrieval_attempts": attempts,
            "retrieval_score": score,
            "rag_engine": engine,
            "rag_scope_fallback": scope_fallback or bool(state.get("rag_scope_fallback")),
            "status": "retrieved",
            **anaphora_patch,
        }

    async def _node_kb_quality_check(self, state: ChatbotGraphState) -> ChatbotGraphState:
        # 当前节点不改写 state，质量判定在 route_after_quality_check 中执行。
        # 保留节点是为了未来扩展（如：证据一致性、冲突检测、合规打分）。
        return {}

    async def _node_kb_rewrite_query(self, state: ChatbotGraphState) -> ChatbotGraphState:
        # C-RAG 查询改写：
        # 首版使用规则补强词（低风险、可解释），并限制最大长度，防止 prompt 膨胀。
        q = str(state.get("query") or "").strip()
        rewritten = f"{q} 具体流程 条件 限制 注意事项"
        rewritten = rewritten[: self._rewrite_max_len]
        return {"query": rewritten}

    async def _node_kb_build_messages(self, state: ChatbotGraphState) -> ChatbotGraphState:
        # 统一 messages 构建顺序（请勿随意调整）：
        # 单条合并 system（模板 + RAG + 历史中的 system）-> 其余历史 -> 当前 user（文本/多模态）
        # 说明：Qwen 等 chat_template 仅允许首条为 system，连续两条 role=system 会报
        # TemplateError: System message must be at the beginning.
        #
        # 高分 FAQ 软直通（CHATBOT_FAQ_SOFT_DIRECT_*）：满足条件时不注入 history_messages，
        # 仅影响生成上下文；检索与 rag_citations 已在 kb_retrieve 完成。
        faq_decision = evaluate_faq_soft_direct(
            enabled=self._faq_soft_direct_enabled,
            min_score=self._faq_soft_direct_min_score,
            enable_rag=bool(state.get("enable_rag", True)),
            intent_label=str(state.get("intent_label") or "kb_qa"),
            anaphora_type=str(state.get("anaphora_type") or "none"),
            anaphora_rule_type=str(state.get("anaphora_rule_type") or "none"),
            query=str(state.get("query") or ""),
            rag_citations=list(state.get("rag_citations") or []),
            context_snippets=list(state.get("context_snippets") or []),
        )
        if faq_decision.active:
            top_cite = (list(state.get("rag_citations") or [{}])[0] or {}) if state.get("rag_citations") else {}
            logger.info(
                "chatbot.faq_soft_direct active reason=%s query_len=%s top_rerank_score=%s top_score=%s",
                faq_decision.reason,
                len(str(state.get("query") or "")),
                top_cite.get("rerank_score"),
                top_cite.get("score"),
            )

        system_chunks: List[str] = []
        sp = str(state.get("system_prompt") or "").strip()
        if sp:
            system_chunks.append(sp)
        anchor_block_out = ""
        snippets_for_llm = snippets_for_llm_generation(
            state.get("context_snippets") or [],
            soft_direct=faq_decision.active,
            snippet_top_n=self._faq_soft_direct_snippet_top_n,
        )
        # 软直通：不注入 history_messages；否则保持原有多轮上下文
        history_for_llm: List[Dict[str, Any]] = (
            [] if faq_decision.active else list(state.get("history_messages") or [])
        )
        query = str(state.get("query") or "")
        image_urls = [u for u in (state.get("image_urls") or []) if isinstance(u, str) and u.strip()]
        anaphora_type = str(state.get("anaphora_type") or "none")
        slot_bullets = state.get("anaphora_slot_bullets") or []

        def build_messages(hist: List[Dict[str, Any]], snippets: List[str]) -> List[Dict[str, Any]]:
            chunks = list(system_chunks)
            if self._anaphora_anchor_enabled and not faq_decision.active:
                anchor = build_dialogue_anchor_block(
                    hist,
                    query,
                    anaphora_type,
                    config_path=self._anaphora_config_path,
                    max_chars=self._anaphora_anchor_max_chars,
                    slot_bullets=slot_bullets,
                )
                if anchor:
                    chunks.append(anchor)
            if snippets:
                chunks.append(
                    format_rag_snippets_for_generation(
                        snippets,
                        soft_direct=faq_decision.active,
                        base_formatter=format_rag_snippets_system_block,
                    )
                )
            return assemble_chatbot_llm_messages(
                system_chunks=chunks,
                history=hist,
                query=query,
                image_urls=image_urls,
            )

        # 先裁历史，再裁排序靠后的 RAG 片段；预算用中文安全 token 上界
        build_result = trim_history_and_build_chatbot_messages(
            history_for_llm,
            build_messages=build_messages,
            rag_snippets=list(snippets_for_llm),
            context_total_tokens=self._llm_context_total_tokens,
            requested_max_tokens=self._main_llm_max_tokens,
            slack_tokens=self._llm_completion_slack_tokens,
            trim_enabled=self._history_trim_enabled and bool(history_for_llm),
            min_keep=self._history_trim_min_keep,
        )
        if self._anaphora_anchor_enabled and not faq_decision.active:
            kept_hist = history_for_llm[build_result.history_dropped :]
            anchor = build_dialogue_anchor_block(
                kept_hist,
                query,
                anaphora_type,
                config_path=self._anaphora_config_path,
                max_chars=self._anaphora_anchor_max_chars,
                slot_bullets=slot_bullets,
            )
            if anchor:
                anchor_block_out = anchor
        out: ChatbotGraphState = {
            "llm_messages": build_result.messages,
            "llm_max_tokens": build_result.max_tokens,
            "history_trim_dropped": build_result.history_dropped,
            "faq_soft_direct": faq_decision.active,
            "faq_soft_direct_reason": faq_decision.reason,
        }
        if anchor_block_out:
            out["anaphora_anchor_block"] = anchor_block_out
        return out

    async def _node_clarify_build_response(self, state: ChatbotGraphState) -> ChatbotGraphState:
        # 首版澄清话术保持稳定输出，后续可替换为模板化/模型化澄清。
        answer = "为了更准确地回答你，请补充更具体的信息：你要咨询的是哪一项业务、当前遇到的具体问题现象，以及你期望的结果。"
        return {"answer_text": answer, "status": "clarifying", "terminate_reason": "need_clarify"}

    async def _node_finalize(self, state: ChatbotGraphState) -> ChatbotGraphState:
        if state.get("status") not in {"clarifying", "failed"}:
            return {"status": "answered"}
        return {}

    def _route_by_intent(self, state: ChatbotGraphState) -> str:
        confirmed = str(state.get("confirmed_route") or "").strip().lower()
        if confirmed:
            label = confirmed
        else:
            label = str(state.get("intent_label") or "kb_qa").lower()
        if label == "clarify":
            route = "clarify"
        elif label == "data_query":
            route = "data_query"
        elif label == "hybrid_qa":
            route = "hybrid_qa"
        elif label == "unsafe":
            route = "unsafe"
        elif label == "handoff_human":
            route = "handoff_human"
        elif label == "smalltalk":
            route = "smalltalk"
        else:
            route = "kb_qa"
        logger.info(
            "chatbot.route intent label=%s route=%s reason=%s conf=%s",
            label,
            route,
            state.get("intent_reason"),
            state.get("intent_confidence"),
        )
        return route

    def _route_after_quality_check(self, state: ChatbotGraphState) -> str:
        # 路由优先级（非常关键）：
        # 1) 关闭 RAG -> build（不走 C-RAG）
        # 2) 低分且可重试 -> retry
        # 3) 低分且预算耗尽 -> clarify（避免继续硬答）
        # 4) 其它 -> build
        if not state.get("enable_rag", True):
            logger.info(
                "chatbot.route quality enable_rag=false route=build attempts=%s score=%s min_score=%s crag_enabled=%s",
                state.get("retrieval_attempts"),
                state.get("retrieval_score"),
                self._min_score,
                self._crag_enabled,
            )
            return "build"
        score = float(state.get("retrieval_score", 0.0))
        attempts = int(state.get("retrieval_attempts", 0))
        if self._crag_enabled and score < self._min_score and attempts < self._max_attempts:
            route = "retry"
        elif score < self._min_score and attempts >= self._max_attempts:
            route = "clarify"
        else:
            route = "build"
        logger.info(
            "chatbot.route quality route=%s attempts=%s score=%.3f min_score=%.3f max_attempts=%s snippets=%s rag_engine=%s",
            route,
            attempts,
            score,
            self._min_score,
            self._max_attempts,
            len(state.get("context_snippets") or []),
            state.get("rag_engine"),
        )
        return route

    def _persist_success(
        self,
        state: ChatbotGraphState,
        req: ChatRequest,
        answer: str,
        is_partial: bool,
        terminate_reason: Optional[str],
    ) -> None:
        # 成功路径落库：固定先 user 再 assistant，保持会话顺序稳定。
        # 注意：partial 也走 assistant 落库，但会加 [partial] 前缀。
        self._conv.append_user_message(
            req.user_id,
            req.session_id,
            build_user_message_with_images(
                req.query,
                req.image_urls,
                original_image_urls=[u for u in (state.get("original_image_urls") or []) if isinstance(u, str) and u.strip()],
                processed_image_urls=[u for u in (state.get("image_urls") or []) if isinstance(u, str) and u.strip()],
            ),
        )
        if answer:
            content = answer if not is_partial else f"[partial] {answer}"
            rc_list = state.get("rag_citations")
            rag_kw: list[dict[str, Any]] | None = (
                [x for x in rc_list if isinstance(x, dict)] if isinstance(rc_list, list) else None
            )
            self._conv.append_assistant_message(req.user_id, req.session_id, content, rag_citations=rag_kw)
            if (
                not is_partial
                and self._anaphora_slots_enabled
                and (answer or "").strip()
                and not state.get("used_nl2sql")
            ):
                try:
                    update_anaphora_slots_after_assistant(
                        req.user_id,
                        req.session_id,
                        answer,
                        last_user_anaphora_type=(str(state.get("anaphora_type") or "").strip() or None),
                        max_bullets=self._anaphora_slots_max_bullets,
                    )
                except Exception as exc:  # noqa: BLE001
                    logger.warning("anaphora slots update failed: %s", exc)
            if not is_partial and self._outline_store is not None and self._outline_store.enabled:
                self._schedule_outline_index(req.user_id, req.session_id, content)
        state["answer_text"] = answer
        state["is_partial"] = is_partial
        state["terminate_reason"] = terminate_reason
        state["status"] = "aborted" if is_partial else "answered"

    def _schedule_outline_index(self, user_id: str, session_id: str, answer_text: str) -> None:
        if self._outline_store is None:
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
                    answer_text=answer_text,
                )
            )
        except Exception:
            pass

    def _persist_hitl_turn(self, state: ChatbotGraphState, req: ChatRequest, assistant_text: str) -> None:
        """首轮 HITL interrupt：落库 user 原问 + assistant 确认话术。"""
        from app.services.chatbot_image_utils import build_user_message_with_images

        self._conv.append_user_message(
            req.user_id,
            req.session_id,
            build_user_message_with_images(
                req.query,
                req.image_urls,
                original_image_urls=[
                    u for u in (state.get("original_image_urls") or []) if isinstance(u, str) and u.strip()
                ],
                processed_image_urls=[
                    u for u in (state.get("image_urls") or []) if isinstance(u, str) and u.strip()
                ],
            ),
        )
        if assistant_text.strip():
            self._conv.append_assistant_message(req.user_id, req.session_id, assistant_text.strip())

    def _persist_resume_user_choice(
        self,
        user_id: str,
        session_id: str,
        action: str,
        *,
        label: str | None = None,
    ) -> None:
        choice_label = (label or "").strip() or hitl_button_label(action) or action
        self._conv.append_user_message(
            user_id,
            session_id,
            format_hitl_user_choice_message(action=action, label=choice_label),
        )

    async def _emit_hitl_events(
        self,
        state: ChatbotGraphState,
        req: ChatRequest,
        start_ts: float,
        stream_id: str | None,
    ) -> AsyncIterator[Dict[str, Any]]:
        interrupt_payload = build_hitl_interrupt_payload(state)
        token = create_chatbot_hitl_resume_session(
            user_id=req.user_id,
            session_id=req.session_id,
            hitl_kind=str(state.get("hitl_kind") or ""),
            state_snapshot=dict(state),
            interrupt_payload=interrupt_payload,
        )
        hitl_ev = build_hitl_sse_event(state, resume_token=token)
        state["resume_token"] = token
        prompt = str(hitl_ev.get("prompt") or "")
        assistant_text = format_hitl_assistant_message(
            hitl_kind=str(state.get("hitl_kind") or ""),
            prompt=prompt,
        )
        self._persist_hitl_turn(state, req, assistant_text)
        state["answer_text"] = assistant_text
        state["status"] = "awaiting_hitl"
        yield {"type": "delta", "delta": assistant_text}
        yield hitl_ev
        yield {"type": "finished", "meta": self._build_finished_meta(state, start_ts, stream_id)}

    async def run_resume_stream_events(
        self,
        *,
        user_id: str,
        session_id: str,
        resume_token: str,
        action: str,
        payload: dict[str, Any] | None = None,
        stream_id: str | None = None,
        cancel_checker: Any | None = None,
    ) -> AsyncIterator[Dict[str, Any]]:
        """HITL 续跑：应用用户按钮选择后继续图编排。支持 ``/chat/stop`` 经 cancel_checker 中断。"""
        session = get_chatbot_hitl_resume_session(resume_token)
        if session is None:
            yield {"type": "error", "error": "invalid or expired resume_token"}
            return
        if session.user_id != user_id or session.session_id != session_id:
            yield {"type": "error", "error": "resume_token session mismatch"}
            return

        state: ChatbotGraphState = dict(session.state_snapshot)  # type: ignore[assignment]
        try:
            state = apply_chatbot_hitl_action(state, action=action, payload=payload or {})  # type: ignore[assignment]
        except ChatbotHitlValidationError as exc:
            yield {"type": "error", "error": str(exc)}
            yield {"type": "finished", "meta": {"status": "failed", "error": str(exc)}}
            return
        delete_chatbot_hitl_resume_session(resume_token)
        self._persist_resume_user_choice(
            user_id,
            session_id,
            action,
            label=str(state.get("hitl_choice_label") or "") or None,
        )

        req = ChatRequest(
            user_id=user_id,
            session_id=session_id,
            query=str(state.get("query") or state.get("hitl_original_query") or ""),
            image_urls=[u for u in (state.get("image_urls") or []) if isinstance(u, str) and u.strip()],
            enable_rag=bool(state.get("enable_rag", True)),
            enable_context=bool(state.get("enable_context", True)),
            enable_nl2sql_route=bool(state.get("enable_nl2sql_route", True)),
            prompt_version=state.get("client_prompt_version") or state.get("prompt_version"),
        )
        start_ts = time.perf_counter()
        try:
            state = await self._run_graph(state, resume=True)
            self._ensure_within_latency(start_ts)

            if state.get("pending_hitl"):
                async for ev in self._emit_hitl_events(state, req, start_ts, stream_id):
                    yield ev
                return

            if state.get("nl2sql_analysis_stream_plan"):
                async for ev in self._emit_nl2sql_analysis_stream(
                    state, req, start_ts, stream_id, cancel_checker, persist_mode="resume"
                ):
                    yield ev
                return

            pre_answer = (state.get("answer_text") or "").strip()
            llm_messages = state.get("llm_messages") or []
            no_stream_path = (
                state.get("intent_label") == "clarify"
                or state.get("status") == "awaiting_hitl"
                or (bool(pre_answer) and not llm_messages)
            )
            if no_stream_path:
                if await self._is_cancelled(req, stream_id, cancel_checker):
                    state["status"] = "aborted"
                    state["terminate_reason"] = "user_cancelled"
                    yield {"type": "finished", "meta": self._build_finished_meta(state, start_ts, stream_id)}
                    return
                answer = pre_answer
                extra = self._maybe_similar_cases_extra(state)
                full = (answer + extra).strip()
                await self._fill_suggested_questions(state, req, full)
                if full:
                    rc_list = state.get("rag_citations")
                    rag_kw = [x for x in rc_list if isinstance(x, dict)] if isinstance(rc_list, list) else None
                    self._conv.append_assistant_message(req.user_id, req.session_id, full, rag_citations=rag_kw)
                state["status"] = "answered"
                if answer:
                    yield {"type": "delta", "delta": answer}
                if extra:
                    yield {"type": "delta", "delta": extra}
                yield {"type": "finished", "meta": self._build_finished_meta(state, start_ts, stream_id)}
                return

            parts: List[str] = []
            requested_max = int(state.get("llm_max_tokens") or self._main_llm_max_tokens)
            safe_max = ensure_chatbot_stream_max_tokens(
                list(llm_messages),
                requested_max_tokens=requested_max,
                context_total_tokens=self._llm_context_total_tokens,
                slack_tokens=self._llm_completion_slack_tokens,
            )
            stream_kw: Dict[str, Any] = {"max_tokens": safe_max}
            if self._main_llm_temperature is not None:
                stream_kw["temperature"] = float(self._main_llm_temperature)
            async for delta in self._llm.stream_chat(model=None, messages=llm_messages, **stream_kw):  # type: ignore[arg-type]
                if await self._is_cancelled(req, stream_id, cancel_checker):
                    partial = "".join(parts).strip()
                    self._persist_resume_partial(state, req, partial)
                    state["status"] = "aborted"
                    state["terminate_reason"] = "user_cancelled"
                    yield {"type": "finished", "meta": self._build_finished_meta(state, start_ts, stream_id)}
                    return
                parts.append(delta)
                yield {"type": "delta", "delta": delta}
            answer = "".join(parts).strip()
            extra = self._maybe_similar_cases_extra(state)
            full_stream = (answer + extra).strip()
            await self._fill_suggested_questions(state, req, full_stream)
            if full_stream:
                rc_list = state.get("rag_citations")
                rag_kw = [x for x in rc_list if isinstance(x, dict)] if isinstance(rc_list, list) else None
                self._conv.append_assistant_message(req.user_id, req.session_id, full_stream, rag_citations=rag_kw)
            state["status"] = "answered"
            if extra:
                yield {"type": "delta", "delta": extra}
            yield {"type": "finished", "meta": self._build_finished_meta(state, start_ts, stream_id)}
        except Exception as exc:  # noqa: BLE001
            logger.exception("ChatbotLangGraphRunner.run_resume_stream_events failed: %s", exc)
            yield {"type": "error", "error": str(exc)}
            yield {"type": "finished", "meta": {"status": "failed", "error": str(exc)}}

    def _persist_failure(self, state: ChatbotGraphState, req: ChatRequest) -> None:
        # 失败时仍写 user，保证会话线完整；assistant 不写入。
        self._conv.append_user_message(
            req.user_id,
            req.session_id,
            build_user_message_with_images(
                req.query,
                req.image_urls,
                original_image_urls=[u for u in (state.get("original_image_urls") or []) if isinstance(u, str) and u.strip()],
                processed_image_urls=[u for u in (state.get("image_urls") or []) if isinstance(u, str) and u.strip()],
            ),
        )

    def _persist_disconnect(self, state: ChatbotGraphState, req: ChatRequest, partial: str) -> None:
        self._conv.append_user_message(
            req.user_id,
            req.session_id,
            build_user_message_with_images(
                req.query,
                req.image_urls,
                original_image_urls=[u for u in (state.get("original_image_urls") or []) if isinstance(u, str) and u.strip()],
                processed_image_urls=[u for u in (state.get("image_urls") or []) if isinstance(u, str) and u.strip()],
            ),
        )
        self._persist_resume_partial(state, req, partial)

    def _persist_resume_partial(self, state: ChatbotGraphState, req: ChatRequest, partial: str) -> None:
        """HITL 续跑中断：仅落库 partial assistant（user 选择消息已写入）。"""
        if self._persist_partial and partial:
            rc_list = state.get("rag_citations")
            rag_kw: list[dict[str, Any]] | None = (
                [x for x in rc_list if isinstance(x, dict)] if isinstance(rc_list, list) else None
            )
            self._conv.append_assistant_message(
                req.user_id, req.session_id, f"[partial] {partial}", rag_citations=rag_kw
            )

    async def _fill_suggested_questions(self, state: ChatbotGraphState, req: ChatRequest, answer_text: str) -> None:
        if not self._suggested_questions_enabled:
            state["suggested_questions"] = []
            return
        # 纯查数不推关联问；Hybrid（查数+知识）允许下发
        if state.get("intent_label") == "data_query" or (
            state.get("used_nl2sql") and state.get("intent_label") != "hybrid_qa"
        ):
            state["suggested_questions"] = []
            return
        sq = await build_suggested_questions(
            query=req.query,
            answer=answer_text,
            context_snippets=list(state.get("context_snippets") or []),
            intent_label=str(state.get("intent_label") or "kb_qa"),
            llm_client=self._llm,
            max_total=self._suggested_questions_max,
        )
        state["suggested_questions"] = sq

    def _build_finished_meta(self, state: ChatbotGraphState, start_ts: float, stream_id: str | None = None) -> Dict[str, Any]:
        # 结束 meta 同时服务于：
        # 1) SSE 最后一帧给前端；
        # 2) LangSmith outputs 聚合。
        # 字段名应尽量保持稳定，避免下游解析兼容性问题。
        # 纯查数不展示 citations / 关联问；Hybrid 双臂成功时保留 RAG 侧引用与关联问
        is_hybrid = state.get("intent_label") == "hybrid_qa" or (
            bool(state.get("used_nl2sql")) and bool(state.get("used_rag"))
        )
        is_data_query = (
            bool(state.get("used_nl2sql")) or state.get("intent_label") == "data_query"
        ) and not is_hybrid
        suggested = [] if is_data_query else list(state.get("suggested_questions") or [])
        citations = (
            []
            if is_data_query
            else filter_rag_citation_dicts(list(state.get("rag_citations") or []))
        )
        used_nl2sql = bool(state.get("used_nl2sql", False))
        nl2sql_failed = bool(state.get("nl2sql_failed", False))
        nl2sql_sql_meta: str | None
        if used_nl2sql:
            nl2sql_sql_meta = (state.get("nl2sql_sql") or "") or None
            if nl2sql_failed and not (state.get("nl2sql_sql") or "").strip():
                nl2sql_sql_meta = None
        else:
            nl2sql_sql_meta = None
        return {
            "used_rag": bool(state.get("used_rag", False)),
            "intent_label": state.get("intent_label"),
            "retrieval_attempts": int(state.get("retrieval_attempts", 0)),
            "rag_engine": state.get("rag_engine"),
            "rag_namespace": state.get("rag_namespace"),
            "rag_scope_reason": state.get("rag_scope_reason"),
            "rag_scope_fallback": bool(state.get("rag_scope_fallback")),
            "faq_soft_direct": bool(state.get("faq_soft_direct", False)),
            "faq_soft_direct_reason": str(state.get("faq_soft_direct_reason") or ""),
            "history_trim_dropped": int(state.get("history_trim_dropped") or 0),
            "status": state.get("status"),
            "duration_ms": int((time.perf_counter() - start_ts) * 1000),
            "terminate_reason": state.get("terminate_reason"),
            "is_partial": bool(state.get("is_partial", False)),
            "similar_cases_appended": bool(state.get("similar_cases_appended")),
            "similar_case_namespace": self._similar_case_namespace if state.get("similar_cases_appended") else None,
            "fault_detect_sources": list(state.get("fault_detect_sources") or []),
            "fault_detect_confidence": float(state.get("fault_detect_confidence") or 0.0),
            "need_similar_cases": bool(state.get("need_similar_cases")),
            "used_nl2sql": used_nl2sql,
            "nl2sql_failed": nl2sql_failed if used_nl2sql else None,
            "nl2sql_error_code": (state.get("nl2sql_error_code") or None) if nl2sql_failed else None,
            "nl2sql_sql": nl2sql_sql_meta,
            "nl2sql_analysis": (state.get("nl2sql_analysis") if used_nl2sql else None),
            "suggested_questions": suggested,
            "rag_citations": citations,
            "hybrid_degraded": state.get("hybrid_degraded") or None,
            "processed_image_urls": [u for u in (state.get("image_urls") or []) if isinstance(u, str) and u.strip()],
            "original_image_urls": [u for u in (state.get("original_image_urls") or []) if isinstance(u, str) and u.strip()],
            "stream_id": stream_id,
            "pending_hitl": bool(state.get("pending_hitl")),
            "hitl_kind": state.get("hitl_kind") or None,
            "resume_token": state.get("resume_token"),
            **self._anaphora_meta_extras(state),
        }

    def _anaphora_meta_extras(self, state: ChatbotGraphState) -> Dict[str, Any]:
        if not self._anaphora_expose_meta:
            return {}
        ab = state.get("anaphora_anchor_block") or ""
        return {
            "anaphora_type": state.get("anaphora_type"),
            "anaphora_rule_type": state.get("anaphora_rule_type"),
            "anaphora_confidence": state.get("anaphora_confidence"),
            "anaphora_source": state.get("anaphora_source"),
            "anaphora_anchor_block_len": len(ab) if isinstance(ab, str) else 0,
        }

    async def _is_cancelled(self, req: ChatRequest, stream_id: str | None, cancel_checker: Any | None) -> bool:
        if not stream_id or cancel_checker is None:
            return False
        try:
            return bool(await cancel_checker(req.user_id, req.session_id, stream_id))
        except Exception as exc:  # noqa: BLE001
            logger.warning("cancel checker failed stream_id=%s err=%s", stream_id, exc)
            return False
