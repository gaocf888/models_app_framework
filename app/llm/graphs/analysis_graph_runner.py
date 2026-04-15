from __future__ import annotations

"""
综合分析（企业版 V2）编排实现。

- 对外入口：`AnalysisGraphRunner.run_with_payload`、`run_with_nl2sql`（由 `AnalysisService` 调用）。
- 两套 LangGraph `StateGraph`（payload / nl2sql）；`langgraph` 不可用时走 `_run_with_*_sequential`。
- 数据计划：优先 `configs/prompts.yaml` 中 `analysis_plan_<analysis_type>`，可选 LLM 意图/计划合并，最后才用内置默认任务。
"""

import json
import re
from datetime import datetime, timezone
from statistics import median
from time import perf_counter
from uuid import uuid4
from dataclasses import dataclass, field
from typing import Any, cast

from app.conversation.manager import ConversationManager
from app.core.config import get_app_config
from app.core.logging import get_logger
from app.core.metrics import (
    ANALYSIS_DEGRADE_COUNT,
    ANALYSIS_NL2SQL_CALL_COUNT,
    ANALYSIS_NODE_LATENCY,
    ANALYSIS_REQUEST_COUNT,
)
from app.llm.client import VLLMHttpClient
from app.llm.prompt_registry import PromptTemplateRegistry
from pydantic import ValidationError

from app.models.analysis import (
    AnalysisEvidence,
    AnalysisNL2SQLCall,
    AnalysisNL2SQLRequest,
    AnalysisPayloadRequest,
    AnalysisTrace,
    AnalysisV2Result,
)
from app.models.analysis_nl2sql_llm import (
    AnalysisIntentLLMOutput,
    AnalysisPlanLLMOutput,
    AnalysisPlanTaskLLMItem,
    extract_json_object_from_llm_text,
)
from app.models.nl2sql import NL2SQLQueryRequest
from app.rag.hybrid_rag_service import HybridRAGService
from app.services.nl2sql_service import NL2SQLService

logger = get_logger(__name__)


@dataclass
class _PlanTask:
    item_id: str
    purpose: str
    question: str
    mandatory: bool = True
    dependency_ids: list[str] = field(default_factory=list)
    namespace_hint: str | None = None


