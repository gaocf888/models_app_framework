from __future__ import annotations

import time

from app.conversation.manager import ConversationManager
from app.core.config import get_app_config
from app.core.logging import get_logger
from app.models.chatbot import ChatRequest, ChatResponse
from app.llm.client import VLLMHttpClient
from app.llm.graphs import ChatbotLangGraphRunner
from app.llm.graphs.chatbot_follow_up import build_suggested_questions
from app.llm.graphs.chatbot_intent_rules import classify_chatbot_intent
from app.llm.graphs.chatbot_nl2sql_answer import summarize_nl2sql_with_llm
from app.llm.graphs.chatbot_similar_cases import (
    FaultCaseGateInput,
    format_similar_cases_block,
    retrieve_similar_case_snippets,
    run_fault_case_gate_decision,
)
from app.models.nl2sql import NL2SQLQueryRequest
from app.services.nl2sql_service import NL2SQLService
from app.llm.prompt_registry import PromptTemplateRegistry
from app.rag.hybrid_rag_service import HybridRAGService
from app.rag.rag_service import RAGService
from app.services.chatbot_image_preprocessor import ChatbotImagePreprocessor
from app.services.chatbot_image_utils import build_user_message_with_images, strip_image_block_from_history
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
        self._stream_ctrl = ChatbotStreamControl()
        self._graph_runner = ChatbotLangGraphRunner(
            rag_service=self._rag,
            conv_manager=self._conv,
            llm_client=self._llm,
            prompt_registry=self._prompts,
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

    async def chat(self, req: ChatRequest) -> ChatResponse:
        req = await self._preprocess_request_images(req)
        if not req.user_id:
            raise ValueError("user_id is required (must be provided by the caller).")
        # 不在此处提前 append_user：须先取历史再组 messages，否则当前句已进 history，
        # _build_llm_messages / ChatbotChain 再追加本轮 query，会造成「双份当前用户句」且易干扰多轮理解。
        # 本轮 user/assistant 在得到 answer 后统一写入（见文末）。

        cfg = self._chatbot_cfg
        intent_labels = {x.strip().lower() for x in (cfg.intent_output_labels or []) if x.strip()}
        enable_nl2sql = bool(req.enable_nl2sql_route) and bool(cfg.nl2sql_route_enabled)
        ilabel, _, _ = classify_chatbot_intent(
            req.query,
            enable_nl2sql_route=enable_nl2sql,
            image_urls=[u for u in req.image_urls if isinstance(u, str) and u.strip()],
        )
        if ilabel not in intent_labels:
            ilabel = "kb_qa"

        if ilabel == "data_query":
            nreq = NL2SQLQueryRequest(user_id=req.user_id, session_id=req.session_id, question=req.query)
            nresp = await self._nl2sql.query(nreq, record_conversation=False)
            answer = await summarize_nl2sql_with_llm(
                self._llm,
                user_query=req.query,
                sql=nresp.sql,
                rows=list(nresp.rows or []),
            )
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
            self._append_user_with_images(req)
            self._conv.append_assistant_message(req.user_id, req.session_id, answer)
            return ChatResponse(
                answer=answer,
                used_rag=False,
                used_nl2sql=True,
                intent_label=ilabel,
                suggested_questions=suggested,
                context_snippets=[],
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
            self._append_user_with_images(req)
            self._conv.append_assistant_message(req.user_id, req.session_id, answer)
            return ChatResponse(
                answer=answer,
                used_rag=False,
                used_nl2sql=False,
                intent_label=ilabel,
                suggested_questions=suggested_clarify,
                context_snippets=[],
            )

        # 优先使用 LangChain ChatbotChain（若可用）
        if self._chain is not None:
            answer = await self._chain.run(
                user_id=req.user_id,
                session_id=req.session_id,
                query=req.query,
                enable_rag=req.enable_rag,
                enable_context=req.enable_context,
                prompt_version=req.prompt_version,
            )
            # 目前链路内部已处理 RAG 与上下文，外部仅标记 used_rag 为请求开关
            used_rag = req.enable_rag
            context_snippets = []
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

        self._append_user_with_images(req)
        self._conv.append_assistant_message(req.user_id, req.session_id, answer)

        return ChatResponse(
            answer=answer,
            used_rag=used_rag,
            used_nl2sql=False,
            intent_label=ilabel,
            suggested_questions=suggested_out,
            context_snippets=context_snippets,
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
        req = await self._preprocess_request_images(req)
        if not req.user_id:
            raise ValueError("user_id is required (must be provided by the caller).")
        """
        结构化流式事件输出（供 API 层组装 SSE payload 使用）。

        事件类型：
        - started: {"type": "started", "stream_id": "..."}
        - delta: {"type": "delta", "delta": "..."}
        - finished: {"type": "finished", "meta": {...}}
        """
        stream_id = self._stream_ctrl.begin_stream(req.user_id, req.session_id)
        yield {"type": "started", "stream_id": stream_id}
        # 显式关闭 graph：走 legacy 流式实现，确保开关语义符合部署预期。
        if not self._chatbot_cfg.graph_enabled:
            try:
                async for ev in self._stream_chat_legacy_events(req, stream_id=stream_id):
                    yield ev
                return
            finally:
                await self._stream_ctrl.clear_stream(req.user_id, req.session_id, stream_id)

        try:
            async for ev in self._graph_runner.run_stream_events(
                req,
                stream_id=stream_id,
                cancel_checker=self._stream_ctrl.is_cancelled,
            ):
                yield ev
            return
        except Exception:
            if not self._chatbot_cfg.fallback_legacy_on_error:
                raise
            logger.exception("ChatbotService.stream_chat_events graph failed, fallback to legacy path.")
            async for ev in self._stream_chat_legacy_events(req, stream_id=stream_id):
                yield ev
        finally:
            await self._stream_ctrl.clear_stream(req.user_id, req.session_id, stream_id)

    async def _stream_chat_legacy_events(self, req: ChatRequest, stream_id: str | None = None) -> AsyncIterator[Dict[str, Any]]:
        """
        旧版流式路径（兜底/回退专用）。

        说明：
        - 仅在 graph 关闭或 graph 运行异常且允许回退时启用；
        - 保持与历史行为一致：检索 -> 历史 -> 组 messages -> vLLM stream -> 会话写入；
        - 若启用相似案例扩展，与 LangGraph 路径一致：主回答流结束后追加限定 namespace 检索块。
        """
        start_ts = time.perf_counter()
        cfg = self._chatbot_cfg
        intent_labels = {x.strip().lower() for x in (cfg.intent_output_labels or []) if x.strip()}
        enable_nl2sql = bool(req.enable_nl2sql_route) and bool(cfg.nl2sql_route_enabled)
        imgs = [u for u in req.image_urls if isinstance(u, str) and u.strip()]
        ilabel, _, _ = classify_chatbot_intent(
            req.query,
            enable_nl2sql_route=enable_nl2sql,
            image_urls=imgs,
        )
        if ilabel not in intent_labels:
            ilabel = "kb_qa"

        duration_ms = lambda: int((time.perf_counter() - start_ts) * 1000)

        if ilabel == "data_query":
            nreq = NL2SQLQueryRequest(user_id=req.user_id, session_id=req.session_id, question=req.query)
            nresp = await self._nl2sql.query(nreq, record_conversation=False)
            answer = await summarize_nl2sql_with_llm(
                self._llm,
                user_query=req.query,
                sql=nresp.sql,
                rows=list(nresp.rows or []),
            )
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
            if answer:
                yield {"type": "delta", "delta": answer}
            self._append_user_with_images(req)
            self._conv.append_assistant_message(req.user_id, req.session_id, answer)
            yield {
                "type": "finished",
                "meta": {
                    "used_rag": False,
                    "used_nl2sql": True,
                    "nl2sql_sql": nresp.sql or None,
                    "intent_label": ilabel,
                    "retrieval_attempts": 0,
                    "rag_engine": None,
                    "status": "answered",
                    "duration_ms": duration_ms(),
                    "terminate_reason": None,
                    "similar_cases_appended": False,
                    "similar_case_namespace": None,
                    "fault_detect_sources": [],
                    "fault_detect_confidence": 0.0,
                    "need_similar_cases": False,
                    "suggested_questions": suggested,
                    "processed_image_urls": imgs,
                    "stream_id": stream_id,
                },
            }
            return

        if ilabel == "clarify":
            answer = (
                "为了更准确地回答你，请补充更具体的信息：你要咨询的是哪一项业务、当前遇到的具体问题现象，以及你期望的结果。"
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
            self._append_user_with_images(req)
            self._conv.append_assistant_message(req.user_id, req.session_id, answer)
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
                    "processed_image_urls": imgs,
                    "stream_id": stream_id,
                },
            }
            return

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
        gate_sources: list[str] = []
        gate_conf = 0.0
        need_cases = False
        async for delta in self._llm.stream_chat(model=None, messages=messages):  # type: ignore[arg-type]
            if await self._is_stream_cancelled(req, stream_id):
                partial = "".join(parts).strip()
                self._append_user_with_images(req)
                if self._chatbot_cfg.persist_partial_on_disconnect and partial:
                    self._conv.append_assistant_message(req.user_id, req.session_id, f"[partial] {partial}")
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
                        "processed_image_urls": imgs,
                        "stream_id": stream_id,
                    },
                }
                return
            parts.append(delta)
            yield {"type": "delta", "delta": delta}

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
        self._append_user_with_images(req)
        if full:
            self._conv.append_assistant_message(req.user_id, req.session_id, full)
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
                "processed_image_urls": imgs,
                "stream_id": stream_id,
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
        cfg = self._chatbot_cfg
        if req.prompt_version:
            tpl = self._prompts.get_template(scene="chatbot", user_id=req.user_id, version=str(req.prompt_version))
        else:
            tpl = self._prompts.get_template(
                scene="chatbot",
                user_id=req.user_id,
                version=None,
                default_version=cfg.default_prompt_version,
            )
        if tpl and tpl.content:
            messages.append({"role": "system", "content": tpl.content})
        if context_snippets:
            ctx = "\n".join(f"- {c}" for c in context_snippets)
            messages.append({"role": "system", "content": f"以下是与用户问题相关的知识片段，请优先参考：\n{ctx}"})
        for h in history:
            role = h.get("role", "user")
            raw_c = h.get("content", "")
            content = raw_c if isinstance(raw_c, str) else (str(raw_c) if raw_c is not None else "")
            content = strip_image_block_from_history(content)
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

    async def _preprocess_request_images(self, req: ChatRequest) -> ChatRequest:
        imgs = [u for u in req.image_urls if isinstance(u, str) and u.strip()]
        if not imgs:
            return req
        new_urls = await self._image_preprocessor.preprocess_urls(imgs)
        return req.model_copy(update={"image_urls": new_urls})

    def _append_user_with_images(self, req: ChatRequest) -> None:
        content = build_user_message_with_images(req.query, req.image_urls)
        self._conv.append_user_message(req.user_id, req.session_id, content)

    async def stop_stream(self, user_id: str, session_id: str, stream_id: str) -> None:
        await self._stream_ctrl.cancel_stream(user_id, session_id, stream_id)

    async def _is_stream_cancelled(self, req: ChatRequest, stream_id: str | None) -> bool:
        if not stream_id:
            return False
        return await self._stream_ctrl.is_cancelled(req.user_id, req.session_id, stream_id)

