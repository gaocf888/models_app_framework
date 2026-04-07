from __future__ import annotations

import re
import time
from typing import Any, AsyncIterator, Dict, List, Optional

from app.conversation.manager import ConversationManager
from app.core.config import get_app_config
from app.core.logging import get_logger
from app.llm.client import VLLMHttpClient
from app.llm.langsmith_tracker import LangSmithTracker
from app.llm.prompt_registry import PromptTemplateRegistry
from app.models.chatbot import ChatRequest
from app.rag.agentic import AgenticRAGService, RAGContext, RAGMode
from app.rag.hybrid_rag_service import HybridRAGService
from app.rag.rag_service import RAGService

from .chatbot_graph_state import ChatbotGraphState

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
    ) -> None:
        self._rag = rag_service
        self._hybrid_rag = HybridRAGService(rag_service=rag_service)
        self._agentic_rag = AgenticRAGService(rag_service=rag_service, default_mode=RAGMode.BASIC)
        self._conv = conv_manager
        self._llm = llm_client
        self._prompts = prompt_registry
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
        # 预留分支：先放节点，默认不放量（由 intent 输出标签控制）。
        graph.add_node("unsafe_guard", self._node_unsafe_guard)
        graph.add_node("handoff_human", self._node_handoff_human)
        graph.add_node("smalltalk_generate", self._node_smalltalk_generate)
        graph.add_node("select_rag_engine", self._node_select_rag_engine)
        graph.add_node("kb_retrieve", self._node_kb_retrieve)
        graph.add_node("kb_quality_check", self._node_kb_quality_check)
        graph.add_node("kb_rewrite_query", self._node_kb_rewrite_query)
        graph.add_node("kb_build_messages", self._node_kb_build_messages)
        graph.add_node("clarify_build_response", self._node_clarify_build_response)
        graph.add_node("finalize", self._node_finalize)

        # 入口固定为模板加载：
        # - 保证每轮都有一致 system prompt；
        # - 避免后续节点重复处理“模板缺失”分支。
        graph.set_entry_point("load_prompt_template")
        graph.add_edge("load_prompt_template", "load_history")
        graph.add_edge("load_history", "intent_classify")
        # 意图路由：
        # - 首版仅放开 kb_qa/clarify；
        # - 其它标签（unsafe/handoff/smalltalk）先占位，不在本版本放量。
        graph.add_conditional_edges(
            "intent_classify",
            self._route_by_intent,
            {
                "kb_qa": "select_rag_engine",
                "clarify": "clarify_build_response",
                "unsafe": "unsafe_guard",
                "handoff_human": "handoff_human",
                "smalltalk": "smalltalk_generate",
            },
        )
        graph.add_edge("unsafe_guard", "finalize")
        graph.add_edge("handoff_human", "finalize")
        graph.add_edge("smalltalk_generate", "finalize")
        graph.add_edge("select_rag_engine", "kb_retrieve")
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
            try:
                # 兼容不同版本/发行包命名，优先 redis checkpointer。
                from langgraph.checkpoint.redis import RedisSaver  # type: ignore[import-not-found]
            except Exception as exc:  # noqa: BLE001
                logger.warning("ChatbotLangGraphRunner: redis checkpointer unavailable, fallback none: %s", exc)
                return None
            if not self._checkpoint_redis_url:
                logger.warning("ChatbotLangGraphRunner: redis checkpoint backend selected but URL missing.")
                return None
            try:
                saver = RedisSaver.from_conn_string(self._checkpoint_redis_url)
                logger.info(
                    "ChatbotLangGraphRunner: redis checkpoint enabled namespace=%s",
                    self._checkpoint_namespace,
                )
                return saver
            except Exception as exc:  # noqa: BLE001
                logger.warning("ChatbotLangGraphRunner: redis checkpointer init failed: %s", exc)
                return None
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

    async def run_stream_events(self, req: ChatRequest) -> AsyncIterator[Dict[str, Any]]:
        """
        运行图并输出结构化事件。

        事件类型：
        - delta: 增量文本
        - finished: 完成事件（含 meta）
        """
        # state 在一次请求生命周期内共享；每个节点只增量更新自己负责字段。
        state = self._initial_state(req)
        start_ts = time.perf_counter()
        try:
            state = await self._run_graph(state)
            self._ensure_within_latency(start_ts)
            if state.get("intent_label") == "clarify":
                answer = state.get("answer_text", "").strip()
                self._persist_success(state, req, answer, is_partial=False, terminate_reason=None)
                if answer:
                    yield {"type": "delta", "delta": answer}
                yield {"type": "finished", "meta": self._build_finished_meta(state, start_ts)}
                return

            # clarify 路径不会进入模型生成；会直接输出澄清文案并 finished。
            llm_messages = state.get("llm_messages") or []
            parts: List[str] = []
            async for delta in self._llm.stream_chat(model=None, messages=llm_messages):  # type: ignore[arg-type]
                self._ensure_within_latency(start_ts)
                parts.append(delta)
                state["answer_parts"] = list(parts)
                yield {"type": "delta", "delta": delta}

            answer = "".join(parts).strip()
            self._persist_success(state, req, answer, is_partial=False, terminate_reason=None)
            yield {"type": "finished", "meta": self._build_finished_meta(state, start_ts)}
        except GeneratorExit:
            # 客户端主动断开：
            # - 这是“正常中断”而非服务异常；
            # - 按配置决定是否落 partial，便于下一轮会话续接。
            partial = "".join(state.get("answer_parts") or []).strip()
            self._persist_disconnect(req, partial)
            state["status"] = "aborted"
            state["terminate_reason"] = "client_disconnect"
            raise
        except Exception as exc:  # noqa: BLE001
            logger.exception("ChatbotLangGraphRunner.run_stream failed: %s", exc)
            state["status"] = "failed"
            state["error"] = str(exc)
            self._persist_failure(req)
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
                    outputs=self._build_finished_meta(state, start_ts),
                    metadata={
                        "error": state.get("error"),
                        "prompt_variant": state.get("prompt_variant"),
                        "terminate_reason": state.get("terminate_reason"),
                    },
                )

    def _initial_state(self, req: ChatRequest) -> ChatbotGraphState:
        return {
            "user_id": req.user_id,
            "session_id": req.session_id,
            "query": req.query,
            "image_urls": [u for u in req.image_urls if isinstance(u, str) and u.strip()],
            "enable_rag": bool(req.enable_rag),
            "enable_context": bool(req.enable_context),
            "history_limit": self._history_limit,
            "context_snippets": [],
            "retrieval_score": 0.0,
            "retrieval_attempts": 0,
            "intent_label": "kb_qa",
            "intent_confidence": 0.0,
            "intent_reason": "",
            "status": "started",
            "used_rag": False,
            "error": None,
            "answer_parts": [],
            "answer_text": "",
        }

    def _ensure_within_latency(self, start_ts: float) -> None:
        elapsed_ms = int((time.perf_counter() - start_ts) * 1000)
        if elapsed_ms > self._max_graph_latency_ms:
            raise TimeoutError(
                f"chatbot graph latency budget exceeded: elapsed_ms={elapsed_ms}, budget_ms={self._max_graph_latency_ms}"
            )

    async def _run_graph(self, state: ChatbotGraphState) -> ChatbotGraphState:
        if self._graph is None:
            # langgraph 不可用时退化为顺序执行：
            # - 目标是“可用性优先”，不能因依赖缺失直接中断主业务；
            # - 顺序分支必须与图语义一致，避免线上行为双轨分叉。
            state = await self._node_load_prompt_template(state)
            state = await self._node_load_history(state)
            state = await self._node_intent_classify(state)
            if self._route_by_intent(state) == "clarify":
                state = await self._node_clarify_build_response(state)
                return await self._node_finalize(state)
            state = await self._node_select_rag_engine(state)
            while True:
                state = await self._node_kb_retrieve(state)
                state = await self._node_kb_quality_check(state)
                route = self._route_after_quality_check(state)
                if route == "retry":
                    state = await self._node_kb_rewrite_query(state)
                    continue
                if route == "clarify":
                    state = await self._node_clarify_build_response(state)
                else:
                    state = await self._node_kb_build_messages(state)
                return await self._node_finalize(state)
        return await self._graph.ainvoke(state)

    async def _node_load_prompt_template(self, state: ChatbotGraphState) -> ChatbotGraphState:
        # 模板策略入口：
        # - 继续复用 PromptTemplateRegistry，保持与历史模板策略兼容；
        # - 若模板缺失，使用固定兜底 system_prompt，防止下游节点判空分叉。
        tpl = self._prompts.get_template(scene="chatbot", user_id=state["user_id"], version=None)
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
        # 企业首版采用稳定规则分类，而非 LLM 分类：
        # - 优点：稳定、低成本、可解释；
        # - 缺点：召回面较窄。后续若升级为 LLM 分类器，请保持 label/reason 字段兼容。
        unclear_patterns = [
            r"^怎么弄[啊呀吗呢]?$",
            r"^怎么办[啊呀吗呢]?$",
            r"^啥意思[啊呀吗呢]?$",
            r"^(这个|那个|它).{0,3}(怎么|怎么办|啥意思)",
        ]
        label = "kb_qa"
        reason = "default_kb_qa"
        conf = 0.82
        if len(q) <= 4:
            label, reason, conf = "clarify", "query_too_short", 0.92
        else:
            for p in unclear_patterns:
                if re.search(p, q):
                    label, reason, conf = "clarify", "ambiguous_query_pattern", 0.9
                    break
        if label not in self._intent_output_labels:
            # 未放量标签一律降级到 kb_qa，并保留原因便于观测。
            reason = f"label_not_enabled:{label}|{reason}"
            label = "kb_qa"
            conf = min(conf, 0.6)
        return {"intent_label": label, "intent_reason": reason, "intent_confidence": conf, "status": "intented"}

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

    async def _node_kb_retrieve(self, state: ChatbotGraphState) -> ChatbotGraphState:
        if not state.get("enable_rag", True):
            # 显式关闭 RAG：不检索、分数归零、used_rag=false。
            return {"context_snippets": [], "used_rag": False, "retrieval_score": 0.0}

        attempts = int(state.get("retrieval_attempts", 0)) + 1
        engine = str(state.get("rag_engine") or "hybrid")
        query = str(state.get("query") or "")
        snippets: List[str] = []
        used_agentic = False

        try:
            if engine == "agentic":
                ctx = RAGContext(
                    user_id=state.get("user_id"),
                    session_id=state.get("session_id"),
                    scene="chatbot",
                )
                res = await self._agentic_rag.retrieve(
                    query=query,
                    ctx=ctx,
                    mode=RAGMode.AGENTIC,
                )
                snippets = res.context_snippets or []
                used_agentic = bool(res.used_agentic)
            else:
                snippets = self._hybrid_rag.retrieve(query)
        except Exception as exc:  # noqa: BLE001
            logger.warning("kb_retrieve failed on engine=%s, fallback=%s err=%s", engine, self._rag_fallback, exc)
            if engine != self._rag_fallback and self._rag_fallback == "hybrid":
                snippets = self._hybrid_rag.retrieve(query)
                engine = "hybrid"

        # 轻量质量分（首版）：
        # 命中条数越多分越高。后续可以替换为“分数+覆盖率”混合评分，
        # 但请保持 retrieval_score 的 0~1 语义，避免路由阈值配置失效。
        score = min(1.0, float(len(snippets)) / 6.0) if snippets else 0.0
        return {
            "context_snippets": snippets,
            "used_rag": len(snippets) > 0,
            "retrieval_attempts": attempts,
            "retrieval_score": score,
            "rag_engine": engine,
            "status": "retrieved",
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
        # system prompt -> 检索上下文 -> 历史 -> 当前 user（文本/多模态）
        # 该顺序与历史实现保持一致，可减少迁移后回答风格漂移。
        messages: List[Dict[str, Any]] = []
        system_prompt = str(state.get("system_prompt") or "")
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        snippets = state.get("context_snippets") or []
        if snippets:
            ctx = "\n".join(f"- {c}" for c in snippets)
            messages.append({"role": "system", "content": f"以下是与用户问题相关的知识片段，请优先参考：\n{ctx}"})
        for h in state.get("history_messages") or []:
            role = h.get("role", "user")
            content = h.get("content", "")
            if content:
                messages.append({"role": role, "content": content})
        image_urls = [u for u in (state.get("image_urls") or []) if isinstance(u, str) and u.strip()]
        query = str(state.get("query") or "")
        if image_urls:
            blocks: List[Dict[str, Any]] = [{"type": "text", "text": query}]
            for u in image_urls:
                blocks.append({"type": "image_url", "image_url": {"url": u}})
            messages.append({"role": "user", "content": blocks})
        else:
            messages.append({"role": "user", "content": query})
        return {"llm_messages": messages}

    async def _node_clarify_build_response(self, state: ChatbotGraphState) -> ChatbotGraphState:
        # 首版澄清话术保持稳定输出，后续可替换为模板化/模型化澄清。
        answer = "为了更准确地回答你，请补充更具体的信息：你要咨询的是哪一项业务、当前遇到的具体问题现象，以及你期望的结果。"
        return {"answer_text": answer, "status": "clarifying", "terminate_reason": "need_clarify"}

    async def _node_finalize(self, state: ChatbotGraphState) -> ChatbotGraphState:
        if state.get("status") not in {"clarifying", "failed"}:
            return {"status": "answered"}
        return {}

    def _route_by_intent(self, state: ChatbotGraphState) -> str:
        # 非 clarify 一律按 kb_qa 处理：
        # 这样首版路径稳定，避免“新增标签误命中”导致行为不可预期。
        label = str(state.get("intent_label") or "kb_qa")
        if label == "clarify":
            return "clarify"
        return "kb_qa"

    def _route_after_quality_check(self, state: ChatbotGraphState) -> str:
        # 路由优先级（非常关键）：
        # 1) 关闭 RAG -> build（不走 C-RAG）
        # 2) 低分且可重试 -> retry
        # 3) 低分且预算耗尽 -> clarify（避免继续硬答）
        # 4) 其它 -> build
        if not state.get("enable_rag", True):
            return "build"
        score = float(state.get("retrieval_score", 0.0))
        attempts = int(state.get("retrieval_attempts", 0))
        if self._crag_enabled and score < self._min_score and attempts < self._max_attempts:
            return "retry"
        if score < self._min_score and attempts >= self._max_attempts:
            return "clarify"
        return "build"

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
        self._conv.append_user_message(req.user_id, req.session_id, req.query)
        if answer:
            content = answer if not is_partial else f"[partial] {answer}"
            self._conv.append_assistant_message(req.user_id, req.session_id, content)
        state["answer_text"] = answer
        state["is_partial"] = is_partial
        state["terminate_reason"] = terminate_reason
        state["status"] = "aborted" if is_partial else "answered"

    def _persist_failure(self, req: ChatRequest) -> None:
        # 失败时仍写 user，保证会话线完整；assistant 不写入。
        self._conv.append_user_message(req.user_id, req.session_id, req.query)

    def _persist_disconnect(self, req: ChatRequest, partial: str) -> None:
        self._conv.append_user_message(req.user_id, req.session_id, req.query)
        if self._persist_partial and partial:
            self._conv.append_assistant_message(req.user_id, req.session_id, f"[partial] {partial}")

    def _build_finished_meta(self, state: ChatbotGraphState, start_ts: float) -> Dict[str, Any]:
        # 结束 meta 同时服务于：
        # 1) SSE 最后一帧给前端；
        # 2) LangSmith outputs 聚合。
        # 字段名应尽量保持稳定，避免下游解析兼容性问题。
        return {
            "used_rag": bool(state.get("used_rag", False)),
            "intent_label": state.get("intent_label"),
            "retrieval_attempts": int(state.get("retrieval_attempts", 0)),
            "rag_engine": state.get("rag_engine"),
            "status": state.get("status"),
            "duration_ms": int((time.perf_counter() - start_ts) * 1000),
            "terminate_reason": state.get("terminate_reason"),
        }
