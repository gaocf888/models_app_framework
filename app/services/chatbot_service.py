from __future__ import annotations

import asyncio
import time

from app.conversation.manager import ConversationManager
from app.conversation.message_id import build_conversation_message_id
from app.core.config import get_app_config
from app.core.logging import get_logger
from app.models.chatbot import ChatRequest, ChatResponse, ChatbotHitlResumeRequest
from app.llm.client import VLLMHttpClient
from app.llm.graphs import ChatbotLangGraphRunner
from app.llm.graphs.chatbot_faq_soft_direct import (
    evaluate_faq_soft_direct,
    format_rag_snippets_for_generation,
    snippets_for_llm_generation,
)
from app.llm.graphs.chatbot_rag_scope import augment_retrieval_query_for_plant_kb, resolve_rag_namespace
from app.llm.graphs.chatbot_follow_up import build_suggested_questions
from app.llm.graphs.chatbot_intent import classify_chatbot_intent_async
from app.llm.graphs.chatbot_citation_stream import CitationStreamParser, citation_stream_enabled, max_citation_ref_index
from app.llm.graphs.chatbot_rag_citations import chunks_to_rag_context
from app.llm.graphs.chatbot_llm_messages import (
    ChatbotLlmBuildResult,
    assemble_chatbot_llm_messages,
    trim_history_and_build_chatbot_messages,
)
from app.llm.graphs.chatbot_anaphora_detect import classify_anaphora_rules
from app.llm.graphs.chatbot_dialogue_anchor import build_dialogue_anchor_block
from app.llm.graphs.chatbot_anaphora_store import get_anaphora_slots, slot_bullets_list, update_anaphora_slots_after_assistant
from app.llm.graphs.chatbot_nl2sql_answer import (
    finalize_streamed_nl2sql_analysis,
    iter_analysis_llm_deltas,
    run_chatbot_nl2sql_query,
)
from app.llm.graphs.chatbot_similar_cases import (
    FaultCaseGateInput,
    format_similar_cases_block,
    retrieve_similar_case_snippets,
    run_fault_case_gate_decision,
)
from app.services.nl2sql_service import NL2SQLService
from app.llm.prompt_registry import PromptTemplateRegistry
from app.rag.hybrid_rag_service import HybridRAGService
from app.rag.rag_service import RAGService
from app.services.chatbot_image_preprocessor import ChatbotImagePreprocessor
from app.services.chatbot_image_utils import build_user_message_with_images, strip_image_block_from_history
from app.services.chatbot_outline import ChatbotOutlineStore
from app.services.chatbot_stream_control import ChatbotStreamControl
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
        self._nl2sql = NL2SQLService(conv_manager=self._conv)
        self._chain = None

        # 如果安装了 LangChain 相关依赖，则启用 ChatbotChain 作为编排层
        try:
            from app.llm.chains.chatbot_chain import ChatbotChain

            self._chain = ChatbotChain(rag_service=self._rag, conv_manager=self._conv)
            logger.info("ChatbotService: LangChain ChatbotChain enabled.")
        except ImportError:
            logger.warning("ChatbotService: LangChain not available, fallback to simple implementation.")

    def _rag_context_and_citations(
        self,
        query: str,
        enable_rag: bool,
        *,
        history: list[dict] | None = None,
        user_id: str | None = None,
        session_id: str | None = None,
        enable_context: bool = True,
    ) -> tuple[list[str], list[dict[str, Any]], str, list[str]]:
        """与 LangGraph `kb_retrieve` 对齐；额外返回规则层 `anaphora_type` 与槽位 bullets（供锚块/落槽）。"""
        if not enable_rag:
            return [], [], "none", []
        cfg = self._chatbot_cfg
        hist = list(history or [])
        slot_bullets: list[str] = []
        if user_id and session_id and cfg.anaphora_slots_enabled:
            slot_bullets = slot_bullets_list(get_anaphora_slots(user_id, session_id))
        rule = classify_anaphora_rules(
            query,
            hist,
            enable_context=enable_context,
            config_path=cfg.anaphora_config_path,
        )
        rag_q, _, _ = build_retrieval_query_with_anaphora(
            query,
            hist,
            enable_context=enable_context,
            fusion_enabled=cfg.anaphora_retrieval_fusion_enabled,
            fusion_max_chars=cfg.anaphora_fusion_max_chars,
            config_path=cfg.anaphora_config_path,
            anaphora_type=rule.anaphora_type,
            rule_result=rule,
        )
        scope = resolve_rag_namespace(
            query,
            enabled=cfg.plant_kb_enabled,
            plant_kb_namespace=cfg.plant_kb_namespace,
            history_messages=hist if enable_context else None,
            enable_context=enable_context,
            history_continuation=bool(cfg.plant_kb_history_continuation),
            query_boost_name=cfg.plant_kb_query_boost_name or None,
        )
        rag_ns = scope.rag_namespace
        if rag_ns:
            rag_q = augment_retrieval_query_for_plant_kb(rag_q, query_boost=scope.query_boost)
        graph_active = bool(
            get_app_config().rag.graph.enabled
            and getattr(self._hybrid_rag, "_graph_query", None) is not None
        )
        if not graph_active:
            chunks = self._rag.retrieve_chunks(rag_q, scene="chatbot", namespace=rag_ns)
            if rag_ns and cfg.plant_kb_fallback_on_empty and not chunks:
                chunks = self._rag.retrieve_chunks(rag_q, scene="chatbot", namespace=None)
            return *chunks_to_rag_context(chunks), rule.anaphora_type, slot_bullets
        chunks = self._rag.retrieve_chunks(rag_q, scene="chatbot", namespace=rag_ns)
        if rag_ns and cfg.plant_kb_fallback_on_empty and not chunks:
            chunks = self._rag.retrieve_chunks(rag_q, scene="chatbot", namespace=None)
        return *chunks_to_rag_context(chunks), rule.anaphora_type, slot_bullets

    def _maybe_update_anaphora_slots(
        self,
        user_id: str,
        session_id: str,
        assistant_text: str,
        last_user_anaphora_type: str | None,
    ) -> None:
        cfg = self._chatbot_cfg
        if not cfg.anaphora_slots_enabled or not (assistant_text or "").strip():
            return
        try:
            update_anaphora_slots_after_assistant(
                user_id,
                session_id,
                assistant_text,
                last_user_anaphora_type=(last_user_anaphora_type or "").strip() or None,
                max_bullets=cfg.anaphora_slots_max_bullets,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("anaphora slots update (legacy) failed: %s", exc)

    async def chat(self, req: ChatRequest) -> ChatResponse:
        original_image_urls = self._clean_image_urls(req.image_urls)
        req = await self._preprocess_request_images(req)
        model_req = await self._apply_structured_reference(req)
        if not req.user_id:
            raise ValueError("user_id is required (must be provided by the caller).")
        # 不在此处提前 append_user：须先取历史再组 messages，否则当前句已进 history，
        # _build_llm_messages / ChatbotChain 再追加本轮 query，会造成「双份当前用户句」且易干扰多轮理解。
        # 本轮 user/assistant 在得到 answer 后统一写入（见文末）。

        cfg = self._chatbot_cfg
        intent_labels = {x.strip().lower() for x in (cfg.intent_output_labels or []) if x.strip()}
        enable_nl2sql = bool(req.enable_nl2sql_route) and bool(cfg.nl2sql_route_enabled)
        hist = (
            self._conv.get_recent_history(req.user_id, req.session_id, limit=max(1, int(cfg.history_limit)))
            if req.enable_context
            else None
        )
        ir = await classify_chatbot_intent_async(
            model_req.query,
            enable_nl2sql_route=enable_nl2sql,
            image_urls=[u for u in model_req.image_urls if isinstance(u, str) and u.strip()],
            history_messages=list(hist) if hist else None,
        )
        ilabel = ir.intent_label
        if ilabel not in intent_labels:
            ilabel = "kb_qa"

        if ilabel == "data_query":
            outcome = await run_chatbot_nl2sql_query(
                self._nl2sql,
                self._llm,
                user_id=req.user_id,
                session_id=req.session_id,
                question=req.query,
            )
            answer = outcome.answer_text
            suggested: list[str] = []
            if cfg.suggested_questions_enabled:
                suggested = await build_suggested_questions(
                    query=req.query,
                    answer=answer,
                    context_snippets=[],
                    intent_label="data_query",
                    llm_client=self._llm,
                    max_total=cfg.suggested_questions_max,
                )
            self._append_user_with_images(req, original_image_urls=original_image_urls)
            self._conv.append_assistant_message(req.user_id, req.session_id, answer, rag_citations=[])
            self._schedule_outline_index(req.user_id, req.session_id)
            return ChatResponse(
                answer=answer,
                used_rag=False,
                used_nl2sql=True,
                intent_label=ilabel,
                suggested_questions=suggested,
                context_snippets=[],
                rag_citations=[],
                nl2sql_analysis=outcome.nl2sql_analysis,
            )

        if ilabel == "clarify":
            answer = (
                "为了更准确地回答你，请补充更具体的信息：你要咨询的是哪一项业务、当前遇到的具体问题现象，以及你期望的结果。"
            )
            suggested_clarify: list[str] = []
            if cfg.suggested_questions_enabled:
                suggested_clarify = await build_suggested_questions(
                    query=req.query,
                    answer=answer,
                    context_snippets=[],
                    intent_label="clarify",
                    llm_client=self._llm,
                    max_total=min(3, cfg.suggested_questions_max),
                )
            self._append_user_with_images(req, original_image_urls=original_image_urls)
            self._conv.append_assistant_message(req.user_id, req.session_id, answer, rag_citations=[])
            self._schedule_outline_index(req.user_id, req.session_id)
            return ChatResponse(
                answer=answer,
                used_rag=False,
                used_nl2sql=False,
                intent_label=ilabel,
                suggested_questions=suggested_clarify,
                context_snippets=[],
                rag_citations=[],
            )

        # 优先使用 LangChain ChatbotChain（若可用）
        rag_citations: list[dict[str, Any]] = []
        anaphora_type_for_slots: str | None = None
        if self._chain is not None:
            answer = await self._chain.run(
                user_id=req.user_id,
                session_id=req.session_id,
                query=model_req.query,
                enable_rag=req.enable_rag,
                enable_context=req.enable_context,
                prompt_version=req.prompt_version,
            )
            # 目前链路内部已处理 RAG 与上下文，外部仅标记 used_rag 为请求开关
            used_rag = req.enable_rag
            context_snippets = []
        else:
            hist_list = list(hist) if hist else []
            context_snippets, rag_citations, anaphora_type, slot_bullets = self._rag_context_and_citations(
                model_req.query,
                req.enable_rag,
                history=hist_list,
                user_id=req.user_id,
                session_id=req.session_id,
                enable_context=req.enable_context,
            )
            used_rag = len(context_snippets) > 0

            history = hist_list

            # 使用统一 LLM 客户端生成回答（多模态 message）
            llm_build = self._build_llm_messages(
                req=model_req,
                history=history,
                context_snippets=context_snippets,
                anaphora_type=anaphora_type,
                anaphora_rule_type=anaphora_type,
                anaphora_slot_bullets=slot_bullets,
                rag_citations=rag_citations,
                intent_label=ilabel,
                enable_rag=req.enable_rag,
            )

            try:
                answer = await self._llm.chat(  # type: ignore[arg-type]
                    model=None,
                    messages=llm_build.messages,
                    max_tokens=llm_build.max_tokens,
                )
            except Exception:  # noqa: BLE001
                logger.exception("ChatbotService: LLM 调用失败，退回占位回答。")
                base = "这是占位回答（大模型暂不可用）。"
                if used_rag:
                    base += f"（已检索到 {len(context_snippets)} 条上下文片段用于参考）"
                answer = base
            anaphora_type_for_slots = anaphora_type

        suggested_out: list[str] = []
        if cfg.suggested_questions_enabled:
            suggested_out = await build_suggested_questions(
                query=req.query,
                answer=answer,
                context_snippets=context_snippets,
                intent_label=ilabel,
                llm_client=self._llm,
                max_total=cfg.suggested_questions_max,
            )

        self._append_user_with_images(req, original_image_urls=original_image_urls)
        self._conv.append_assistant_message(req.user_id, req.session_id, answer, rag_citations=rag_citations)
        self._maybe_update_anaphora_slots(req.user_id, req.session_id, answer, anaphora_type_for_slots)
        self._schedule_outline_index(req.user_id, req.session_id)

        return ChatResponse(
            answer=answer,
            used_rag=used_rag,
            used_nl2sql=False,
            intent_label=ilabel,
            suggested_questions=suggested_out,
            context_snippets=context_snippets,
            rag_citations=rag_citations,
        )

    async def stream_chat(self, req: ChatRequest) -> AsyncIterator[str]:
        if not req.user_id:
            raise ValueError("user_id is required (must be provided by the caller).")
        """
        token 级流式输出（基于 vLLM OpenAI 兼容 stream）。

        会话写入顺序：先按「不含本轮」的历史组 messages，流式结束后再 append 本轮 user + assistant，
        与 `chat()` 非流式路径一致，避免历史里先插入当前用户句导致重复与上下文错乱。
        """
        async for ev in self.stream_chat_events(req):
            if ev.get("type") == "delta":
                yield str(ev.get("delta") or "")

    async def stream_chat_events(self, req: ChatRequest) -> AsyncIterator[Dict[str, Any]]:
        original_image_urls = self._clean_image_urls(req.image_urls)
        req = await self._preprocess_request_images(req)
        model_req = await self._apply_structured_reference(req)
        if not req.user_id:
            raise ValueError("user_id is required (must be provided by the caller).")
        """
        结构化流式事件输出（供 API 层组装 SSE payload 使用）。

        事件类型：
        - started: {"type": "started", "stream_id": "..."}
        - delta: {"type": "delta", "delta": "..."}
        - citation: {"type": "citation", "ref_index": n}（API 层映射 SSE ``citation_ref``）
        - finished: {"type": "finished", "meta": {...}}
        """
        stream_id = self._stream_ctrl.begin_stream(req.user_id, req.session_id)
        yield {"type": "started", "stream_id": stream_id}
        # 显式关闭 graph：走 legacy 流式实现，确保开关语义符合部署预期。
        if not self._chatbot_cfg.graph_enabled:
            try:
                async for ev in self._stream_chat_legacy_events(
                    model_req,
                    stream_id=stream_id,
                    original_image_urls=original_image_urls,
                    original_query=req.query,
                ):
                    yield ev
                return
            finally:
                await self._stream_ctrl.clear_stream(req.user_id, req.session_id, stream_id)

        try:
            async for ev in self._graph_runner.run_stream_events(
                req,
                model_req=model_req,
                stream_id=stream_id,
                cancel_checker=self._stream_ctrl.is_cancelled,
                original_image_urls=original_image_urls,
            ):
                yield ev
            return
        except Exception:
            if not self._chatbot_cfg.fallback_legacy_on_error:
                raise
            logger.exception("ChatbotService.stream_chat_events graph failed, fallback to legacy path.")
            async for ev in self._stream_chat_legacy_events(
                model_req,
                stream_id=stream_id,
                original_image_urls=original_image_urls,
                original_query=req.query,
            ):
                yield ev
        finally:
            await self._stream_ctrl.clear_stream(req.user_id, req.session_id, stream_id)

    async def stream_chat_resume_events(
        self,
        req: ChatbotHitlResumeRequest,
    ) -> AsyncIterator[Dict[str, Any]]:
        """HITL 续跑结构化事件（供 /chat/resume-stream SSE 使用）。"""
        stream_id = self._stream_ctrl.begin_stream(req.user_id, req.session_id)
        yield {"type": "started", "stream_id": stream_id}
        try:
            async for ev in self._graph_runner.run_resume_stream_events(
                user_id=req.user_id,
                session_id=req.session_id,
                resume_token=req.resume_token,
                action=req.action,
                payload=dict(req.payload or {}),
                stream_id=stream_id,
                cancel_checker=self._stream_ctrl.is_cancelled,
            ):
                yield ev
        finally:
            await self._stream_ctrl.clear_stream(req.user_id, req.session_id, stream_id)

    async def _stream_chat_legacy_events(
        self,
        req: ChatRequest,
        stream_id: str | None = None,
        original_image_urls: list[str] | None = None,
        original_query: str | None = None,
    ) -> AsyncIterator[Dict[str, Any]]:
        """
        旧版流式路径（兜底/回退专用）。

        说明：
        - 仅在 graph 关闭或 graph 运行异常且允许回退时启用；
        - 保持与历史行为一致：检索 -> 历史 -> 组 messages -> vLLM stream -> 会话写入；
        - 若启用相似案例扩展，与 LangGraph 路径一致：主回答流结束后追加限定 namespace 检索块。
        """
        start_ts = time.perf_counter()
        persist_req = req if not original_query else req.model_copy(update={"query": original_query})
        cfg = self._chatbot_cfg
        intent_labels = {x.strip().lower() for x in (cfg.intent_output_labels or []) if x.strip()}
        enable_nl2sql = bool(req.enable_nl2sql_route) and bool(cfg.nl2sql_route_enabled)
        imgs = [u for u in req.image_urls if isinstance(u, str) and u.strip()]
        hist = (
            self._conv.get_recent_history(req.user_id, req.session_id, limit=max(1, int(cfg.history_limit)))
            if req.enable_context
            else None
        )
        ir = await classify_chatbot_intent_async(
            req.query,
            enable_nl2sql_route=enable_nl2sql,
            image_urls=imgs,
            history_messages=list(hist) if hist else None,
        )
        ilabel = ir.intent_label
        if ilabel not in intent_labels:
            ilabel = "kb_qa"
        logger.info(
            "chatbot.legacy intent label=%s enable_nl2sql=%s has_images=%s query_len=%s",
            ilabel,
            enable_nl2sql,
            bool(imgs),
            len(req.query or ""),
        )

        duration_ms = lambda: int((time.perf_counter() - start_ts) * 1000)

        if ilabel == "data_query":
            outcome = await run_chatbot_nl2sql_query(
                self._nl2sql,
                self._llm,
                user_id=req.user_id,
                session_id=req.session_id,
                question=(original_query or req.query),
                defer_analysis_stream=True,
            )
            answer = outcome.answer_text
            analysis_meta = outcome.nl2sql_analysis
            plan = outcome.analysis_stream_plan
            if plan is not None:
                parts: list[str] = []
                try:
                    async for delta in iter_analysis_llm_deltas(
                        self._llm,
                        system=plan.system,
                        user_content=plan.user_content,
                    ):
                        parts.append(delta)
                        yield {"type": "delta", "delta": delta}
                except Exception:
                    logger.warning("chatbot.legacy nl2sql analysis stream failed", exc_info=True)
                    parts = []
                streamed = "".join(parts).strip()
                finalized = finalize_streamed_nl2sql_analysis(plan, streamed)
                answer = finalized.answer_text
                analysis_meta = finalized.analysis_meta
                if not streamed and answer:
                    yield {"type": "delta", "delta": answer}
            elif answer:
                yield {"type": "delta", "delta": answer}
            # data_query：不在 finished.meta 中下发关联问句（与 LangGraph 路径一致，且不调用推荐问 LLM）。
            suggested: list[str] = []
            self._append_user_with_images(persist_req, original_image_urls=original_image_urls)
            self._conv.append_assistant_message(req.user_id, req.session_id, answer, rag_citations=[])
            self._schedule_outline_index(req.user_id, req.session_id)
            yield {
                "type": "finished",
                "meta": {
                    "used_rag": False,
                    "used_nl2sql": True,
                    "nl2sql_failed": outcome.nl2sql_failed or None,
                    "nl2sql_error_code": outcome.nl2sql_error_code,
                    "nl2sql_sql": outcome.nl2sql_sql,
                    "nl2sql_analysis": analysis_meta,
                    "intent_label": ilabel,
                    "retrieval_attempts": 0,
                    "rag_engine": None,
                    "status": "answered",
                    "duration_ms": duration_ms(),
                    "terminate_reason": outcome.terminate_reason,
                    "similar_cases_appended": False,
                    "similar_case_namespace": None,
                    "fault_detect_sources": [],
                    "fault_detect_confidence": 0.0,
                    "need_similar_cases": False,
                    "suggested_questions": suggested,
                    "rag_citations": [],
                    "processed_image_urls": imgs,
                    "stream_id": stream_id,
                },
            }
            return

        if ilabel == "clarify":
            answer = (
                "为了更准确地回答你，请补充更具体的信息：你要咨询的是哪一项业务、当前遇到的具体问题现象，以及你期望的结果。"
            )
            logger.info(
                "chatbot.legacy route=clarify terminate_reason=need_clarify query=%s",
                (req.query or "")[:120],
            )
            suggested_cl: list[str] = []
            if cfg.suggested_questions_enabled:
                suggested_cl = await build_suggested_questions(
                    query=req.query,
                    answer=answer,
                    context_snippets=[],
                    intent_label="clarify",
                    llm_client=self._llm,
                    max_total=min(3, cfg.suggested_questions_max),
                )
            yield {"type": "delta", "delta": answer}
            self._append_user_with_images(persist_req, original_image_urls=original_image_urls)
            self._conv.append_assistant_message(req.user_id, req.session_id, answer, rag_citations=[])
            self._schedule_outline_index(req.user_id, req.session_id)
            yield {
                "type": "finished",
                "meta": {
                    "used_rag": False,
                    "used_nl2sql": False,
                    "nl2sql_sql": None,
                    "intent_label": ilabel,
                    "retrieval_attempts": 0,
                    "rag_engine": None,
                    "status": "clarifying",
                    "duration_ms": duration_ms(),
                    "terminate_reason": "need_clarify",
                    "similar_cases_appended": False,
                    "similar_case_namespace": None,
                    "fault_detect_sources": [],
                    "fault_detect_confidence": 0.0,
                    "need_similar_cases": False,
                    "suggested_questions": suggested_cl,
                    "rag_citations": [],
                    "processed_image_urls": imgs,
                    "stream_id": stream_id,
                },
            }
            return

        history: list[dict] = []
        if req.enable_context:
            history = self._conv.get_recent_history(
                req.user_id,
                req.session_id,
                limit=max(1, int(self._chatbot_cfg.history_limit)),
            )
        context_snippets, rag_citations, anaphora_type, slot_bullets = self._rag_context_and_citations(
            req.query,
            req.enable_rag,
            history=history,
            user_id=req.user_id,
            session_id=req.session_id,
            enable_context=req.enable_context,
        )

        llm_build = self._build_llm_messages(
            req=req,
            history=history,
            context_snippets=context_snippets,
            anaphora_type=anaphora_type,
            anaphora_rule_type=anaphora_type,
            anaphora_slot_bullets=slot_bullets,
            rag_citations=rag_citations,
            intent_label="kb_qa",
            enable_rag=req.enable_rag,
        )
        messages = llm_build.messages
        parts: list[str] = []
        cite_parser: CitationStreamParser | None = None
        if citation_stream_enabled(rag_citations):
            cite_parser = CitationStreamParser(max_ref_index=max_citation_ref_index(rag_citations))
        gate_sources: list[str] = []
        gate_conf = 0.0
        need_cases = False
        legacy_ana_meta: Dict[str, Any] = {}
        if cfg.anaphora_expose_meta:
            legacy_ana_meta = {"anaphora_type": anaphora_type, "anaphora_source": "rule"}
        stream_kw: Dict[str, Any] = {"max_tokens": llm_build.max_tokens}
        if self._chatbot_cfg.main_llm_temperature is not None:
            stream_kw["temperature"] = float(self._chatbot_cfg.main_llm_temperature)
        async for delta in self._llm.stream_chat(model=None, messages=messages, **stream_kw):  # type: ignore[arg-type]
            if await self._is_stream_cancelled(req, stream_id):
                partial = "".join(parts).strip()
                self._append_user_with_images(persist_req, original_image_urls=original_image_urls)
                if self._chatbot_cfg.persist_partial_on_disconnect and partial:
                    self._conv.append_assistant_message(
                        req.user_id,
                        req.session_id,
                        partial,
                        rag_citations=rag_citations,
                        is_partial=True,
                    )
                yield {
                    "type": "finished",
                    "meta": {
                        "used_rag": bool(context_snippets),
                        "used_nl2sql": False,
                        "nl2sql_sql": None,
                        "intent_label": ilabel,
                        "retrieval_attempts": 1 if req.enable_rag else 0,
                        "rag_engine": "hybrid" if req.enable_rag else None,
                        "status": "aborted",
                        "duration_ms": duration_ms(),
                        "terminate_reason": "user_cancelled",
                        "similar_cases_appended": False,
                        "similar_case_namespace": None,
                        "fault_detect_sources": gate_sources,
                        "fault_detect_confidence": gate_conf,
                        "need_similar_cases": need_cases,
                        "suggested_questions": [],
                        "rag_citations": rag_citations,
                        "processed_image_urls": imgs,
                        "stream_id": stream_id,
                        "history_trim_dropped": llm_build.history_dropped,
                        **legacy_ana_meta,
                    },
                }
                return
            parts.append(delta)
            if cite_parser is None:
                yield {"type": "delta", "delta": delta}
            else:
                for ev in cite_parser.feed(delta):
                    yield ev
        if cite_parser is not None:
            for ev in cite_parser.flush():
                yield ev

        answer = "".join(parts).strip()
        extra = ""
        similar_appended = False
        if self._chatbot_cfg.similar_case_enabled:
            gate = await run_fault_case_gate_decision(
                self._llm,
                FaultCaseGateInput(
                    similar_case_enabled=self._chatbot_cfg.similar_case_enabled,
                    fault_detect_enabled=self._chatbot_cfg.fault_detect_enabled,
                    fault_vision_enabled=self._chatbot_cfg.fault_vision_enabled,
                    fault_detect_mode=self._chatbot_cfg.fault_detect_mode,
                    fault_min_confidence=self._chatbot_cfg.fault_min_confidence,
                    intent_label=ilabel,
                    query=req.query,
                    image_urls=imgs,
                    enable_fault_vision=req.enable_fault_vision,
                ),
            )
            gate_sources = list(gate.fault_detect_sources)
            gate_conf = float(gate.fault_detect_confidence)
            need_cases = gate.need_similar_cases
            if gate.need_similar_cases and ilabel != "clarify":
                snippets = retrieve_similar_case_snippets(
                    self._hybrid_rag,
                    query=gate.case_rag_query or req.query,
                    namespace=self._chatbot_cfg.similar_case_namespace,
                    top_k=self._chatbot_cfg.similar_case_top_k,
                )
                extra = format_similar_cases_block(snippets)
                similar_appended = bool(extra.strip())

        if extra:
            yield {"type": "delta", "delta": extra}

        full = (answer + extra).strip()
        suggested_out: list[str] = []
        if cfg.suggested_questions_enabled:
            suggested_out = await build_suggested_questions(
                query=req.query,
                answer=full,
                context_snippets=context_snippets,
                intent_label=ilabel,
                llm_client=self._llm,
                max_total=cfg.suggested_questions_max,
            )
        self._append_user_with_images(persist_req, original_image_urls=original_image_urls)
        if full:
            self._conv.append_assistant_message(req.user_id, req.session_id, full, rag_citations=rag_citations)
            self._maybe_update_anaphora_slots(req.user_id, req.session_id, full, anaphora_type)
            self._schedule_outline_index(req.user_id, req.session_id)
        yield {
            "type": "finished",
            "meta": {
                "used_rag": bool(context_snippets),
                "used_nl2sql": False,
                "nl2sql_sql": None,
                "intent_label": ilabel,
                "retrieval_attempts": 1 if req.enable_rag else 0,
                "rag_engine": "hybrid" if req.enable_rag else None,
                "status": "answered",
                "duration_ms": duration_ms(),
                "terminate_reason": None,
                "similar_cases_appended": similar_appended,
                "similar_case_namespace": self._chatbot_cfg.similar_case_namespace if similar_appended else None,
                "fault_detect_sources": gate_sources,
                "fault_detect_confidence": gate_conf,
                "need_similar_cases": need_cases,
                "suggested_questions": suggested_out,
                "rag_citations": rag_citations,
                "processed_image_urls": imgs,
                "stream_id": stream_id,
                "history_trim_dropped": llm_build.history_dropped,
                **legacy_ana_meta,
            },
        }

    def _main_llm_max_tokens(self) -> int:
        llm_cfg = get_app_config().llm
        entry = llm_cfg.models.get(llm_cfg.default_model)
        return max(64, int(getattr(entry, "max_tokens", 2048) or 2048))

    def _build_llm_messages(
        self,
        req: ChatRequest,
        history: list[dict],
        context_snippets: list[str],
        *,
        anaphora_type: str | None = None,
        anaphora_rule_type: str | None = None,
        anaphora_slot_bullets: list[str] | None = None,
        rag_citations: list[dict[str, Any]] | None = None,
        intent_label: str = "kb_qa",
        enable_rag: bool = True,
    ) -> ChatbotLlmBuildResult:
        """
        构建发送给 vLLM/OpenAI 兼容接口的 messages，并按上下文预算裁剪历史。

        高分 FAQ 软直通（CHATBOT_FAQ_SOFT_DIRECT_*）：与 LangGraph ``kb_build_messages`` 对齐。
        """
        cfg = self._chatbot_cfg
        faq_decision = evaluate_faq_soft_direct(
            enabled=bool(cfg.faq_soft_direct_enabled),
            min_score=float(cfg.faq_soft_direct_min_score),
            enable_rag=bool(enable_rag),
            intent_label=intent_label,
            anaphora_type=anaphora_type,
            anaphora_rule_type=anaphora_rule_type if anaphora_rule_type is not None else anaphora_type,
            query=req.query,
            rag_citations=rag_citations,
            context_snippets=context_snippets,
        )
        history_for_llm = [] if faq_decision.active else list(history)
        if req.prompt_version:
            tpl = self._prompts.get_template(scene="chatbot", user_id=req.user_id, version=str(req.prompt_version))
        else:
            tpl = self._prompts.get_template(
                scene="chatbot",
                user_id=req.user_id,
                version=None,
                default_version=cfg.default_prompt_version,
            )
        system_chunks: list[str] = []
        if tpl and tpl.content:
            system_chunks.append(tpl.content)
        at = (anaphora_type or "").strip()
        snippets_for_llm = snippets_for_llm_generation(
            context_snippets,
            soft_direct=faq_decision.active,
            snippet_top_n=cfg.faq_soft_direct_snippet_top_n,
        )
        image_urls = [u for u in req.image_urls if isinstance(u, str) and u.strip()]

        def build_messages(hist: list[dict], snippets: list[str]) -> list[dict[str, Any]]:
            chunks = list(system_chunks)
            if cfg.anaphora_anchor_block_enabled and at and at != "none" and not faq_decision.active:
                anchor = build_dialogue_anchor_block(
                    hist,
                    req.query,
                    at,
                    config_path=cfg.anaphora_config_path,
                    max_chars=cfg.anaphora_anchor_max_chars,
                    slot_bullets=anaphora_slot_bullets or [],
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
                query=req.query,
                image_urls=image_urls,
            )

        return trim_history_and_build_chatbot_messages(
            history_for_llm,
            build_messages=build_messages,
            rag_snippets=list(snippets_for_llm),
            context_total_tokens=cfg.llm_context_total_tokens,
            requested_max_tokens=self._main_llm_max_tokens(),
            slack_tokens=cfg.llm_completion_budget_slack_tokens,
            trim_enabled=bool(cfg.history_trim_enabled) and bool(history_for_llm),
            min_keep=cfg.history_trim_min_keep,
        )

    async def _preprocess_request_images(self, req: ChatRequest) -> ChatRequest:
        imgs = [u for u in req.image_urls if isinstance(u, str) and u.strip()]
        if not imgs:
            return req
        new_urls = await self._image_preprocessor.preprocess_urls(imgs)
        return req.model_copy(update={"image_urls": new_urls})

    def _append_user_with_images(self, req: ChatRequest, original_image_urls: list[str] | None = None) -> None:
        content = build_user_message_with_images(
            req.query,
            req.image_urls,
            original_image_urls=self._clean_image_urls(original_image_urls or []),
            processed_image_urls=self._clean_image_urls(req.image_urls),
        )
        self._conv.append_user_message(req.user_id, req.session_id, content)

    @staticmethod
    def _clean_image_urls(image_urls: list[str]) -> list[str]:
        return [u for u in image_urls if isinstance(u, str) and u.strip()]

    async def stop_stream(self, user_id: str, session_id: str, stream_id: str) -> None:
        await self._stream_ctrl.cancel_stream(user_id, session_id, stream_id)

    async def _is_stream_cancelled(self, req: ChatRequest, stream_id: str | None) -> bool:
        if not stream_id:
            return False
        return await self._stream_ctrl.is_cancelled(req.user_id, req.session_id, stream_id)

    async def _apply_structured_reference(self, req: ChatRequest) -> ChatRequest:
        if not self._outline_store.enabled:
            return req
        ref = await self._outline_store.resolve_reference(user_id=req.user_id, session_id=req.session_id, query=req.query)
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