class AnalysisGraphRunner:
    """
    综合分析编排内核：payload（给定载荷）与 nl2sql（多步查库）共用依赖（会话、LLM、RAG、NL2SQL、提示词）。

    图节点名与 Prometheus `analysis_node_latency_seconds` 的 `node` 标签一致；trace 中 `execution_summary.graph_nodes`
    记录节点顺序；可选 LangGraph checkpoint（`AnalysisConfig.checkpoint_*`）。
    """

    def __init__(
        self,
        *,
        conv_manager: ConversationManager | None = None,
        llm_client: VLLMHttpClient | None = None,
        prompt_registry: PromptTemplateRegistry | None = None,
        hybrid_rag: HybridRAGService | None = None,
        nl2sql_service: NL2SQLService | None = None,
    ) -> None:
        """注入依赖并编译两套图；checkpoint 与图编译失败时自动降级。"""
        self._conv = conv_manager or ConversationManager()
        self._llm = llm_client or VLLMHttpClient()
        self._prompts = prompt_registry or PromptTemplateRegistry()
        self._hybrid_rag = hybrid_rag or HybridRAGService()
        self._nl2sql = nl2sql_service or NL2SQLService(conv_manager=self._conv)
        self._analysis_cfg = get_app_config().analysis
        self._checkpointer = self._build_analysis_checkpointer()
        self._graph_payload = self._build_payload_graph()
        self._graph_nl2sql = self._build_nl2sql_graph()

    @staticmethod
    def _mark_node(node_latency_ms: dict[str, int], node_status: dict[str, str], node: str, started: float, ok: bool) -> None:
        node_latency_ms[node] = int((perf_counter() - started) * 1000)
        node_status[node] = "success" if ok else "failed"

    @staticmethod
    def _safe_doc_id(chunk: Any) -> str:
        meta = getattr(chunk, "metadata", None) or {}
        doc_id = meta.get("doc_id") or meta.get("document_id") or getattr(chunk, "chunk_id", None) or getattr(chunk, "doc_name", None)
        return str(doc_id) if doc_id is not None else ""

    def _retrieve_rag_with_sources(
        self, *, query: str, namespace: str | None, top_k: int, scene: str = "analysis"
    ) -> tuple[list[str], list[dict[str, Any]]]:
        """
        统一 RAG 检索输出：
        - snippets: 供 LLM 使用的文本片段；
        - sources: 审计证据（namespace/doc_id/score）。
        """
        # 优先使用 retrieve_chunks（可拿到 doc_id/score），否则回退 retrieve_context 文本检索。
        rag_svc = getattr(self._hybrid_rag, "_rag_service", None)
        if rag_svc is not None and hasattr(rag_svc, "retrieve_chunks"):
            chunks = rag_svc.retrieve_chunks(query=query, top_k=top_k, namespace=namespace, scene=scene)
            snippets = [getattr(c, "text", "") for c in chunks if getattr(c, "text", "")]
            sources = [
                {
                    "namespace": getattr(c, "namespace", None) or namespace,
                    "doc_id": self._safe_doc_id(c),
                    "score": getattr(c, "score", None),
                }
                for c in chunks
            ]
            return snippets, sources
        try:
            snippets = self._hybrid_rag.retrieve(query, namespace=namespace, top_k=top_k)
        except TypeError:
            snippets = self._hybrid_rag.retrieve(query, namespace=namespace)
        sources = [{"namespace": namespace or "global", "doc_id": "", "score": None} for _ in snippets]
        return list(snippets), sources

    @staticmethod
    def _plan_task_to_dict(t: _PlanTask) -> dict[str, Any]:
        return {
            "item_id": t.item_id,
            "purpose": t.purpose,
            "question": t.question,
            "mandatory": t.mandatory,
            "dependency_ids": t.dependency_ids,
            "namespace_hint": t.namespace_hint,
        }

    @staticmethod
    def _plan_task_from_dict(d: dict[str, Any]) -> _PlanTask:
        return _PlanTask(
            item_id=str(d["item_id"]),
            purpose=str(d["purpose"]),
            question=str(d["question"]),
            mandatory=bool(d.get("mandatory", True)),
            dependency_ids=[str(x) for x in (d.get("dependency_ids") or [])],
            namespace_hint=(str(d["namespace_hint"]).strip() or None) if d.get("namespace_hint") is not None else None,
        )

    def _merge_latency(self, state: dict[str, Any], node: str, ms: int) -> dict[str, int]:
        out = dict(state.get("node_latency_ms") or {})
        out[node] = ms
        return out

    def _merge_status(self, state: dict[str, Any], node: str, status: str) -> dict[str, str]:
        out = dict(state.get("node_status") or {})
        out[node] = status
        return out

    def _payload_graph_input(
        self, req: AnalysisPayloadRequest, *, checkpoint_thread_id: str | None = None
    ) -> dict[str, Any]:
        d: dict[str, Any] = {"payload_request": req.model_dump(mode="json"), "data_mode": "payload"}
        if checkpoint_thread_id:
            d["_checkpoint_thread_id"] = checkpoint_thread_id
        return d

    def _nl2sql_graph_input(
        self, req: AnalysisNL2SQLRequest, *, checkpoint_thread_id: str | None = None
    ) -> dict[str, Any]:
        d: dict[str, Any] = {"nl2sql_request": req.model_dump(mode="json"), "data_mode": "nl2sql"}
        if checkpoint_thread_id:
            d["_checkpoint_thread_id"] = checkpoint_thread_id
        return d

    @staticmethod
    def _norm_question_key(q: str) -> str:
        return re.sub(r"\s+", " ", (q or "").strip().lower())[:200]

    def _extend_tasks_with_hints(self, tasks: list[_PlanTask], req: AnalysisNL2SQLRequest) -> None:
        existing = {t.item_id for t in tasks}
        for i, h in enumerate(req.data_requirements_hint or [], start=1):
            qid = f"h{i}"
            if qid in existing:
                continue
            existing.add(qid)
            tasks.append(
                _PlanTask(
                    item_id=qid,
                    purpose=f"提示补充:{h}",
                    question=f"{req.query}，补充查询与「{h}」直接相关的数据",
                    mandatory=False,
                )
            )

    @staticmethod
    def _apply_plan_context_guide(tasks: list[_PlanTask], plan_context: list[str]) -> None:
        if not plan_context or not tasks:
            return
        guide = "；".join(plan_context[:2])
        for task in tasks:
            task.question = f"{task.question}。请结合以下规则线索：{guide}"

    def _merge_nl2sql_template_and_llm_tasks(
        self,
        template_tasks: list[_PlanTask],
        llm_items: list[AnalysisPlanTaskLLMItem],
        *,
        req: AnalysisNL2SQLRequest,
    ) -> list[_PlanTask]:
        """
        合并规则：JSON 模板任务（含 item_id）优先保留；LLM 任务仅追加「新 item_id」，
        且规范化 question 与任一模板任务相同时视为重复并丢弃。
        """
        out: list[_PlanTask] = list(template_tasks)
        seen_ids = {t.item_id for t in out}
        template_qkeys = {self._norm_question_key(t.question) for t in template_tasks if self._norm_question_key(t.question)}
        for it in llm_items:
            nid = (it.item_id or "").strip()
            if not nid or nid in seen_ids:
                continue
            qk = self._norm_question_key(it.question)
            if qk and qk in template_qkeys:
                continue
            dep_ok = [d for d in (it.dependency_ids or []) if str(d).strip() in seen_ids]
            seen_ids.add(nid)
            out.append(
                _PlanTask(
                    item_id=nid,
                    purpose=it.purpose.strip()[:300] or nid,
                    question=it.question.strip()[:4000],
                    mandatory=bool(it.mandatory),
                    dependency_ids=[str(x).strip() for x in dep_ok],
                )
            )
        self._extend_tasks_with_hints(out, req)
        return out

    async def _nl2sql_run_intent_llm(
        self, req: AnalysisNL2SQLRequest, *, plan_context: list[str]
    ) -> tuple[AnalysisIntentLLMOutput, str, list[str]]:
        """调用「意图」阶段 LLM，返回结构化结果、模板版本号与告警（解析/校验失败时降级为空对象）。"""
        intent_prompt, intent_version = self._resolve_stage_template(
            stage="analysis_intent",
            analysis_type=req.analysis_type,
            user_id=req.user_id,
            default_text="你是一名综合分析规划助手。",
        )
        ctx = "；".join(plan_context[:6]) if plan_context else "无"
        schema = (
            '{"goals":["string"],"key_entities":["string"],"time_scope_hint":"string",'
            '"output_focus":["string"],"data_domains":["string"]}'
        )
        prompt = (
            f"{intent_prompt}\n\n"
            "你必须只输出一个 JSON 对象，不要输出 Markdown 围栏外的解释文字。JSON 必须符合下列字段结构：\n"
            f"{schema}\n\n"
            f"分析类型: {req.analysis_type}\n"
            f"用户问题: {req.query}\n"
            f"可选规则线索（来自 RAG）: {ctx}\n"
        )
        warnings: list[str] = []
        try:
            raw = await self._llm.generate(model=None, prompt=prompt)  # type: ignore[arg-type]
        except Exception:  # noqa: BLE001
            logger.exception("analysis intent llm call failed")
            warnings.append("intent_llm_http_failed")
            return AnalysisIntentLLMOutput(), intent_version, warnings
        obj = extract_json_object_from_llm_text(raw or "")
        if obj is None:
            warnings.append("intent_llm_json_parse_failed")
            return AnalysisIntentLLMOutput(), intent_version, warnings
        try:
            return AnalysisIntentLLMOutput.model_validate(obj), intent_version, warnings
        except ValidationError:
            warnings.append("intent_llm_validation_failed")
            return AnalysisIntentLLMOutput(), intent_version, warnings

    async def _nl2sql_run_plan_llm(
        self,
        req: AnalysisNL2SQLRequest,
        *,
        intent: AnalysisIntentLLMOutput,
        plan_context: list[str],
    ) -> tuple[AnalysisPlanLLMOutput | None, str, list[str]]:
        """调用「数据计划」阶段 LLM，产出 tasks 列表；失败时返回 None 与告警。"""
        data_plan_prompt, data_plan_version = self._resolve_stage_template(
            stage="analysis_data_plan",
            analysis_type=req.analysis_type,
            user_id=req.user_id,
            default_text="请先明确本次分析所需数据域与依赖关系。",
        )
        ctx = "；".join(plan_context[:8]) if plan_context else "无"
        intent_blob = intent.model_dump()
        schema = (
            '{"tasks":[{"item_id":"q1","purpose":"...","question":"自然语言问句","mandatory":true,"dependency_ids":[]}]}'
        )
        prompt = (
            f"{data_plan_prompt}\n\n"
            "你必须只输出一个 JSON 对象。顶层键 tasks 为数组；每项含 item_id、purpose、question、mandatory、dependency_ids。\n"
            "item_id 仅使用字母数字下划线与中划线；dependency_ids 必须指向已声明的 item_id。\n"
            f"结构示例: {schema}\n\n"
            f"分析类型: {req.analysis_type}\n"
            f"用户问题: {req.query}\n"
            f"意图阶段结构化结果(JSON): {json.dumps(intent_blob, ensure_ascii=False)[:3500]}\n"
            f"规则线索: {ctx}\n"
        )
        warnings: list[str] = []
        try:
            raw = await self._llm.generate(model=None, prompt=prompt)  # type: ignore[arg-type]
        except Exception:  # noqa: BLE001
            logger.exception("analysis plan llm call failed")
            warnings.append("plan_llm_http_failed")
            return None, data_plan_version, warnings
        obj = extract_json_object_from_llm_text(raw or "")
        if obj is None:
            warnings.append("plan_llm_json_parse_failed")
            return None, data_plan_version, warnings
        try:
            return AnalysisPlanLLMOutput.model_validate(obj), data_plan_version, warnings
        except ValidationError:
            warnings.append("plan_llm_validation_failed")
            return None, data_plan_version, warnings

    async def _nl2sql_merge_plan_tasks(
        self,
        req: AnalysisNL2SQLRequest,
        *,
        plan_context: list[str],
        llm_plan: AnalysisPlanLLMOutput | None,
    ) -> tuple[list[_PlanTask], list[str]]:
        """模板 JSON 优先，LLM 补充去重；若最终为空则回退 _build_data_plan。"""
        warnings: list[str] = []
        template_only = self._build_data_plan_from_template(req, plan_context=[])
        llm_items: list[AnalysisPlanTaskLLMItem] = list((llm_plan.tasks if llm_plan else []) or [])[
            : req.options.max_nl2sql_calls + 8
        ]
        merged: list[_PlanTask]
        if template_only:
            merged = self._merge_nl2sql_template_and_llm_tasks(template_only, llm_items, req=req)
            if not llm_items:
                warnings.append("plan_llm_no_tasks_template_only")
        else:
            if llm_items:
                merged = self._merge_nl2sql_template_and_llm_tasks([], llm_items, req=req)
            else:
                merged = list(self._build_data_plan(req, plan_context=[]))
                warnings.append("plan_fallback_rules_default")
        self._apply_plan_context_guide(merged, plan_context)
        merged = merged[: req.options.max_nl2sql_calls]
        if not merged:
            merged = self._build_data_plan(req, plan_context=plan_context)
            warnings.append("plan_merge_empty_full_fallback")
        return merged, warnings

    def _build_analysis_checkpointer(self):
        """
        LangGraph checkpoint（可选），语义与 Chatbot 一致：
        - none：不启用；
        - memory：进程内（开发/测试）；
        - redis：需 ANALYSIS_CHECKPOINT_REDIS_URL；依赖缺失或初始化失败时返回 None。
        """
        backend = (self._analysis_cfg.checkpoint_backend or "none").lower()
        if backend == "none":
            return None
        if backend == "memory":
            try:
                from langgraph.checkpoint.memory import MemorySaver  # type: ignore[import-not-found]

                logger.info("AnalysisGraphRunner: memory checkpoint enabled.")
                return MemorySaver()
            except Exception as exc:  # noqa: BLE001
                logger.warning("AnalysisGraphRunner: memory checkpointer unavailable: %s", exc)
                return None
        if backend == "redis":
            try:
                from langgraph.checkpoint.redis import RedisSaver  # type: ignore[import-not-found]
            except Exception as exc:  # noqa: BLE001
                logger.warning("AnalysisGraphRunner: redis checkpointer unavailable, fallback none: %s", exc)
                return None
            url = (self._analysis_cfg.checkpoint_redis_url or "").strip()
            if not url:
                logger.warning("AnalysisGraphRunner: redis checkpoint backend selected but URL missing.")
                return None
            try:
                saver = RedisSaver.from_conn_string(url)
                logger.info(
                    "AnalysisGraphRunner: redis checkpoint enabled namespace=%s",
                    self._analysis_cfg.checkpoint_namespace,
                )
                return saver
            except Exception as exc:  # noqa: BLE001
                logger.warning("AnalysisGraphRunner: redis checkpointer init failed: %s", exc)
                return None
        logger.warning("AnalysisGraphRunner: unknown checkpoint backend=%s, disable checkpoint.", backend)
        return None

    def _graph_trace_checkpoint_extras(self, state: dict[str, Any]) -> dict[str, Any]:
        """写入 trace.execution_summary 的 checkpoint 元数据（仅 LangGraph 路径）。"""
        if self._checkpointer is None:
            return {}
        tid = state.get("_checkpoint_thread_id")
        out: dict[str, Any] = {
            "checkpoint_backend": self._analysis_cfg.checkpoint_backend,
            "checkpoint_namespace": self._analysis_cfg.checkpoint_namespace,
        }
        if isinstance(tid, str) and tid.strip():
            out["checkpoint_thread_id"] = tid.strip()
        return out

    def _build_payload_graph(self):
        """编译 payload 线性图：normalize_request → rag_enrichment → data_quality_gate → synthesis → finalize。"""
        try:
            from langgraph.graph import END, StateGraph  # type: ignore[import-not-found]
        except Exception as exc:  # noqa: BLE001
            logger.warning("AnalysisGraphRunner: langgraph unavailable, payload graph disabled. err=%s", exc)
            return None
        g = StateGraph(dict)
        g.add_node("normalize_request", self._lg_payload_normalize_request)
        g.add_node("rag_enrichment", self._lg_payload_rag_enrichment)
        g.add_node("data_quality_gate", self._lg_payload_data_quality_gate)
        g.add_node("synthesis", self._lg_payload_synthesis)
        g.add_node("finalize", self._lg_payload_finalize)
        g.set_entry_point("normalize_request")
        g.add_edge("normalize_request", "rag_enrichment")
        g.add_edge("rag_enrichment", "data_quality_gate")
        g.add_edge("data_quality_gate", "synthesis")
        g.add_edge("synthesis", "finalize")
        g.add_edge("finalize", END)
        if self._checkpointer is not None:
            return g.compile(checkpointer=self._checkpointer)
        return g.compile()

    def _build_nl2sql_graph(self):
        try:
            from langgraph.graph import END, StateGraph  # type: ignore[import-not-found]
        except Exception as exc:  # noqa: BLE001
            logger.warning("AnalysisGraphRunner: langgraph unavailable, nl2sql graph disabled. err=%s", exc)
            return None
        g = StateGraph(dict)
        g.add_node("normalize_request", self._lg_nl2sql_normalize_request)
        g.add_node("plan_context_rag", self._lg_nl2sql_plan_context_rag)
        g.add_node("intent_llm", self._lg_nl2sql_intent_llm)
        g.add_node("plan_llm", self._lg_nl2sql_plan_llm_merge)
        g.add_node("acquire_data", self._lg_nl2sql_acquire_data)
        g.add_node("data_quality_gate", self._lg_nl2sql_data_quality_gate)
        g.add_node("rag_enrichment", self._lg_nl2sql_rag_enrichment)
        g.add_node("synthesis", self._lg_nl2sql_synthesis)
        g.add_node("finalize", self._lg_nl2sql_finalize)
        g.set_entry_point("normalize_request")
        g.add_edge("normalize_request", "plan_context_rag")
        g.add_edge("plan_context_rag", "intent_llm")
        g.add_edge("intent_llm", "plan_llm")
        g.add_edge("plan_llm", "acquire_data")
        g.add_edge("acquire_data", "data_quality_gate")
        g.add_edge("data_quality_gate", "rag_enrichment")
        g.add_edge("rag_enrichment", "synthesis")
        g.add_edge("synthesis", "finalize")
        g.add_edge("finalize", END)
        if self._checkpointer is not None:
            return g.compile(checkpointer=self._checkpointer)
        return g.compile()

    async def _lg_payload_normalize_request(self, state: dict[str, Any]) -> dict[str, Any]:
        """图节点：写入 request_id/plan_id、会话用户消息、初始 node_latency。"""
        req = AnalysisPayloadRequest.model_validate(state["payload_request"])
        t0 = perf_counter()
        self._conv.append_user_message(req.user_id, req.session_id, req.query)
        ms = int((perf_counter() - t0) * 1000)
        return {
            "request_id": f"anl_{uuid4().hex[:12]}",
            "plan_id": f"plan_{uuid4().hex[:10]}",
            "user_id": req.user_id,
            "session_id": req.session_id,
            "analysis_type": req.analysis_type,
            "query": req.query,
            "options": req.options.model_dump(mode="json"),
            "input_payload": req.payload,
            "degrade_reasons": [],
            "node_latency_ms": self._merge_latency(state, "normalize_request", ms),
            "node_status": self._merge_status(state, "normalize_request", "success"),
        }

    async def _lg_payload_rag_enrichment(self, state: dict[str, Any]) -> dict[str, Any]:
        req = AnalysisPayloadRequest.model_validate(state["payload_request"])
        at = req.analysis_type
        context_snippets: list[str] = []
        rag_sources: list[dict[str, Any]] = []
        used_rag = False
        if req.options.enable_rag:
            t_rag = perf_counter()
            context_snippets, rag_sources = self._retrieve_business_rag(req.query, at)
            used_rag = len(context_snippets) > 0
            ms = int((perf_counter() - t_rag) * 1000)
            ANALYSIS_NODE_LATENCY.labels(node="rag_enrichment", analysis_type=at).observe(perf_counter() - t_rag)
            return {
                "context_snippets": context_snippets,
                "rag_sources": rag_sources,
                "used_rag": used_rag,
                "node_latency_ms": self._merge_latency(state, "rag_enrichment", ms),
                "node_status": self._merge_status(state, "rag_enrichment", "success"),
            }
        return {
            "context_snippets": [],
            "rag_sources": [],
            "used_rag": False,
            "node_latency_ms": self._merge_latency(state, "rag_enrichment", 0),
            "node_status": self._merge_status(state, "rag_enrichment", "success"),
        }

    async def _lg_payload_data_quality_gate(self, state: dict[str, Any]) -> dict[str, Any]:
        """图节点：payload 质量闸门；strict 且阈值失败时由上层捕获为业务错误。"""
        req = AnalysisPayloadRequest.model_validate(state["payload_request"])
        at = req.analysis_type
        t_quality = perf_counter()
        quality_report = self._evaluate_payload_quality(req.payload, at)
        ms = int((perf_counter() - t_quality) * 1000)
        ANALYSIS_NODE_LATENCY.labels(node="data_quality_gate", analysis_type=at).observe(perf_counter() - t_quality)
        degrade = list(state.get("degrade_reasons") or [])
        if req.options.strict and quality_report.get("threshold_result", {}).get("failed", False):
            ANALYSIS_DEGRADE_COUNT.labels(reason="strict_payload_quality_blocked").inc()
            degrade.append("strict_payload_quality_blocked")
            raise ValueError("strict mode enabled: payload quality is insufficient for analysis")
        return {
            "quality_report": quality_report,
            "degrade_reasons": degrade,
            "node_latency_ms": self._merge_latency(state, "data_quality_gate", ms),
            "node_status": self._merge_status(state, "data_quality_gate", "success"),
        }

    async def _lg_payload_synthesis(self, state: dict[str, Any]) -> dict[str, Any]:
        """图节点：LLM 综合 + 结构化报告 + 建议列表（无 NL2SQL）。"""
        req = AnalysisPayloadRequest.model_validate(state["payload_request"])
        at = req.analysis_type
        t_syn = perf_counter()
        synthesis_prompt, synthesis_version = self._resolve_stage_template(
            stage="analysis_synthesis",
            analysis_type=at,
            user_id=req.user_id,
            default_text="你是一名综合分析助手，请基于事实数据给出结论和建议。",
        )
        _intent_prompt, intent_version = self._resolve_stage_template(
            stage="analysis_intent",
            analysis_type=at,
            user_id=req.user_id,
            default_text="你是一名综合分析规划助手。",
        )
        _data_plan_prompt, data_plan_version = self._resolve_stage_template(
            stage="analysis_data_plan",
            analysis_type=at,
            user_id=req.user_id,
            default_text="请先明确本次分析所需数据域与依赖关系。",
        )
        _report_prompt, report_version = self._resolve_stage_template(
            stage="analysis_report",
            analysis_type=at,
            user_id=req.user_id,
            default_text="请输出结构化报告，包含结论、依据、建议。",
        )
        _ = (_intent_prompt, _data_plan_prompt, _report_prompt)
        context_snippets = list(state.get("context_snippets") or [])
        quality_report = cast(dict[str, Any], state.get("quality_report") or {})
        summary = await self._generate_summary(
            query=req.query,
            analysis_type=at,
            data_mode="payload",
            data_blob=req.payload,
            context_snippets=context_snippets,
            system_prompt=synthesis_prompt,
        )
        suggestions = self._build_suggestions(summary, at, req.options.max_suggestions)
        structured_report = self._build_structured_report(
            summary=summary,
            suggestions=suggestions,
            analysis_type=at,
            report_style=req.options.report_style,
            report_template=req.options.report_template,
            chart_mode=req.options.chart_mode,
            data_coverage={
                "mode": "payload",
                "payload_fields": len(req.payload.keys()),
                "completeness": quality_report.get("completeness", 0.0),
                "records": self._extract_records_from_payload(req.payload),
            },
        )
        ms = int((perf_counter() - t_syn) * 1000)
        ANALYSIS_NODE_LATENCY.labels(node="synthesis", analysis_type=at).observe(perf_counter() - t_syn)
        return {
            "summary": summary,
            "structured_report": structured_report,
            "suggestions": suggestions,
            "template_versions": {
                "intent": intent_version,
                "data_plan": data_plan_version,
                "synthesis": synthesis_version,
                "report": report_version,
            },
            "node_latency_ms": self._merge_latency(state, "synthesis", ms),
            "node_status": self._merge_status(state, "synthesis", "success"),
        }

    async def _lg_payload_finalize(self, state: dict[str, Any]) -> dict[str, Any]:
        """图节点：组装 AnalysisV2Result、trace、会话助手消息，写入 v2_result。"""
        req = AnalysisPayloadRequest.model_validate(state["payload_request"])
        summary = str(state.get("summary") or "")
        quality_report = cast(dict[str, Any], state.get("quality_report") or {})
        used_rag = bool(state.get("used_rag"))
        rag_sources = list(state.get("rag_sources") or [])
        self._conv.append_assistant_message(req.user_id, req.session_id, summary)
        evidence = AnalysisEvidence(
            used_rag=used_rag,
            rag_sources=rag_sources[:32],
            nl2sql_calls=[],
            data_coverage={
                "mode": "payload",
                "input_keys": list(req.payload.keys()),
                "data_quality_report": quality_report,
            },
        )
        trace = AnalysisTrace(
            plan_id=str(state.get("plan_id") or ""),
            node_latency_ms=dict(state.get("node_latency_ms") or {}),
            template_versions=dict(state.get("template_versions") or {}),
            execution_summary={
                "analysis_type": req.analysis_type,
                "data_mode": "payload",
                "started_at": datetime.now(timezone.utc).isoformat(),
                "used_rag": used_rag,
                "orchestrator": "langgraph",
                "graph_nodes": [
                    "normalize_request",
                    "rag_enrichment",
                    "data_quality_gate",
                    "synthesis",
                    "report_builder",
                    "finalize",
                ],
                **self._graph_trace_checkpoint_extras(state),
            },
            node_status=dict(state.get("node_status") or {}),
            data_plan_trace=[],
            degrade_reasons=list(state.get("degrade_reasons") or []),
        )
        result = AnalysisV2Result(
            request_id=str(state.get("request_id") or ""),
            analysis_type=req.analysis_type,
            summary=summary,
            structured_report=cast(dict[str, Any], state.get("structured_report") or {}),
            evidence=evidence,
            trace=trace,
        )
        return {"v2_result": result}

    async def _lg_nl2sql_normalize_request(self, state: dict[str, Any]) -> dict[str, Any]:
        """图节点：与 payload 分支类似，写入 nl2sql 请求快照与 request_id。"""
        req = AnalysisNL2SQLRequest.model_validate(state["nl2sql_request"])
        t0 = perf_counter()
        self._conv.append_user_message(req.user_id, req.session_id, req.query)
        ms = int((perf_counter() - t0) * 1000)
        return {
            "request_id": f"anl_{uuid4().hex[:12]}",
            "plan_id": f"plan_{uuid4().hex[:10]}",
            "user_id": req.user_id,
            "session_id": req.session_id,
            "analysis_type": req.analysis_type,
            "query": req.query,
            "options": req.options.model_dump(mode="json"),
            "degrade_reasons": [],
            "node_latency_ms": self._merge_latency(state, "normalize_request", ms),
            "node_status": self._merge_status(state, "normalize_request", "success"),
        }

    async def _lg_nl2sql_plan_context_rag(self, state: dict[str, Any]) -> dict[str, Any]:
        """图节点：规划前 RAG（scene=nl2sql），写入 plan_context / plan_rag_sources。"""
        req = AnalysisNL2SQLRequest.model_validate(state["nl2sql_request"])
        at = req.analysis_type
        t0 = perf_counter()
        plan_context, plan_rag_sources = self._retrieve_plan_rag(req.query, at, req.options.enable_rag)
        ms = int((perf_counter() - t0) * 1000)
        ANALYSIS_NODE_LATENCY.labels(node="plan_context_rag", analysis_type=at).observe(perf_counter() - t0)
        return {
            "plan_context": plan_context,
            "plan_rag_sources": plan_rag_sources,
            "planner_warnings": list(state.get("planner_warnings") or []),
            "node_latency_ms": self._merge_latency(state, "plan_context_rag", ms),
            "node_status": self._merge_status(state, "plan_context_rag", "success"),
        }

    async def _lg_nl2sql_intent_llm(self, state: dict[str, Any]) -> dict[str, Any]:
        req = AnalysisNL2SQLRequest.model_validate(state["nl2sql_request"])
        at = req.analysis_type
        t0 = perf_counter()
        plan_context = list(state.get("plan_context") or [])
        warns: list[str] = list(state.get("planner_warnings") or [])
        if not self._analysis_cfg.nl2sql_llm_planner_enabled:
            _, intent_version = self._resolve_stage_template(
                stage="analysis_intent",
                analysis_type=at,
                user_id=req.user_id,
                default_text="你是一名综合分析规划助手。",
            )
            intent = AnalysisIntentLLMOutput()
            warns.append("nl2sql_planner_disabled")
        else:
            intent, intent_version, w2 = await self._nl2sql_run_intent_llm(req, plan_context=plan_context)
            warns.extend(w2)
        ms = int((perf_counter() - t0) * 1000)
        ANALYSIS_NODE_LATENCY.labels(node="intent_llm", analysis_type=at).observe(perf_counter() - t0)
        return {
            "intent_llm_result": intent.model_dump(mode="json"),
            "intent_version": intent_version,
            "planner_warnings": warns,
            "node_latency_ms": self._merge_latency(state, "intent_llm", ms),
            "node_status": self._merge_status(state, "intent_llm", "success"),
        }

    async def _lg_nl2sql_plan_llm_merge(self, state: dict[str, Any]) -> dict[str, Any]:
        """图节点：合并模板与 LLM 计划，写入 plan_tasks，受 max_nl2sql_calls 截断。"""
        req = AnalysisNL2SQLRequest.model_validate(state["nl2sql_request"])
        at = req.analysis_type
        t0 = perf_counter()
        plan_context = list(state.get("plan_context") or [])
        warns: list[str] = list(state.get("planner_warnings") or [])
        intent_raw = state.get("intent_llm_result") or {}
        try:
            intent_obj = AnalysisIntentLLMOutput.model_validate(intent_raw)
        except ValidationError:
            intent_obj = AnalysisIntentLLMOutput()
            warns.append("intent_state_invalid")
        if not self._analysis_cfg.nl2sql_llm_planner_enabled:
            _, data_plan_version = self._resolve_stage_template(
                stage="analysis_data_plan",
                analysis_type=at,
                user_id=req.user_id,
                default_text="请先明确本次分析所需数据域与依赖关系。",
            )
            tasks = self._build_data_plan(req, plan_context=plan_context)
            tasks = tasks[: req.options.max_nl2sql_calls]
        else:
            llm_plan, data_plan_version, w2 = await self._nl2sql_run_plan_llm(req, intent=intent_obj, plan_context=plan_context)
            warns.extend(w2)
            tasks, w3 = await self._nl2sql_merge_plan_tasks(req, plan_context=plan_context, llm_plan=llm_plan)
            warns.extend(w3)
        ms = int((perf_counter() - t0) * 1000)
        ANALYSIS_NODE_LATENCY.labels(node="plan_llm", analysis_type=at).observe(perf_counter() - t0)
        return {
            "data_plan_version": data_plan_version,
            "plan_tasks": [self._plan_task_to_dict(t) for t in tasks],
            "planner_warnings": warns,
            "node_latency_ms": self._merge_latency(state, "plan_llm", ms),
            "node_status": self._merge_status(state, "plan_llm", "success"),
        }

    async def _lg_nl2sql_acquire_data(self, state: dict[str, Any]) -> dict[str, Any]:
        """图节点：按 plan_tasks 调用 NL2SQL，填充 gathered_data / nl2sql_calls。"""
        req = AnalysisNL2SQLRequest.model_validate(state["nl2sql_request"])
        raw_tasks = list(state.get("plan_tasks") or [])
        tasks = [self._plan_task_from_dict(x) for x in raw_tasks if isinstance(x, dict)]
        nl2sql_calls, gathered_data, task_status, acquire_latency_ms = await self._execute_data_plan(req=req, tasks=tasks)
        return {
            "nl2sql_calls": [c.model_dump(mode="json") for c in nl2sql_calls],
            "gathered_data": gathered_data,
            "task_status": task_status,
            "acquire_latency_ms": acquire_latency_ms,
            "node_latency_ms": self._merge_latency(state, "acquire_data", acquire_latency_ms),
            "node_status": self._merge_status(state, "acquire_data", "success"),
        }

    async def _lg_nl2sql_data_quality_gate(self, state: dict[str, Any]) -> dict[str, Any]:
        """图节点：基于取数结果与阈值做 nl2sql 质量评估；strict 失败抛错。"""
        req = AnalysisNL2SQLRequest.model_validate(state["nl2sql_request"])
        at = req.analysis_type
        calls_raw = list(state.get("nl2sql_calls") or [])
        calls = [AnalysisNL2SQLCall.model_validate(x) for x in calls_raw if isinstance(x, dict)]
        gathered_data = cast(dict[str, list[dict]], state.get("gathered_data") or {})
        task_status = cast(dict[str, str], state.get("task_status") or {})
        t_quality = perf_counter()
        quality_report = self._evaluate_nl2sql_quality(
            calls,
            gathered_data,
            analysis_type=at,
            task_status=task_status,
        )
        ms = int((perf_counter() - t_quality) * 1000)
        ANALYSIS_NODE_LATENCY.labels(node="data_quality_gate", analysis_type=at).observe(perf_counter() - t_quality)
        degrade = list(state.get("degrade_reasons") or [])
        if quality_report.get("mandatory_failed", 0) > 0:
            degrade.append("mandatory_steps_failed")
        if req.options.strict and quality_report.get("threshold_result", {}).get("failed", False):
            ANALYSIS_DEGRADE_COUNT.labels(reason="strict_nl2sql_quality_blocked").inc()
            degrade.append("strict_nl2sql_quality_blocked")
            raise ValueError("strict mode enabled: NL2SQL data quality thresholds not met")
        return {
            "quality_report": quality_report,
            "degrade_reasons": degrade,
            "node_latency_ms": self._merge_latency(state, "data_quality_gate", ms),
            "node_status": self._merge_status(state, "data_quality_gate", "success"),
        }

    async def _lg_nl2sql_rag_enrichment(self, state: dict[str, Any]) -> dict[str, Any]:
        """图节点：取数后的业务解释 RAG（scene=analysis），写入 context_snippets。"""
        req = AnalysisNL2SQLRequest.model_validate(state["nl2sql_request"])
        at = req.analysis_type
        context_snippets: list[str] = []
        biz_rag_sources: list[dict[str, Any]] = []
        used_rag = False
        if req.options.enable_rag:
            t_rag = perf_counter()
            context_snippets, biz_rag_sources = self._retrieve_business_rag(req.query, at)
            used_rag = len(context_snippets) > 0
            ms = int((perf_counter() - t_rag) * 1000)
            ANALYSIS_NODE_LATENCY.labels(node="rag_enrichment", analysis_type=at).observe(perf_counter() - t_rag)
            plan_src = list(state.get("plan_rag_sources") or [])
            merged_sources = (plan_src + biz_rag_sources)[:64]
            return {
                "context_snippets": context_snippets,
                "rag_sources": merged_sources,
                "used_rag": used_rag,
                "node_latency_ms": self._merge_latency(state, "rag_enrichment", ms),
                "node_status": self._merge_status(state, "rag_enrichment", "success"),
            }
        plan_src = list(state.get("plan_rag_sources") or [])
        return {
            "context_snippets": [],
            "rag_sources": plan_src[:64],
            "used_rag": False,
            "node_latency_ms": self._merge_latency(state, "rag_enrichment", 0),
            "node_status": self._merge_status(state, "rag_enrichment", "success"),
        }

    async def _lg_nl2sql_synthesis(self, state: dict[str, Any]) -> dict[str, Any]:
        req = AnalysisNL2SQLRequest.model_validate(state["nl2sql_request"])
        at = req.analysis_type
        t_syn = perf_counter()
        synthesis_prompt, synthesis_version = self._resolve_stage_template(
            stage="analysis_synthesis",
            analysis_type=at,
            user_id=req.user_id,
            default_text="你是一名综合分析助手，请基于事实数据给出结论和建议。",
        )
        _report_prompt, report_version = self._resolve_stage_template(
            stage="analysis_report",
            analysis_type=at,
            user_id=req.user_id,
            default_text="请输出结构化报告，包含结论、依据、建议。",
        )
        _ = _report_prompt
        gathered_data = cast(dict[str, list[dict]], state.get("gathered_data") or {})
        calls_raw = list(state.get("nl2sql_calls") or [])
        calls = [AnalysisNL2SQLCall.model_validate(x) for x in calls_raw if isinstance(x, dict)]
        raw_tasks = list(state.get("plan_tasks") or [])
        tasks = [self._plan_task_from_dict(x) for x in raw_tasks if isinstance(x, dict)]
        context_snippets = list(state.get("context_snippets") or [])
        planning_ctx: str | None = None
        if self._analysis_cfg.nl2sql_llm_planner_enabled:
            ir = state.get("intent_llm_result")
            if isinstance(ir, dict):
                planning_ctx = json.dumps(ir, ensure_ascii=False)
        summary = await self._generate_summary(
            query=req.query,
            analysis_type=at,
            data_mode="nl2sql",
            data_blob=gathered_data,
            context_snippets=context_snippets,
            system_prompt=synthesis_prompt,
            planning_context=planning_ctx,
        )
        suggestions = self._build_suggestions(summary, at, req.options.max_suggestions)
        quality_report = cast(dict[str, Any], state.get("quality_report") or {})
        structured_report = self._build_structured_report(
            summary=summary,
            suggestions=suggestions,
            analysis_type=at,
            report_style=req.options.report_style,
            report_template=req.options.report_template,
            chart_mode=req.options.chart_mode,
            data_coverage={
                "mode": "nl2sql",
                "planned_calls": len(tasks),
                "success_calls": sum(1 for c in calls if c.status == "success"),
                "failed_calls": sum(1 for c in calls if c.status == "failed"),
                "skipped_calls": sum(1 for c in calls if c.status == "skipped"),
                "records": self._extract_records_from_gathered(gathered_data),
            },
        )
        ms = int((perf_counter() - t_syn) * 1000)
        ANALYSIS_NODE_LATENCY.labels(node="synthesis", analysis_type=at).observe(perf_counter() - t_syn)
        return {
            "summary": summary,
            "structured_report": structured_report,
            "suggestions": suggestions,
            "synthesis_version": synthesis_version,
            "report_version": report_version,
            "node_latency_ms": self._merge_latency(state, "synthesis", ms),
            "node_status": self._merge_status(state, "synthesis", "success"),
        }

    async def _lg_nl2sql_finalize(self, state: dict[str, Any]) -> dict[str, Any]:
        """图节点：组装 evidence、含 nl2sql 与规划告警的 trace，写入 v2_result。"""
        req = AnalysisNL2SQLRequest.model_validate(state["nl2sql_request"])
        summary = str(state.get("summary") or "")
        calls_raw = list(state.get("nl2sql_calls") or [])
        calls = [AnalysisNL2SQLCall.model_validate(x) for x in calls_raw if isinstance(x, dict)]
        gathered_data = cast(dict[str, list[dict]], state.get("gathered_data") or {})
        raw_tasks = list(state.get("plan_tasks") or [])
        tasks = [self._plan_task_from_dict(x) for x in raw_tasks if isinstance(x, dict)]
        quality_report = cast(dict[str, Any], state.get("quality_report") or {})
        used_rag = bool(state.get("used_rag"))
        rag_sources_state = list(state.get("rag_sources") or [])
        self._conv.append_assistant_message(req.user_id, req.session_id, summary)
        evidence = AnalysisEvidence(
            used_rag=used_rag,
            rag_sources=rag_sources_state[:64],
            nl2sql_calls=calls,
            data_coverage={
                "mode": "nl2sql",
                "planned_calls": len(tasks),
                "success_calls": sum(1 for c in calls if c.status == "success"),
                "failed_calls": sum(1 for c in calls if c.status == "failed"),
                "skipped_calls": sum(1 for c in calls if c.status == "skipped"),
                "data_quality_report": quality_report,
            },
        )
        trace = AnalysisTrace(
            plan_id=str(state.get("plan_id") or ""),
            node_latency_ms=dict(state.get("node_latency_ms") or {}),
            template_versions={
                "intent": str(state.get("intent_version") or ""),
                "data_plan": str(state.get("data_plan_version") or ""),
                "synthesis": str(state.get("synthesis_version") or ""),
                "report": str(state.get("report_version") or ""),
            },
            execution_summary={
                "analysis_type": req.analysis_type,
                "data_mode": "nl2sql",
                "started_at": datetime.now(timezone.utc).isoformat(),
                "used_rag": used_rag,
                "planned_calls": len(tasks),
                "orchestrator": "langgraph",
                "graph_nodes": [
                    "normalize_request",
                    "plan_context_rag",
                    "intent_llm",
                    "plan_llm",
                    "acquire_data",
                    "data_quality_gate",
                    "rag_enrichment",
                    "synthesis",
                    "report_builder",
                    "finalize",
                ],
                "planner_warnings": [w for w in (state.get("planner_warnings") or []) if isinstance(w, str)],
                **self._graph_trace_checkpoint_extras(state),
            },
            node_status=dict(state.get("node_status") or {}),
            data_plan_trace=[
                {
                    "item_id": c.item_id,
                    "purpose": c.purpose,
                    "status": c.status,
                    "attempts": c.attempts,
                    "dependency_ids": c.dependency_ids,
                    "row_count": c.row_count,
                    "error": c.error,
                }
                for c in calls
            ],
            degrade_reasons=list(state.get("degrade_reasons") or []),
        )
        result = AnalysisV2Result(
            request_id=str(state.get("request_id") or ""),
            analysis_type=req.analysis_type,
            summary=summary,
            structured_report=cast(dict[str, Any], state.get("structured_report") or {}),
            evidence=evidence,
            trace=trace,
        )
        return {"v2_result": result}

    async def run_with_payload(self, req: AnalysisPayloadRequest) -> AnalysisV2Result:
        """执行 payload 模式：优先 LangGraph 编译图，否则 `_run_with_payload_sequential`。"""
        ANALYSIS_REQUEST_COUNT.labels(
            analysis_type=req.analysis_type, data_mode="payload", status="started"
        ).inc()
        try:
            if self._graph_payload is not None:
                checkpoint_tid: str | None = None
                invoke_cfg: dict[str, Any] | None = None
                if self._checkpointer is not None:
                    checkpoint_tid = f"analysis:payload:{uuid4().hex}"
                    invoke_cfg = {"configurable": {"thread_id": checkpoint_tid}}
                inp = self._payload_graph_input(req, checkpoint_thread_id=checkpoint_tid)
                if invoke_cfg is not None:
                    out = await self._graph_payload.ainvoke(inp, config=invoke_cfg)
                else:
                    out = await self._graph_payload.ainvoke(inp)
                result = out.get("v2_result")
                if result is None:
                    raise RuntimeError("analysis payload graph: missing v2_result")
            else:
                result = await self._run_with_payload_sequential(req)
            ANALYSIS_REQUEST_COUNT.labels(
                analysis_type=req.analysis_type, data_mode="payload", status="success"
            ).inc()
            return result
        except Exception:
            ANALYSIS_REQUEST_COUNT.labels(
                analysis_type=req.analysis_type, data_mode="payload", status="failed"
            ).inc()
            raise

    async def _run_with_payload_sequential(self, req: AnalysisPayloadRequest) -> AnalysisV2Result:
        """无 LangGraph 时的顺序执行路径，与 payload 图节点语义对齐。"""
        request_id = f"anl_{uuid4().hex[:12]}"
        plan_id = f"plan_{uuid4().hex[:10]}"
        node_latency_ms: dict[str, int] = {}
        node_status: dict[str, str] = {}
        degrade_reasons: list[str] = []
        t0 = perf_counter()
        self._conv.append_user_message(req.user_id, req.session_id, req.query)
        node_latency_ms["normalize_request"] = int((perf_counter() - t0) * 1000)
        node_status["normalize_request"] = "success"

        context_snippets: list[str] = []
        rag_sources: list[dict[str, Any]] = []
        used_rag = False
        if req.options.enable_rag:
            t_rag = perf_counter()
            context_snippets, rag_sources = self._retrieve_business_rag(req.query, req.analysis_type)
            used_rag = len(context_snippets) > 0
            self._mark_node(node_latency_ms, node_status, "rag_enrichment", t_rag, ok=True)
            ANALYSIS_NODE_LATENCY.labels(node="rag_enrichment", analysis_type=req.analysis_type).observe(
                (perf_counter() - t_rag)
            )

        t_quality = perf_counter()
        quality_report = self._evaluate_payload_quality(req.payload, req.analysis_type)
        node_latency_ms["data_quality_gate"] = int((perf_counter() - t_quality) * 1000)
        node_status["data_quality_gate"] = "success"
        ANALYSIS_NODE_LATENCY.labels(node="data_quality_gate", analysis_type=req.analysis_type).observe(
            (perf_counter() - t_quality)
        )
        if req.options.strict and quality_report.get("threshold_result", {}).get("failed", False):
            ANALYSIS_DEGRADE_COUNT.labels(reason="strict_payload_quality_blocked").inc()
            degrade_reasons.append("strict_payload_quality_blocked")
            raise ValueError("strict mode enabled: payload quality is insufficient for analysis")

        t_syn = perf_counter()
        synthesis_prompt, synthesis_version = self._resolve_stage_template(
            stage="analysis_synthesis",
            analysis_type=req.analysis_type,
            user_id=req.user_id,
            default_text="你是一名综合分析助手，请基于事实数据给出结论和建议。",
        )
        _intent_prompt, intent_version = self._resolve_stage_template(
            stage="analysis_intent",
            analysis_type=req.analysis_type,
            user_id=req.user_id,
            default_text="你是一名综合分析规划助手。",
        )
        _data_plan_prompt, data_plan_version = self._resolve_stage_template(
            stage="analysis_data_plan",
            analysis_type=req.analysis_type,
            user_id=req.user_id,
            default_text="请先明确本次分析所需数据域与依赖关系。",
        )
        _report_prompt, report_version = self._resolve_stage_template(
            stage="analysis_report",
            analysis_type=req.analysis_type,
            user_id=req.user_id,
            default_text="请输出结构化报告，包含结论、依据、建议。",
        )
        summary = await self._generate_summary(
            query=req.query,
            analysis_type=req.analysis_type,
            data_mode="payload",
            data_blob=req.payload,
            context_snippets=context_snippets,
            system_prompt=synthesis_prompt,
        )
        suggestions = self._build_suggestions(summary, req.analysis_type, req.options.max_suggestions)
        structured_report = self._build_structured_report(
            summary=summary,
            suggestions=suggestions,
            analysis_type=req.analysis_type,
            report_style=req.options.report_style,
            report_template=req.options.report_template,
            chart_mode=req.options.chart_mode,
            data_coverage={
                "mode": "payload",
                "payload_fields": len(req.payload.keys()),
                "completeness": quality_report.get("completeness", 0.0),
                "records": self._extract_records_from_payload(req.payload),
            },
        )
        self._mark_node(node_latency_ms, node_status, "synthesis", t_syn, ok=True)
        ANALYSIS_NODE_LATENCY.labels(node="synthesis", analysis_type=req.analysis_type).observe(
            (perf_counter() - t_syn)
        )

        self._conv.append_assistant_message(req.user_id, req.session_id, summary)
        evidence = AnalysisEvidence(
            used_rag=used_rag,
            rag_sources=rag_sources[:32],
            nl2sql_calls=[],
            data_coverage={
                "mode": "payload",
                "input_keys": list(req.payload.keys()),
                "data_quality_report": quality_report,
            },
        )
        trace = AnalysisTrace(
            plan_id=plan_id,
            node_latency_ms=node_latency_ms,
            template_versions={
                "intent": intent_version,
                "data_plan": data_plan_version,
                "synthesis": synthesis_version,
                "report": report_version,
            },
            execution_summary={
                "analysis_type": req.analysis_type,
                "data_mode": "payload",
                "started_at": datetime.now(timezone.utc).isoformat(),
                "used_rag": used_rag,
                "orchestrator": "sequential",
                "graph_nodes": [
                    "normalize_request",
                    "rag_enrichment",
                    "data_quality_gate",
                    "synthesis",
                    "report_builder",
                    "finalize",
                ],
            },
            node_status=node_status,
            data_plan_trace=[],
            degrade_reasons=degrade_reasons,
        )
        return AnalysisV2Result(
            request_id=request_id,
            analysis_type=req.analysis_type,
            summary=summary,
            structured_report=structured_report,
            evidence=evidence,
            trace=trace,
        )

    async def run_with_nl2sql(self, req: AnalysisNL2SQLRequest) -> AnalysisV2Result:
        """执行 nl2sql 模式：优先 LangGraph 编译图，否则 `_run_with_nl2sql_sequential`。"""
        ANALYSIS_REQUEST_COUNT.labels(
            analysis_type=req.analysis_type, data_mode="nl2sql", status="started"
        ).inc()
        try:
            if self._graph_nl2sql is not None:
                checkpoint_tid: str | None = None
                invoke_cfg: dict[str, Any] | None = None
                if self._checkpointer is not None:
                    checkpoint_tid = f"analysis:nl2sql:{uuid4().hex}"
                    invoke_cfg = {"configurable": {"thread_id": checkpoint_tid}}
                inp = self._nl2sql_graph_input(req, checkpoint_thread_id=checkpoint_tid)
                if invoke_cfg is not None:
                    out = await self._graph_nl2sql.ainvoke(inp, config=invoke_cfg)
                else:
                    out = await self._graph_nl2sql.ainvoke(inp)
                result = out.get("v2_result")
                if result is None:
                    raise RuntimeError("analysis nl2sql graph: missing v2_result")
            else:
                result = await self._run_with_nl2sql_sequential(req)
            ANALYSIS_REQUEST_COUNT.labels(
                analysis_type=req.analysis_type, data_mode="nl2sql", status="success"
            ).inc()
            return result
        except Exception:
            ANALYSIS_REQUEST_COUNT.labels(
                analysis_type=req.analysis_type, data_mode="nl2sql", status="failed"
            ).inc()
            raise

    async def _run_with_nl2sql_sequential(self, req: AnalysisNL2SQLRequest) -> AnalysisV2Result:
        """无 LangGraph 时的顺序执行路径，与 nl2sql 图节点语义对齐。"""
        request_id = f"anl_{uuid4().hex[:12]}"
        plan_id = f"plan_{uuid4().hex[:10]}"
        node_latency_ms: dict[str, int] = {}
        node_status: dict[str, str] = {}
        degrade_reasons: list[str] = []
        self._conv.append_user_message(req.user_id, req.session_id, req.query)

        t_pc = perf_counter()
        plan_context, plan_rag_sources = self._retrieve_plan_rag(req.query, req.analysis_type, req.options.enable_rag)
        node_latency_ms["plan_context_rag"] = int((perf_counter() - t_pc) * 1000)
        node_status["plan_context_rag"] = "success"

        planner_warnings: list[str] = []
        t_int = perf_counter()
        if not self._analysis_cfg.nl2sql_llm_planner_enabled:
            _, intent_version = self._resolve_stage_template(
                stage="analysis_intent",
                analysis_type=req.analysis_type,
                user_id=req.user_id,
                default_text="你是一名综合分析规划助手。",
            )
            intent_obj = AnalysisIntentLLMOutput()
            planner_warnings.append("nl2sql_planner_disabled")
        else:
            intent_obj, intent_version, w_int = await self._nl2sql_run_intent_llm(req, plan_context=plan_context)
            planner_warnings.extend(w_int)
        node_latency_ms["intent_llm"] = int((perf_counter() - t_int) * 1000)
        node_status["intent_llm"] = "success"

        t_pl = perf_counter()
        if not self._analysis_cfg.nl2sql_llm_planner_enabled:
            _, data_plan_version = self._resolve_stage_template(
                stage="analysis_data_plan",
                analysis_type=req.analysis_type,
                user_id=req.user_id,
                default_text="请先明确本次分析所需数据域与依赖关系。",
            )
            tasks = self._build_data_plan(req, plan_context=plan_context)
            tasks = tasks[: req.options.max_nl2sql_calls]
        else:
            llm_plan, data_plan_version, w_pl = await self._nl2sql_run_plan_llm(
                req, intent=intent_obj, plan_context=plan_context
            )
            planner_warnings.extend(w_pl)
            tasks, w_m = await self._nl2sql_merge_plan_tasks(req, plan_context=plan_context, llm_plan=llm_plan)
            planner_warnings.extend(w_m)
        node_latency_ms["plan_llm"] = int((perf_counter() - t_pl) * 1000)
        node_status["plan_llm"] = "success"

        nl2sql_calls, gathered_data, task_status, acquire_latency_ms = await self._execute_data_plan(
            req=req, tasks=tasks
        )
        node_latency_ms["acquire_data"] = acquire_latency_ms
        node_status["acquire_data"] = "success"
        ANALYSIS_NODE_LATENCY.labels(node="plan_context_rag", analysis_type=req.analysis_type).observe(
            perf_counter() - t_pc
        )
        ANALYSIS_NODE_LATENCY.labels(node="intent_llm", analysis_type=req.analysis_type).observe(
            perf_counter() - t_int
        )
        ANALYSIS_NODE_LATENCY.labels(node="plan_llm", analysis_type=req.analysis_type).observe(perf_counter() - t_pl)

        t_quality = perf_counter()
        quality_report = self._evaluate_nl2sql_quality(
            nl2sql_calls,
            gathered_data,
            analysis_type=req.analysis_type,
            task_status=task_status,
        )
        node_latency_ms["data_quality_gate"] = int((perf_counter() - t_quality) * 1000)
        node_status["data_quality_gate"] = "success"
        ANALYSIS_NODE_LATENCY.labels(node="data_quality_gate", analysis_type=req.analysis_type).observe(
            (perf_counter() - t_quality)
        )
        if quality_report.get("mandatory_failed", 0) > 0:
            degrade_reasons.append("mandatory_steps_failed")
        if req.options.strict and quality_report.get("threshold_result", {}).get("failed", False):
            ANALYSIS_DEGRADE_COUNT.labels(reason="strict_nl2sql_quality_blocked").inc()
            degrade_reasons.append("strict_nl2sql_quality_blocked")
            raise ValueError("strict mode enabled: NL2SQL data quality thresholds not met")

        context_snippets: list[str] = []
        biz_rag_sources: list[dict[str, Any]] = []
        used_rag = False
        if req.options.enable_rag:
            t_rag = perf_counter()
            context_snippets, biz_rag_sources = self._retrieve_business_rag(req.query, req.analysis_type)
            used_rag = len(context_snippets) > 0
            self._mark_node(node_latency_ms, node_status, "rag_enrichment", t_rag, ok=True)
            ANALYSIS_NODE_LATENCY.labels(node="rag_enrichment", analysis_type=req.analysis_type).observe(
                (perf_counter() - t_rag)
            )

        t_syn = perf_counter()
        synthesis_prompt, synthesis_version = self._resolve_stage_template(
            stage="analysis_synthesis",
            analysis_type=req.analysis_type,
            user_id=req.user_id,
            default_text="你是一名综合分析助手，请基于事实数据给出结论和建议。",
        )
        _report_prompt, report_version = self._resolve_stage_template(
            stage="analysis_report",
            analysis_type=req.analysis_type,
            user_id=req.user_id,
            default_text="请输出结构化报告，包含结论、依据、建议。",
        )
        planning_ctx: str | None = None
        if self._analysis_cfg.nl2sql_llm_planner_enabled:
            planning_ctx = json.dumps(intent_obj.model_dump(mode="json"), ensure_ascii=False)
        summary = await self._generate_summary(
            query=req.query,
            analysis_type=req.analysis_type,
            data_mode="nl2sql",
            data_blob=gathered_data,
            context_snippets=context_snippets,
            system_prompt=synthesis_prompt,
            planning_context=planning_ctx,
        )
        suggestions = self._build_suggestions(summary, req.analysis_type, req.options.max_suggestions)
        structured_report = self._build_structured_report(
            summary=summary,
            suggestions=suggestions,
            analysis_type=req.analysis_type,
            report_style=req.options.report_style,
            report_template=req.options.report_template,
            chart_mode=req.options.chart_mode,
            data_coverage={
                "mode": "nl2sql",
                "planned_calls": len(tasks),
                "success_calls": sum(1 for c in nl2sql_calls if c.status == "success"),
                "failed_calls": sum(1 for c in nl2sql_calls if c.status == "failed"),
                "skipped_calls": sum(1 for c in nl2sql_calls if c.status == "skipped"),
                "records": self._extract_records_from_gathered(gathered_data),
            },
        )
        self._mark_node(node_latency_ms, node_status, "synthesis", t_syn, ok=True)
        ANALYSIS_NODE_LATENCY.labels(node="synthesis", analysis_type=req.analysis_type).observe(
            (perf_counter() - t_syn)
        )

        self._conv.append_assistant_message(req.user_id, req.session_id, summary)
        evidence = AnalysisEvidence(
            used_rag=used_rag,
            rag_sources=(plan_rag_sources + biz_rag_sources)[:64],
            nl2sql_calls=nl2sql_calls,
            data_coverage={
                "mode": "nl2sql",
                "planned_calls": len(tasks),
                "success_calls": sum(1 for c in nl2sql_calls if c.status == "success"),
                "failed_calls": sum(1 for c in nl2sql_calls if c.status == "failed"),
                "skipped_calls": sum(1 for c in nl2sql_calls if c.status == "skipped"),
                "data_quality_report": quality_report,
            },
        )
        trace = AnalysisTrace(
            plan_id=plan_id,
            node_latency_ms=node_latency_ms,
            template_versions={
                "intent": intent_version,
                "data_plan": data_plan_version,
                "synthesis": synthesis_version,
                "report": report_version,
            },
            execution_summary={
                "analysis_type": req.analysis_type,
                "data_mode": "nl2sql",
                "started_at": datetime.now(timezone.utc).isoformat(),
                "used_rag": used_rag,
                "planned_calls": len(tasks),
                "orchestrator": "sequential",
                "graph_nodes": [
                    "normalize_request",
                    "plan_context_rag",
                    "intent_llm",
                    "plan_llm",
                    "acquire_data",
                    "data_quality_gate",
                    "rag_enrichment",
                    "synthesis",
                    "report_builder",
                    "finalize",
                ],
                "planner_warnings": planner_warnings,
            },
            node_status=node_status,
            data_plan_trace=[
                {
                    "item_id": c.item_id,
                    "purpose": c.purpose,
                    "status": c.status,
                    "attempts": c.attempts,
                    "dependency_ids": c.dependency_ids,
                    "row_count": c.row_count,
                    "error": c.error,
                }
                for c in nl2sql_calls
            ],
            degrade_reasons=degrade_reasons,
        )
        return AnalysisV2Result(
            request_id=request_id,
            analysis_type=req.analysis_type,
            summary=summary,
            structured_report=structured_report,
            evidence=evidence,
            trace=trace,
        )

    async def _generate_summary(
        self,
        *,
        query: str,
        analysis_type: str,
        data_mode: str,
        data_blob: dict,
        context_snippets: list[str],
        system_prompt: str,
        planning_context: str | None = None,
    ) -> str:
        """单次 LLM 调用生成分析摘要；失败时返回固定降级文案。"""
        data_preview = json.dumps(data_blob, ensure_ascii=False)[:4000]
        rag_text = "\n".join(f"- {s}" for s in context_snippets[:8])
        pc = (planning_context or "").strip()
        planning_block = f"\n分阶段规划意图(结构化要点):\n{pc[:2000]}\n" if pc else ""
        prompt = (
            f"{system_prompt}\n\n"
            f"分析类型: {analysis_type}\n"
            f"数据来源模式: {data_mode}\n"
            f"用户问题: {query}\n"
            f"{planning_block}"
            f"数据摘要(JSON截断): {data_preview}\n"
            f"RAG参考片段:\n{rag_text}\n\n"
            "请输出：1) 核心结论；2) 关键依据；3) 可执行建议。"
        )
        try:
            summary = await self._llm.generate(model=None, prompt=prompt)  # type: ignore[arg-type]
            return summary
        except Exception:  # noqa: BLE001
            logger.exception("analysis graph summary generation failed")
            return "综合分析生成失败，已返回基础报告，请稍后重试。"

    @staticmethod
    def _build_suggestions(summary: str, analysis_type: str, max_items: int) -> list[dict]:
        """
        将摘要升级为结构化动作策略（多条）：
        - 含 priority/category/owner/eta/trigger/rationale/action；
        - 按场景注入默认高价值动作，再融合摘要中可提取动作句。
        """
        trimmed = summary.strip()
        if not trimmed:
            return []
        actions = AnalysisGraphRunner._default_actions_by_type(analysis_type)
        extracted = AnalysisGraphRunner._extract_action_sentences(trimmed)
        for idx, sentence in enumerate(extracted, start=1):
            actions.append(
                {
                    "priority": min(5, idx + 2),
                    "category": "follow_up",
                    "owner": "运行值班",
                    "eta": "24h",
                    "trigger": "summary_signal",
                    "rationale": sentence[:120],
                    "action": sentence[:140],
                }
            )
        dedup: list[dict[str, Any]] = []
        seen: set[str] = set()
        for item in actions:
            key = str(item.get("action", "")).strip()
            if not key or key in seen:
                continue
            seen.add(key)
            dedup.append(item)
        return dedup[:max_items]

    @staticmethod
    def _extract_action_sentences(summary: str) -> list[str]:
        raw_parts = []
        for block in summary.splitlines():
            raw_parts.extend(block.replace("；", "。").split("。"))
        verbs = ("建议", "应", "需要", "安排", "调整", "复核", "监测", "检修", "治理")
        out: list[str] = []
        for part in raw_parts:
            sentence = part.strip(" -\t")
            if len(sentence) < 8:
                continue
            if any(v in sentence for v in verbs):
                out.append(sentence)
        return out[:8]

    @staticmethod
    def _default_actions_by_type(analysis_type: str) -> list[dict[str, Any]]:
        if analysis_type == "overheat_guidance":
            return [
                {
                    "priority": 1,
                    "category": "operation_adjustment",
                    "owner": "运行主值",
                    "eta": "2h",
                    "trigger": "overheat_detected",
                    "rationale": "超温类问题优先进行运行参数快速收敛，降低持续热偏差风险。",
                    "action": "按受热面热偏差对风门开度进行小步调节（建议单次 5%-10%），并观察 30 分钟趋势。",
                },
                {
                    "priority": 2,
                    "category": "maintenance",
                    "owner": "检修班组",
                    "eta": "24h",
                    "trigger": "heat_exchange_drop",
                    "rationale": "换热效率下降常与积灰相关，需结合运行窗口安排处理。",
                    "action": "安排吹灰作业并复测关键温区，确认超温频次是否下降。",
                },
            ]
        if analysis_type == "maintenance_strategy":
            return [
                {
                    "priority": 1,
                    "category": "repair_plan",
                    "owner": "设备专工",
                    "eta": "48h",
                    "trigger": "thickness_below_threshold",
                    "rationale": "壁厚低于安全阈值区域应优先纳入强制检修清单。",
                    "action": "形成一级必换管清单（壁厚<3mm），并提交检修窗口审批。",
                },
                {
                    "priority": 2,
                    "category": "monitoring",
                    "owner": "点检工程师",
                    "eta": "7d",
                    "trigger": "high_temp_frequency",
                    "rationale": "中风险区域需通过高频复测避免风险快速演化。",
                    "action": "对二级监测区（3-4mm 且高超温频次）执行周级复测与趋势追踪。",
                },
            ]
        return [
            {
                "priority": 1,
                "category": "general",
                "owner": "业务负责人",
                "eta": "24h",
                "trigger": "analysis_completed",
                "rationale": "默认策略要求先完成数据口径确认再执行动作。",
                "action": "组织一次数据口径复核会议，确认关键指标定义与时间窗范围。",
            }
        ]

    @staticmethod
    def _build_structured_report(
        *,
        summary: str,
        suggestions: list[dict],
        analysis_type: str,
        report_style: str,
        report_template: str,
        chart_mode: str,
        data_coverage: dict[str, Any],
    ) -> dict:
        """由摘要与数据覆盖组装 `structured_report`（sections/tables/charts 等）。"""
        records = AnalysisGraphRunner._flatten_records(data_coverage)
        charts = []
        if chart_mode != "off":
            if analysis_type == "overheat_guidance":
                charts = [
                    {
                        "type": "line",
                        "title": "超温趋势",
                        "x_field": "time",
                        "y_field": "temperature",
                        "series_name": "wall_temp",
                        "data": AnalysisGraphRunner._build_overheat_trend_data(records),
                    },
                    {
                        "type": "bar",
                        "title": "区域超温次数",
                        "x_field": "zone",
                        "y_field": "count",
                        "series_name": "overheat_events",
                        "data": AnalysisGraphRunner._build_zone_count_data(records),
                    },
                ]
            elif analysis_type == "maintenance_strategy":
                charts = [
                    {
                        "type": "histogram",
                        "title": "壁厚分布",
                        "x_field": "thickness_bin",
                        "y_field": "count",
                        "series_name": "wall_thickness",
                        "data": AnalysisGraphRunner._build_thickness_histogram_data(records),
                    },
                    {
                        "type": "bar",
                        "title": "检修分级统计",
                        "x_field": "level",
                        "y_field": "count",
                        "series_name": "maintenance_level",
                        "data": AnalysisGraphRunner._build_level_count_data(records),
                    },
                ]
        return {
            "meta": {
                "analysis_type": analysis_type,
                "report_style": report_style,
                "report_template": report_template,
            },
            "sections": [
                {"title": "结论摘要", "content": summary},
                {"title": "执行说明", "content": f"数据覆盖概览: {json.dumps(data_coverage, ensure_ascii=False)}"},
            ],
            "tables": [
                {
                    "title": "建议清单",
                    "columns": ["priority", "category", "owner", "eta", "trigger", "rationale", "action"],
                    "rows": suggestions,
                }
            ],
            "charts": charts if chart_mode != "minimal" else charts[:1],
            "suggestions": suggestions,
            "risks": [],
        }

    @staticmethod
    def _flatten_records(data_coverage: dict[str, Any]) -> list[dict[str, Any]]:
        """
        将 Phase3 输入中的数据覆盖摘要转为统一记录列表。
        兼容：
        - payload 模式（可能带 records）
        - nl2sql 模式（当前阶段可能无 rows，仅返回空）
        """
        records = data_coverage.get("records")
        if isinstance(records, list):
            return [r for r in records if isinstance(r, dict)]
        return []

    @staticmethod
    def _build_overheat_trend_data(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for r in records:
            t = r.get("time") or r.get("timestamp") or r.get("ts")
            temp = (
                r.get("temperature")
                or r.get("temp")
                or r.get("wall_temp")
                or r.get("wall_temperature")
            )
            if t is None or temp is None:
                continue
            try:
                out.append({"time": str(t), "temperature": float(temp)})
            except Exception:  # noqa: BLE001
                continue
        return out[:500]

    @staticmethod
    def _build_zone_count_data(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
        buckets: dict[str, int] = {}
        for r in records:
            zone = r.get("zone") or r.get("area") or r.get("region") or r.get("location")
            if zone is None:
                continue
            key = str(zone)
            buckets[key] = buckets.get(key, 0) + 1
        return [{"zone": k, "count": v} for k, v in buckets.items()]

    @staticmethod
    def _build_thickness_histogram_data(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
        bins = {"<3": 0, "3-4": 0, "4-5": 0, ">=5": 0}
        for r in records:
            v = r.get("thickness") or r.get("wall_thickness") or r.get("thk")
            try:
                fv = float(v)
            except Exception:  # noqa: BLE001
                continue
            if fv < 3:
                bins["<3"] += 1
            elif fv < 4:
                bins["3-4"] += 1
            elif fv < 5:
                bins["4-5"] += 1
            else:
                bins[">=5"] += 1
        return [{"thickness_bin": k, "count": v} for k, v in bins.items()]

    @staticmethod
    def _build_level_count_data(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
        buckets: dict[str, int] = {}
        for r in records:
            level = r.get("level") or r.get("maintenance_level") or r.get("risk_level")
            if level is None:
                continue
            key = str(level)
            buckets[key] = buckets.get(key, 0) + 1
        if buckets:
            return [{"level": k, "count": v} for k, v in buckets.items()]
        # 若没有显式等级字段，则基于 thickness 做规则分级（企业默认口径）
        fallback = {"一级必换": 0, "二级建议监测": 0, "三级常规跟踪": 0}
        for r in records:
            v = r.get("thickness") or r.get("wall_thickness") or r.get("thk")
            try:
                fv = float(v)
            except Exception:  # noqa: BLE001
                continue
            if fv < 3:
                fallback["一级必换"] += 1
            elif fv < 4:
                fallback["二级建议监测"] += 1
            else:
                fallback["三级常规跟踪"] += 1
        return [{"level": k, "count": v} for k, v in fallback.items()]

    @staticmethod
    def _extract_records_from_payload(payload: dict[str, Any]) -> list[dict[str, Any]]:
        records: list[dict[str, Any]] = []
        for _, v in payload.items():
            if isinstance(v, list):
                for item in v:
                    if isinstance(item, dict):
                        records.append(item)
            elif isinstance(v, dict):
                records.append(v)
        return records[:1000]

    @staticmethod
    def _extract_records_from_gathered(gathered_data: dict[str, list[dict]]) -> list[dict[str, Any]]:
        records: list[dict[str, Any]] = []
        for _, rows in gathered_data.items():
            for row in rows:
                if isinstance(row, dict):
                    records.append(row)
        return records[:2000]

    def _resolve_stage_template(
        self,
        *,
        stage: str,
        analysis_type: str,
        user_id: str,
        default_text: str,
    ) -> tuple[str, str]:
        """
        分层模板解析策略（生产可用）：
        1) 优先匹配 stage + analysis_type；
        2) 回退 stage；
        3) 最终回退 analysis；
        """
        candidate_scenes = [
            f"{stage}_{analysis_type}",
            stage,
            "analysis",
        ]
        for scene in candidate_scenes:
            tpl = self._prompts.get_template(scene=scene, user_id=user_id, version=None)
            if tpl and tpl.content:
                version = f"{scene}:{getattr(tpl, 'version', 'default')}"
                return tpl.content, version
        return default_text, f"{stage}:default"

    def _evaluate_payload_quality(self, payload: dict[str, Any], analysis_type: str) -> dict[str, Any]:
        """计算 payload 完整度、时间窗覆盖、异常率、关键字段缺失率及 strict 用阈值结果。"""
        keys = list(payload.keys())
        non_empty_keys = [k for k, v in payload.items() if v not in (None, [], {}, "")]
        records = self._extract_records_from_payload(payload)
        warnings: list[str] = []
        if not keys:
            warnings.append("payload 为空，分析依据有限")
        if keys and not non_empty_keys:
            warnings.append("payload 字段均为空值，建议补充有效数据")
        coverage_rate = self._compute_time_window_coverage_rate(records)
        anomaly_rate = self._compute_numeric_anomaly_rate(records)
        required_fields = self._required_fields_by_type(analysis_type)
        missing_key_rate = self._compute_missing_key_rate(records, required_fields)
        completeness = 0.0 if not keys else round(len(non_empty_keys) / len(keys), 4)
        threshold_result = self._payload_threshold_result(
            coverage_rate=coverage_rate,
            anomaly_rate=anomaly_rate,
            missing_key_rate=missing_key_rate,
        )
        warnings.extend(threshold_result["warnings"])
        return {
            "completeness": completeness,
            "total_fields": len(keys),
            "non_empty_fields": len(non_empty_keys),
            "time_window_coverage_rate": coverage_rate,
            "anomaly_rate": anomaly_rate,
            "missing_key_rate": missing_key_rate,
            "required_fields": required_fields,
            "threshold_result": threshold_result,
            "warnings": warnings,
        }

    def _evaluate_nl2sql_quality(
        self,
        calls: list[AnalysisNL2SQLCall],
        gathered_data: dict[str, list[dict]],
        *,
        analysis_type: str,
        task_status: dict[str, str],
    ) -> dict[str, Any]:
        """基于 NL2SQL 调用结果与聚合行评估覆盖与质量，供 data_quality_gate 与 strict 使用。"""
        planned = len(calls)
        success = sum(1 for c in calls if c.status == "success")
        failed = sum(1 for c in calls if c.status == "failed")
        skipped = sum(1 for c in calls if c.status == "skipped")
        mandatory_failed = sum(1 for _k, v in task_status.items() if v == "mandatory_failed")
        total_rows = sum(len(v) for v in gathered_data.values())
        records = self._extract_records_from_gathered(gathered_data)
        coverage_rate = self._compute_time_window_coverage_rate(records)
        anomaly_rate = self._compute_numeric_anomaly_rate(records)
        required_fields = self._required_fields_by_type(analysis_type)
        missing_key_rate = self._compute_missing_key_rate(records, required_fields)
        warnings: list[str] = []
        if success == 0:
            warnings.append("NL2SQL 查询全部失败，建议检查问题表述或数据库连接")
        elif total_rows == 0:
            warnings.append("NL2SQL 查询成功但无结果，建议调整时间窗或过滤条件")
        if mandatory_failed > 0:
            warnings.append("存在关键数据步骤失败，分析结果可能偏保守")
        threshold_result = self._nl2sql_threshold_result(
            coverage_rate=coverage_rate,
            anomaly_rate=anomaly_rate,
            missing_key_rate=missing_key_rate,
            success_calls=success,
            planned_calls=planned,
            mandatory_failed=mandatory_failed,
        )
        warnings.extend(threshold_result["warnings"])
        completeness = 0.0 if planned == 0 else round(success / planned, 4)
        return {
            "completeness": completeness,
            "planned_calls": planned,
            "success_calls": success,
            "failed_calls": failed,
            "skipped_calls": skipped,
            "mandatory_failed": mandatory_failed,
            "total_rows": total_rows,
            "time_window_coverage_rate": coverage_rate,
            "anomaly_rate": anomaly_rate,
            "missing_key_rate": missing_key_rate,
            "required_fields": required_fields,
            "threshold_result": threshold_result,
            "warnings": warnings,
        }

    @staticmethod
    def _required_fields_by_type(analysis_type: str) -> list[str]:
        if analysis_type == "overheat_guidance":
            return ["time", "temperature", "zone"]
        if analysis_type == "maintenance_strategy":
            return ["time", "thickness", "zone"]
        return ["time"]

    @staticmethod
    def _pick_time_value(record: dict[str, Any]) -> datetime | None:
        for key in ("time", "timestamp", "ts", "datetime", "date"):
            val = record.get(key)
            if val is None:
                continue
            try:
                text = str(val).strip()
                if text.endswith("Z"):
                    text = text[:-1] + "+00:00"
                dt = datetime.fromisoformat(text)
                return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
            except Exception:  # noqa: BLE001
                continue
        return None

    @classmethod
    def _compute_time_window_coverage_rate(cls, records: list[dict[str, Any]]) -> float:
        timestamps = [cls._pick_time_value(r) for r in records]
        timestamps = [t for t in timestamps if t is not None]
        if len(timestamps) < 2:
            return 1.0 if records else 0.0
        timestamps = sorted(timestamps)
        gaps = [
            max(1.0, (timestamps[i + 1] - timestamps[i]).total_seconds())
            for i in range(len(timestamps) - 1)
        ]
        step = median(gaps) if gaps else 60.0
        span = max(1.0, (timestamps[-1] - timestamps[0]).total_seconds())
        expected = max(1, int(round(span / step)) + 1)
        observed = len(timestamps)
        return round(min(1.0, observed / expected), 4)

    @staticmethod
    def _compute_numeric_anomaly_rate(records: list[dict[str, Any]]) -> float:
        numeric_fields: dict[str, list[float]] = {}
        for row in records:
            for k, v in row.items():
                if isinstance(v, bool):
                    continue
                try:
                    fv = float(v)
                except Exception:  # noqa: BLE001
                    continue
                numeric_fields.setdefault(k, []).append(fv)
        total_points = 0
        anomaly_points = 0
        for values in numeric_fields.values():
            if len(values) < 8:
                continue
            sorted_vals = sorted(values)
            q1 = sorted_vals[len(sorted_vals) // 4]
            q3 = sorted_vals[(len(sorted_vals) * 3) // 4]
            iqr = q3 - q1
            if iqr <= 0:
                continue
            low = q1 - 1.5 * iqr
            high = q3 + 1.5 * iqr
            total_points += len(values)
            anomaly_points += sum(1 for x in values if x < low or x > high)
        if total_points == 0:
            return 0.0
        return round(anomaly_points / total_points, 4)

    @staticmethod
    def _compute_missing_key_rate(records: list[dict[str, Any]], required_fields: list[str]) -> float:
        if not records or not required_fields:
            return 0.0
        total_checks = len(records) * len(required_fields)
        miss = 0
        aliases = {
            "temperature": {"temp", "wall_temp", "wall_temperature"},
            "thickness": {"wall_thickness", "thk"},
            "time": {"timestamp", "ts", "datetime", "date"},
            "zone": {"area", "region", "location"},
        }
        for row in records:
            for field in required_fields:
                candidates = {field} | aliases.get(field, set())
                if not any(row.get(c) not in (None, "", [], {}) for c in candidates):
                    miss += 1
        return round(miss / total_checks, 4)

    def _payload_threshold_result(
        self, *, coverage_rate: float, anomaly_rate: float, missing_key_rate: float
    ) -> dict[str, Any]:
        thresholds = {
            "time_window_coverage_min": self._analysis_cfg.payload_time_window_coverage_min,
            "anomaly_rate_max": self._analysis_cfg.payload_anomaly_rate_max,
            "missing_key_rate_max": self._analysis_cfg.payload_missing_key_rate_max,
        }
        violations: list[str] = []
        if coverage_rate < thresholds["time_window_coverage_min"]:
            violations.append("time_window_coverage_low")
        if anomaly_rate > thresholds["anomaly_rate_max"]:
            violations.append("anomaly_rate_high")
        if missing_key_rate > thresholds["missing_key_rate_max"]:
            violations.append("missing_key_rate_high")
        warnings = [f"payload_quality_violation:{v}" for v in violations]
        return {"failed": len(violations) > 0, "violations": violations, "thresholds": thresholds, "warnings": warnings}

    def _nl2sql_threshold_result(
        self,
        *,
        coverage_rate: float,
        anomaly_rate: float,
        missing_key_rate: float,
        success_calls: int,
        planned_calls: int,
        mandatory_failed: int,
    ) -> dict[str, Any]:
        thresholds = {
            "time_window_coverage_min": self._analysis_cfg.nl2sql_time_window_coverage_min,
            "anomaly_rate_max": self._analysis_cfg.nl2sql_anomaly_rate_max,
            "missing_key_rate_max": self._analysis_cfg.nl2sql_missing_key_rate_max,
        }
        violations: list[str] = []
        if coverage_rate < thresholds["time_window_coverage_min"]:
            violations.append("time_window_coverage_low")
        if anomaly_rate > thresholds["anomaly_rate_max"]:
            violations.append("anomaly_rate_high")
        if missing_key_rate > thresholds["missing_key_rate_max"]:
            violations.append("missing_key_rate_high")
        if planned_calls > 0 and success_calls <= 0:
            violations.append("all_calls_failed")
        if mandatory_failed > 0:
            violations.append("mandatory_steps_failed")
        warnings = [f"nl2sql_quality_violation:{v}" for v in violations]
        return {"failed": len(violations) > 0, "violations": violations, "thresholds": thresholds, "warnings": warnings}

    def _retrieve_plan_rag(
        self, query: str, analysis_type: str, enable_rag: bool
    ) -> tuple[list[str], list[dict[str, Any]]]:
        """规划前 RAG：逐 nl2sql_* 命名空间检索，scene 固定为 nl2sql。"""
        if not enable_rag:
            return [], []
        namespaces = ["nl2sql_schema", "nl2sql_biz_knowledge", "nl2sql_qa_examples"]
        results: list[str] = []
        sources: list[dict[str, Any]] = []
        for ns in namespaces:
            try:
                parts, src = self._retrieve_rag_with_sources(
                    query=f"{analysis_type} {query}",
                    namespace=ns,
                    top_k=3,
                    scene="nl2sql",
                )
            except Exception:  # noqa: BLE001
                logger.exception("analysis plan rag retrieve failed namespace=%s", ns)
                continue
            if parts:
                results.extend(parts[:3])
                sources.extend(src[:3])
        return results[:9], sources[:9]

    def _retrieve_business_rag(self, query: str, analysis_type: str) -> tuple[list[str], list[dict[str, Any]]]:
        """结论前业务 RAG：全局 namespace，scene=analysis。"""
        try:
            return self._retrieve_rag_with_sources(
                query=f"{analysis_type} {query}",
                namespace=None,
                top_k=8,
                scene="analysis",
            )
        except Exception:  # noqa: BLE001
            logger.exception("analysis business rag retrieve failed")
            return [], []

    def _build_data_plan(self, req: AnalysisNL2SQLRequest, *, plan_context: list[str]) -> list[_PlanTask]:
        """数据计划：先 YAML 模板 `analysis_plan_<type>`；为空时用代码内置默认任务，再拼 data_requirements_hint 与 plan_context 引导。"""
        templated = self._build_data_plan_from_template(req, plan_context=plan_context)
        if templated:
            return templated
        hints = req.data_requirements_hint or []
        if req.analysis_type == "overheat_guidance":
            base = [
                _PlanTask("q1", "超温事实明细", f"{req.query}，查询超温事件明细与时间分布", mandatory=True),
                _PlanTask("q2", "运行参数关联", f"{req.query}，查询风门开度、蒸汽流量等运行参数", mandatory=True),
                _PlanTask("q3", "燃烧器状态", f"{req.query}，查询燃烧器状态及切换记录", mandatory=False, dependency_ids=["q1"]),
            ]
        elif req.analysis_type == "maintenance_strategy":
            base = [
                _PlanTask("q1", "壁厚测量数据", f"{req.query}，查询壁厚测量结果与趋势", mandatory=True),
                _PlanTask("q2", "换管历史记录", f"{req.query}，查询换管历史、材质与时间信息", mandatory=True),
                _PlanTask("q3", "超温频次统计", f"{req.query}，按区域统计超温频次", mandatory=False, dependency_ids=["q1"]),
            ]
        else:
            base = [
                _PlanTask("q1", "关键事实数据", f"{req.query}，查询核心事实数据", mandatory=True),
                _PlanTask("q2", "关联维度数据", f"{req.query}，查询关联维度和补充信息", mandatory=False, dependency_ids=["q1"]),
            ]

        if hints:
            for i, h in enumerate(hints, start=1):
                qid = f"h{i}"
                base.append(
                    _PlanTask(
                        item_id=qid,
                        purpose=f"提示补充:{h}",
                        question=f"{req.query}，补充查询与“{h}”直接相关的数据",
                        mandatory=False,
                    )
                )
        if plan_context:
            guide = "；".join(plan_context[:2])
            for task in base:
                task.question = f"{task.question}。请结合以下规则线索：{guide}"
        return base

    def _build_data_plan_from_template(self, req: AnalysisNL2SQLRequest, *, plan_context: list[str]) -> list[_PlanTask]:
        """
        基于 PromptTemplateRegistry 的分析类型计划模板扩展数据计划。
        约定 scene：analysis_plan_<analysis_type>，content 为 JSON 数组：
        [
          {"item_id":"q1","purpose":"...","question":"...","mandatory":true,"dependency_ids":[]}
        ]
        """
        scene = f"analysis_plan_{req.analysis_type}"
        tpl = self._prompts.get_template(scene=scene, user_id=req.user_id, version=None)
        if tpl is None or not getattr(tpl, "content", "").strip():
            return []
        try:
            raw_items = json.loads(str(tpl.content))
        except Exception:  # noqa: BLE001
            logger.warning("analysis data plan template is not valid json, scene=%s", scene)
            return []
        if not isinstance(raw_items, list):
            return []
        tasks: list[_PlanTask] = []
        guide = "；".join(plan_context[:2]) if plan_context else ""
        for idx, item in enumerate(raw_items, start=1):
            if not isinstance(item, dict):
                continue
            q = str(item.get("question") or req.query).strip()
            if guide:
                q = f"{q}。请结合以下规则线索：{guide}"
            tasks.append(
                _PlanTask(
                    item_id=str(item.get("item_id") or f"q{idx}"),
                    purpose=str(item.get("purpose") or f"模板任务{idx}"),
                    question=q,
                    mandatory=bool(item.get("mandatory", True)),
                    dependency_ids=[str(x) for x in (item.get("dependency_ids") or []) if str(x).strip()],
                    namespace_hint=(str(item.get("namespace_hint")).strip() or None)
                    if item.get("namespace_hint") is not None
                    else None,
                )
            )
        hints = req.data_requirements_hint or []
        for i, h in enumerate(hints, start=1):
            tasks.append(
                _PlanTask(
                    item_id=f"h{i}",
                    purpose=f"提示补充:{h}",
                    question=f"{req.query}，补充查询与“{h}”直接相关的数据",
                    mandatory=False,
                )
            )
        return tasks

    async def _execute_data_plan(
        self, *, req: AnalysisNL2SQLRequest, tasks: list[_PlanTask]
    ) -> tuple[list[AnalysisNL2SQLCall], dict[str, list[dict]], dict[str, str], int]:
        """逐项执行 NL2SQL：依赖未满足则 skipped；每项最多 2 次尝试。返回调用轨迹、聚合数据、任务状态与耗时毫秒。"""
        calls: list[AnalysisNL2SQLCall] = []
        gathered_data: dict[str, list[dict]] = {}
        task_status: dict[str, str] = {}
        t_data = perf_counter()
        for task in tasks:
            if task.dependency_ids and any(task_status.get(dep) != "success" for dep in task.dependency_ids):
                calls.append(
                    AnalysisNL2SQLCall(
                        item_id=task.item_id,
                        purpose=task.purpose,
                        question=task.question,
                        sql="",
                        row_count=0,
                        status="skipped",
                        attempts=0,
                        dependency_ids=task.dependency_ids,
                        error="dependency_not_satisfied",
                    )
                )
                task_status[task.item_id] = "mandatory_failed" if task.mandatory else "optional_skipped"
                ANALYSIS_NL2SQL_CALL_COUNT.labels(
                    analysis_type=req.analysis_type, status="skipped"
                ).inc()
                continue

            max_attempts = 2
            last_error: str | None = None
            final_sql = ""
            rows: list[dict] = []
            success = False
            for attempt in range(1, max_attempts + 1):
                try:
                    resp = await self._nl2sql.query(
                        NL2SQLQueryRequest(
                            user_id=req.user_id,
                            session_id=req.session_id,
                            question=task.question,
                        ),
                        record_conversation=False,
                    )
                    final_sql = resp.sql
                    rows = resp.rows[: req.options.max_rows_per_query]
                    success = True
                    calls.append(
                        AnalysisNL2SQLCall(
                            item_id=task.item_id,
                            purpose=task.purpose,
                            question=task.question,
                            sql=final_sql,
                            row_count=len(rows),
                            status="success",
                            attempts=attempt,
                            dependency_ids=task.dependency_ids,
                        )
                    )
                    gathered_data[task.item_id] = rows
                    task_status[task.item_id] = "success"
                    ANALYSIS_NL2SQL_CALL_COUNT.labels(
                        analysis_type=req.analysis_type, status="success"
                    ).inc()
                    break
                except Exception as exc:  # noqa: BLE001
                    last_error = str(exc)
                    logger.exception("analysis nl2sql task failed item=%s attempt=%s", task.item_id, attempt)
            if not success:
                calls.append(
                    AnalysisNL2SQLCall(
                        item_id=task.item_id,
                        purpose=task.purpose,
                        question=task.question,
                        sql=final_sql,
                        row_count=0,
                        status="failed",
                        attempts=max_attempts,
                        dependency_ids=task.dependency_ids,
                        error=last_error,
                    )
                )
                task_status[task.item_id] = "mandatory_failed" if task.mandatory else "optional_failed"
                ANALYSIS_NL2SQL_CALL_COUNT.labels(
                    analysis_type=req.analysis_type, status="failed"
                ).inc()
        duration_s = perf_counter() - t_data
        ANALYSIS_NODE_LATENCY.labels(node="acquire_data", analysis_type=req.analysis_type).observe(duration_s)
        return calls, gathered_data, task_status, int(duration_s * 1000)
