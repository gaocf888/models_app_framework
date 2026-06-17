from __future__ import annotations

"""
综合分析（企业版 V2）编排实现。

- 对外入口：`AnalysisGraphRunner.run_with_payload`、`run_with_nl2sql`（由 `AnalysisService` 调用）。
- 两套 LangGraph `StateGraph(AnalysisGraphState)`（payload / nl2sql）；`langgraph` 不可用时走 `_run_with_*_sequential`。
- 数据计划：优先 `configs/prompts_bak_new.yaml` 中 `analysis_plan_<analysis_type>`，可选 LLM 意图/计划合并，最后才用内置默认任务。
- **acquire_data / `_execute_data_plan`**：默认按 **`dependency_ids` 分层**，同层任务 **`asyncio.gather` 并行**调用 `NL2SQLService.query`（单任务内仍为「生成 SQL → 执行」串行）；可通过 **`ANALYSIS_NL2SQL_ACQUIRE_PARALLEL_ENABLED`** / **`ANALYSIS_NL2SQL_ACQUIRE_MAX_PARALLEL`** 关闭或限流（见 `AnalysisConfig`）。
- 阶段模板加载优先级（`_resolve_stage_template`）：
  `stage_<analysis_type>` -> `stage` -> `analysis`。
  例如 `analysis_type=overheat_guidance` 时：
  `analysis_intent_overheat_guidance` -> `analysis_intent` -> `analysis`（其余 stage 同理）。
"""

import asyncio
import json
import re
from datetime import datetime, timezone
from statistics import median
from time import perf_counter
from uuid import uuid4
from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Awaitable, Callable, cast

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
from app.llm.graphs.analysis_graph_state import AnalysisGraphState
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
from app.llm.graphs.analysis_finished_meta import (
    analysis_finished_sse_event,
    build_analysis_finished_meta,
)
from app.llm.graphs.chatbot_rag_citations import chunks_to_rag_citations
from app.rag.hybrid_rag_service import HybridRAGService
from app.rag.models import RetrievedChunk
from app.services.analysis_stream_hooks import dispatch_analysis_nl2sql_stream_structured
from app.nl2sql.errors import NL2SQLExecutionError
from app.nl2sql.question_intent_display import trace_include_question_intent
from app.services.nl2sql_service import NL2SQLService
from app.llm.graphs.analysis_synthesis_v2 import (
    AnalysisSynthesisV2Engine,
    synthesis_v2_registry_available,
)

logger = get_logger(__name__)

# 规划前 RAG：analysis_type → 短中文标签（cn_label_prefix / two_stage 重排用，与业务知识库表述对齐）
_PLAN_RAG_ANALYSIS_TYPE_CN: dict[str, str] = {
    "overheat_guidance": "超温分析",
    "maintenance_strategy": "检修策略分析",
    "four_tube_health_interpretation": "四管健康报告智能解读",
    "leakage_burst_analysis": "泄爆分析",
    "img_diag_defect_ident": "缺陷识别看图诊断",
    "img_diag_leakage_burst": "泄爆分析看图诊断",
}

# 数据计划子任务送 NL2SQL 时的额外约束（抑制臆造机组/墙别 WHERE）
_PLAN_TASK_SCOPE_GUARD_CN = "若用户未指定机组/区域，则不要在 WHERE 中臆造具体锅炉名或墙别。"
_TIME_REWRITE_WARNING_CN: dict[str, str] = {
    "anchor_lookback_skipped_no_anchor": (
        "用户未提供可解析的事故时刻，plan 要求的「锚点向前 N 天」回溯窗未生效；"
        "请在用户问题中补充事故发生时间。"
    ),
    "anchor_fallback_now": (
        "用户未提供事故时刻，已暂以当前时刻 NOW() 为锚点合成近 N 天回溯窗（结果仅供参考，"
        "建议在报告中提示用户补充准确事故时间）。"
    ),
    "no_parsed_time_window": (
        "用户问句未解析到时间窗；若 NL2SQL 含 @t_start/@t_end 将拒绝执行，"
        "请在用户问题中补充时间范围或事故时刻。"
    ),
}
_UNRESOLVED_TIME_PLACEHOLDER_WARNING = (
    "部分 NL2SQL 因时间占位符未替换而失败，请在用户问题中补充明确时间或事故时刻。"
)
_IMG_DIAG_SQL_PLACEHOLDER_CN = (
    "【SQL占位符强制】须使用 @t_start/@t_end（事实表/运行记录时间列写 >= @t_start AND < @t_end，左闭右开；"
    "禁止写死日期字面量）及 @unit_keyword、@device_keyword、@piperow_keyword、@row_no、@tube_no"
    "（须配 IS NULL/空串 guard，写法见 img_diag_*_plan_reference_sql.sql）；禁止硬编码示例锅炉名或区域。"
)
_IMG_DIAG_SQL_PLACEHOLDER_LEAKAGE_CN = (
    f"{_IMG_DIAG_SQL_PLACEHOLDER_CN}"
    "本场景 plan 含「锚点向前N天」时，@t_start/@t_end 由 NL2SQL 按用户 query 解析的事故锚点合成回溯窗后改写。"
)
# 规划前 RAG：写入「请结合以下规则线索」的条数（命名空间间轮询取值）
_PLAN_GUIDE_MAX_SNIPPETS = 4
# finished.meta.rag_citations：不展示 NL2SQL 取数链路的库表/QA 命名空间（与 acquire_data 内 RAG 一致）
_ANALYSIS_RAG_CITATIONS_EXCLUDED_NAMESPACES = frozenset(
    {"nl2sql_schema", "nl2sql_biz_knowledge", "nl2sql_qa_examples"}
)
# 超温专项 business RAG query boost（方案 B：仅追加检索词，不改 namespace）
_OVERHEAT_BUSINESS_RAG_BOOST = (
    "规格材质 受热面材质 管材 钢号 超温 蠕变 氧化皮 金相劣化 耐温性能 爆管"
)
_DEFECT_IDENT_BUSINESS_RAG_BOOST = (
    "缺陷识别 飞灰冲刷 点蚀 腐蚀 胀粗 裂纹 焊口 防磨瓦 氧化皮 "
    "打磨补焊 换管 防磨护瓦 运行监护 复测周期 测厚 无损检测 处置案例"
)
_LEAKAGE_BURST_IMG_DIAG_BUSINESS_RAG_BOOST = (
    "泄爆分析 爆管 泄漏 超温热应力 飞灰冲刷磨损 烟气腐蚀 水汽侧腐蚀 "
    "焊接缺陷 材质缺陷 运行操作偏差 直接原因 劣化因素 根因 "
    "历史事故案例 典型故障处理 标准规程 防控技术 同类爆管预防 同区域改造"
)


@dataclass
class _PlanTask:
    item_id: str
    purpose: str
    question: str
    mandatory: bool = True
    dependency_ids: list[str] = field(default_factory=list)
    namespace_hint: str | None = None


@dataclass
class _Nl2SqlPipelineThroughRagContext:
    """NL2SQL 顺序路径中，自会话写入起至 `rag_enrichment` 结束的快照（与 `_run_with_nl2sql_sequential` 前半段一致）。"""

    request_id: str
    plan_id: str
    tasks: list[_PlanTask]
    nl2sql_calls: list[AnalysisNL2SQLCall]
    gathered_data: dict[str, list[dict]]
    task_status: dict[str, str]
    quality_report: dict[str, Any]
    context_snippets: list[str]
    plan_rag_sources: list[dict[str, Any]]
    biz_rag_sources: list[dict[str, Any]]
    rag_citations: list[dict[str, Any]]
    used_rag: bool
    used_plan_rag: bool
    used_business_rag: bool
    intent_version: str
    data_plan_version: str
    plan_template_version: str
    planner_warnings: list[str]
    intent_obj: AnalysisIntentLLMOutput
    node_latency_ms: dict[str, int]
    node_status: dict[str, str]
    degrade_reasons: list[str]


@dataclass
class _SynthesisRunOutcome:
    """单次 synthesis 执行结果（v1 或 v2）。"""

    summary: str
    synthesis_version: str
    strategy_configured: str
    strategy_effective: str
    strategy_fallback_reason: str | None = None
    v2_tables: list[dict[str, Any]] = field(default_factory=list)
    v2_charts: list[dict[str, Any]] = field(default_factory=list)
    v2_sections: list[dict[str, Any]] = field(default_factory=list)
    slot_trace: list[dict[str, Any]] = field(default_factory=list)


class AnalysisGraphRunner:
    """
    综合分析编排内核：payload（给定载荷）与 nl2sql（多步查库）共用依赖（会话、LLM、RAG、NL2SQL、提示词）。

    图节点名与 Prometheus `analysis_node_latency_seconds` 的 `node` 标签一致；trace 中 `execution_summary.graph_nodes`
    记录节点顺序；可选 LangGraph checkpoint（`AnalysisConfig.checkpoint_*`）。
    **`acquire_data`**（`_execute_data_plan`）默认按依赖分层 **并行** 调度 NL2SQL 子调用（**`AnalysisConfig.nl2sql_acquire_*`**）。
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
    def _should_skip_optional_overheat_q5(
        task: _PlanTask,
        analysis_type: str,
        gathered_data: dict[str, list[dict]],
    ) -> bool:
        """q1 无超温行时跳过 optional q5，避免对 sis_pi_data 做无效大表扫描。"""
        if task.item_id != "q5" or task.mandatory:
            return False
        if "overheat" not in (analysis_type or ""):
            return False
        return not (gathered_data.get("q1") or [])

    @staticmethod
    def _skipped_plan_call(task: _PlanTask, *, error: str) -> AnalysisNL2SQLCall:
        return AnalysisNL2SQLCall(
            item_id=task.item_id,
            purpose=task.purpose,
            question=task.question,
            sql="",
            row_count=0,
            status="skipped",
            attempts=0,
            dependency_ids=task.dependency_ids,
            error=error,
        )

    @staticmethod
    def _task_status_from_call(task: _PlanTask, call: AnalysisNL2SQLCall) -> str:
        if call.status == "success":
            return "success"
        if call.status == "skipped":
            return "mandatory_failed" if task.mandatory else "optional_skipped"
        return "mandatory_failed" if task.mandatory else "optional_failed"

    @staticmethod
    def _safe_doc_id(chunk: Any) -> str:
        meta = getattr(chunk, "metadata", None) or {}
        doc_id = meta.get("doc_id") or meta.get("document_id") or getattr(chunk, "chunk_id", None) or getattr(chunk, "doc_name", None)
        return str(doc_id) if doc_id is not None else ""

    @staticmethod
    def _analysis_rag_citation_namespace_allowed(namespace: str | None) -> bool:
        ns = (namespace or "").strip()
        return ns not in _ANALYSIS_RAG_CITATIONS_EXCLUDED_NAMESPACES

    @classmethod
    def _filter_analysis_rag_citation_chunks(
        cls, chunks: list[RetrievedChunk] | None
    ) -> list[RetrievedChunk]:
        if not chunks:
            return []
        return [c for c in chunks if cls._analysis_rag_citation_namespace_allowed(getattr(c, "namespace", None))]

    @classmethod
    def _build_analysis_rag_citations(
        cls,
        *,
        plan_chunks: list[RetrievedChunk] | None = None,
        business_chunks: list[RetrievedChunk] | None = None,
        max_items: int = 32,
    ) -> list[dict[str, Any]]:
        """
        合并规划前 + 业务 RAG 分片，转为与智能客服一致的 rag_citations（含 original_content_url）。

        排除 nl2sql_schema / nl2sql_biz_knowledge / nl2sql_qa_examples，避免 acquire_data 内
        NL2SQL RAG 与库表知识库片段出现在结束帧；plan_context 若命中上述命名空间亦不在 citations 展示。
        """
        merged: list[RetrievedChunk] = []
        merged.extend(cls._filter_analysis_rag_citation_chunks(plan_chunks))
        merged.extend(cls._filter_analysis_rag_citation_chunks(business_chunks))
        cites = chunks_to_rag_citations(merged, max_items=max_items)
        return [
            c
            for c in cites
            if cls._analysis_rag_citation_namespace_allowed(
                str(c.get("namespace") or "") if c.get("namespace") is not None else None
            )
        ]

    def _retrieve_rag_with_sources(
        self,
        *,
        query: str,
        namespace: str | None,
        top_k: int,
        scene: str = "analysis",
        rerank_query: str | None = None,
        exclude_namespaces: frozenset[str] | None = None,
    ) -> tuple[list[str], list[dict[str, Any]], list[RetrievedChunk]]:
        """
        统一 RAG 检索输出：
        - snippets: 供 LLM 使用的文本片段；
        - sources: 审计证据（namespace/doc_id/score）；
        - chunks: 原始分片（用于 rag_citations）。
        """
        # 优先使用 retrieve_chunks（可拿到 doc_id/score），否则回退 retrieve_context 文本检索。
        rag_svc = getattr(self._hybrid_rag, "_rag_service", None)
        if rag_svc is not None and hasattr(rag_svc, "retrieve_chunks"):
            retrieve_kw: dict[str, Any] = {
                "query": query,
                "top_k": top_k,
                "namespace": namespace,
                "scene": scene,
                "rerank_query": rerank_query,
            }
            if exclude_namespaces:
                retrieve_kw["exclude_namespaces"] = sorted(exclude_namespaces)
            chunks = list(rag_svc.retrieve_chunks(**retrieve_kw) or [])
            snippets = [getattr(c, "text", "") for c in chunks if getattr(c, "text", "")]
            sources = [
                {
                    "namespace": getattr(c, "namespace", None) or namespace,
                    "doc_id": self._safe_doc_id(c),
                    "score": getattr(c, "score", None),
                }
                for c in chunks
            ]
            return snippets, sources, chunks
        try:
            snippets = self._hybrid_rag.retrieve(query, namespace=namespace, top_k=top_k)
        except TypeError:
            snippets = self._hybrid_rag.retrieve(query, namespace=namespace)
        sources = [{"namespace": namespace or "global", "doc_id": "", "score": None} for _ in snippets]
        return list(snippets), sources, []

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

    @staticmethod
    def _compose_plan_task_question(user_query: str, specific_question: str) -> str:
        """
        数据计划子任务问句：统一为「用户原句 + 子任务具体描述」，并附加机组/区域约束说明。
        """
        uq = (user_query or "").strip()
        sq = (specific_question or "").strip()
        if uq and sq:
            body = f"{uq}。{sq}"
        elif uq:
            body = uq
        else:
            body = sq
        if not body:
            return ""
        return f"{body}。{_PLAN_TASK_SCOPE_GUARD_CN}"

    @staticmethod
    def _img_diag_q2_plan_tasks(*, query: str, ph: str, leakage: bool) -> list[_PlanTask]:
        """检修处置 q2a～q2e：与 reference SQL 一一对应，每条独立 NL2SQL 调用。"""
        compose = AnalysisGraphRunner._compose_plan_task_question
        prefix = "在事故锚点向前3天内" if leakage else ""
        p = f"{prefix}，" if prefix else ""
        return [
            _PlanTask(
                "q2a",
                "检修处置-近3次壁厚",
                compose(
                    query,
                    f"{p}查询近3次壁厚测量记录（overhaul_boiler→overhaul_record mark_type=1→overhaul_record_tubes，LIMIT 3）。{ph}",
                ),
                mandatory=True,
            ),
            _PlanTask(
                "q2b",
                "检修处置-减薄速率",
                compose(
                    query,
                    f"{p}查询年平均减薄速率（overhaul_boiler JOIN overhaul_thickness_rate）。{ph}",
                ),
                mandatory=True,
            ),
            _PlanTask(
                "q2c",
                "检修处置-泄爆泄漏履历",
                compose(
                    query,
                    f"{p}查询近50次泄爆/泄漏记录（overhual_leakage）。{ph}",
                ),
                mandatory=True,
            ),
            _PlanTask(
                "q2d",
                "检修处置-遗留问题",
                compose(
                    query,
                    f"{p}查询近50条遗留问题及处置结果（overhaul_legacy_problem）。{ph}",
                ),
                mandatory=True,
            ),
            _PlanTask(
                "q2e",
                "检修处置-补焊换管",
                compose(
                    query,
                    f"{p}查询近50条补焊/换管记录（is_change=1 或 mark_type=2）。{ph}",
                ),
                mandatory=True,
            ),
        ]

    @staticmethod
    def _plan_context_snippets_for_guide(plan_context: list[str], *, max_items: int | None = None) -> list[str]:
        """
        规则线索：按 nl2sql_schema → biz → qa 三组（每组至多 3 条）做轮询取前 N 条，
        避免全部被单一命名空间前几块占满。
        """
        if not plan_context:
            return []
        cap = max_items if max_items is not None else _PLAN_GUIDE_MAX_SNIPPETS
        bucket_size = 3
        buckets: list[list[str]] = []
        i = 0
        while i < len(plan_context):
            buckets.append(plan_context[i : i + bucket_size])
            i += bucket_size
        out: list[str] = []
        round_idx = 0
        while len(out) < cap:
            progressed = False
            for b in buckets:
                if round_idx < len(b):
                    out.append(b[round_idx])
                    progressed = True
                    if len(out) >= cap:
                        return out
            if not progressed:
                break
            round_idx += 1
        return out

    @classmethod
    def _plan_context_guide_text(cls, plan_context: list[str]) -> str:
        parts = cls._plan_context_snippets_for_guide(plan_context, max_items=_PLAN_GUIDE_MAX_SNIPPETS)
        return "；".join(parts)

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
                    question=self._compose_plan_task_question(req.query, f"补充查询与「{h}」直接相关的数据"),
                    mandatory=False,
                )
            )

    @staticmethod
    def _apply_plan_context_guide(tasks: list[_PlanTask], plan_context: list[str]) -> None:
        if not plan_context or not tasks:
            return
        guide = AnalysisGraphRunner._plan_context_guide_text(plan_context)
        if not guide:
            return
        for task in tasks:
            q = (task.question or "").rstrip()
            if not q:
                continue
            # 子任务已以句末标点结尾时不再插入第二个「。」（避免出现。。）
            if q[-1] in ("。", ".", "！", "!", "？", "?"):
                task.question = f"{q}请结合以下规则线索：{guide}"
            else:
                task.question = f"{q}。请结合以下规则线索：{guide}"

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
                    question=self._compose_plan_task_question(req.query, it.question.strip())[:4000],
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
        if req.analysis_type == "maintenance_strategy":
            schema = (
                '{"response_mode":"FULL|SCOPED|WINDOW|FEASIBILITY|RUN_ADVICE",'
                '"goals":["string"],"key_entities":["string"],"time_scope_hint":"string",'
                '"scope_devices":["string"],"target_window":"string",'
                '"focus_entities":["string"],"output_focus":["string"],'
                '"resource_hints":["string"],"data_domains":["string"]}'
            )
        else:
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
        g = StateGraph(AnalysisGraphState)
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
        g = StateGraph(AnalysisGraphState)
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
            context_snippets, rag_sources, biz_chunks = self._retrieve_business_rag(req.query, at)
            used_rag = len(context_snippets) > 0
            rag_citations = self._build_analysis_rag_citations(business_chunks=biz_chunks)
            ms = int((perf_counter() - t_rag) * 1000)
            ANALYSIS_NODE_LATENCY.labels(node="rag_enrichment", analysis_type=at).observe(perf_counter() - t_rag)
            return {
                "context_snippets": context_snippets,
                "rag_sources": rag_sources,
                "rag_citations": rag_citations,
                "used_rag": used_rag,
                "node_latency_ms": self._merge_latency(state, "rag_enrichment", ms),
                "node_status": self._merge_status(state, "rag_enrichment", "success"),
            }
        return {
            "context_snippets": [],
            "rag_sources": [],
            "rag_citations": [],
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
        synthesis_prompt, synthesis_version = self._resolve_synthesis_stage_template(
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
        syn_outcome = await self._execute_synthesis(
            query=req.query,
            analysis_type=at,
            data_mode="payload",
            data_blob=req.payload,
            context_snippets=context_snippets,
            system_prompt=synthesis_prompt,
            chart_mode=req.options.chart_mode,
            user_id=req.user_id,
        )
        summary = syn_outcome.summary
        synthesis_version = syn_outcome.synthesis_version
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
            v2_tables=syn_outcome.v2_tables,
            v2_charts=syn_outcome.v2_charts,
            v2_sections=syn_outcome.v2_sections,
            synthesis_strategy_effective=syn_outcome.strategy_effective,
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
                "synthesis_strategy": syn_outcome.strategy_configured,
                "synthesis_strategy_effective": syn_outcome.strategy_effective,
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
            rag_citations=list(state.get("rag_citations") or []),
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
        plan_context, plan_rag_sources, plan_rag_chunks = self._retrieve_plan_rag(
            req.query, at, req.options.enable_rag
        )
        ms = int((perf_counter() - t0) * 1000)
        ANALYSIS_NODE_LATENCY.labels(node="plan_context_rag", analysis_type=at).observe(perf_counter() - t0)
        return {
            "plan_context": plan_context,
            "plan_rag_sources": plan_rag_sources,
            "plan_rag_chunks": plan_rag_chunks,
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
        """图节点：按 plan_tasks 执行 NL2SQL（`_execute_data_plan`：默认同依赖层并行 query，见配置项）。"""
        req = AnalysisNL2SQLRequest.model_validate(state["nl2sql_request"])
        raw_tasks = list(state.get("plan_tasks") or [])
        tasks = [self._plan_task_from_dict(x) for x in raw_tasks if isinstance(x, dict)]
        analysis_request_id = str(state.get("request_id") or "").strip() or None
        nl2sql_calls, gathered_data, task_status, acquire_latency_ms = await self._execute_data_plan(
            req=req, tasks=tasks, analysis_request_id=analysis_request_id
        )
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
            context_snippets, biz_rag_sources, biz_rag_chunks = self._retrieve_business_rag(req.query, at)
            used_business_rag = len(context_snippets) > 0
            ms = int((perf_counter() - t_rag) * 1000)
            ANALYSIS_NODE_LATENCY.labels(node="rag_enrichment", analysis_type=at).observe(perf_counter() - t_rag)
            plan_src = list(state.get("plan_rag_sources") or [])
            plan_chunks = list(state.get("plan_rag_chunks") or [])
            merged_sources = (plan_src + biz_rag_sources)[:64]
            rag_citations = self._build_analysis_rag_citations(
                plan_chunks=cast(list[RetrievedChunk], plan_chunks),
                business_chunks=biz_rag_chunks,
            )
            used_rag = bool(plan_src) or used_business_rag
            return {
                "context_snippets": context_snippets,
                "rag_sources": merged_sources,
                "rag_citations": rag_citations,
                "used_rag": used_rag,
                "node_latency_ms": self._merge_latency(state, "rag_enrichment", ms),
                "node_status": self._merge_status(state, "rag_enrichment", "success"),
            }
        plan_src = list(state.get("plan_rag_sources") or [])
        plan_chunks = list(state.get("plan_rag_chunks") or [])
        rag_citations = self._build_analysis_rag_citations(
            plan_chunks=cast(list[RetrievedChunk], plan_chunks),
        )
        return {
            "context_snippets": [],
            "rag_sources": plan_src[:64],
            "rag_citations": rag_citations,
            "used_rag": bool(plan_src),
            "node_latency_ms": self._merge_latency(state, "rag_enrichment", 0),
            "node_status": self._merge_status(state, "rag_enrichment", "success"),
        }

    async def _lg_nl2sql_synthesis(self, state: dict[str, Any]) -> dict[str, Any]:
        req = AnalysisNL2SQLRequest.model_validate(state["nl2sql_request"])
        at = req.analysis_type
        t_syn = perf_counter()
        synthesis_prompt, synthesis_version = self._resolve_synthesis_stage_template(
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
        syn_outcome = await self._execute_synthesis(
            query=req.query,
            analysis_type=at,
            data_mode="nl2sql",
            data_blob=gathered_data,
            context_snippets=context_snippets,
            system_prompt=synthesis_prompt,
            planning_context=planning_ctx,
            chart_mode=req.options.chart_mode,
            user_id=req.user_id,
        )
        summary = syn_outcome.summary
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
            v2_tables=syn_outcome.v2_tables,
            v2_charts=syn_outcome.v2_charts,
            v2_sections=syn_outcome.v2_sections,
            synthesis_strategy_effective=syn_outcome.strategy_effective,
        )
        ms = int((perf_counter() - t_syn) * 1000)
        ANALYSIS_NODE_LATENCY.labels(node="synthesis", analysis_type=at).observe(perf_counter() - t_syn)
        return {
            "summary": summary,
            "structured_report": structured_report,
            "suggestions": suggestions,
            "synthesis_version": syn_outcome.synthesis_version,
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
            rag_citations=list(state.get("rag_citations") or []),
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
            context_snippets, rag_sources, biz_chunks = self._retrieve_business_rag(req.query, req.analysis_type)
            used_rag = len(context_snippets) > 0
            rag_citations = self._build_analysis_rag_citations(business_chunks=biz_chunks)
            self._mark_node(node_latency_ms, node_status, "rag_enrichment", t_rag, ok=True)
            ANALYSIS_NODE_LATENCY.labels(node="rag_enrichment", analysis_type=req.analysis_type).observe(
                (perf_counter() - t_rag)
            )
        else:
            rag_citations = []

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
        synthesis_prompt, synthesis_version = self._resolve_synthesis_stage_template(
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
        syn_outcome = await self._execute_synthesis(
            query=req.query,
            analysis_type=req.analysis_type,
            data_mode="payload",
            data_blob=req.payload,
            context_snippets=context_snippets,
            system_prompt=synthesis_prompt,
            chart_mode=req.options.chart_mode,
            user_id=req.user_id,
        )
        summary = syn_outcome.summary
        synthesis_version = syn_outcome.synthesis_version
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
            v2_tables=syn_outcome.v2_tables,
            v2_charts=syn_outcome.v2_charts,
            v2_sections=syn_outcome.v2_sections,
            synthesis_strategy_effective=syn_outcome.strategy_effective,
        )
        self._mark_node(node_latency_ms, node_status, "synthesis", t_syn, ok=True)
        ANALYSIS_NODE_LATENCY.labels(node="synthesis", analysis_type=req.analysis_type).observe(
            (perf_counter() - t_syn)
        )

        self._conv.append_assistant_message(req.user_id, req.session_id, summary)
        evidence = AnalysisEvidence(
            used_rag=used_rag,
            rag_sources=rag_sources[:32],
            rag_citations=rag_citations,
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
                "synthesis_strategy": syn_outcome.strategy_configured,
                "synthesis_strategy_effective": syn_outcome.strategy_effective,
            },
            execution_summary={
                "analysis_type": req.analysis_type,
                "data_mode": "payload",
                "started_at": datetime.now(timezone.utc).isoformat(),
                "used_rag": used_rag,
                "synthesis_strategy": syn_outcome.strategy_configured,
                "synthesis_strategy_effective": syn_outcome.strategy_effective,
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

    async def _run_nl2sql_pipeline_through_rag(
        self, req: AnalysisNL2SQLRequest, *, request_id: str | None = None
    ) -> _Nl2SqlPipelineThroughRagContext:
        """顺序路径中与 LangGraph 前半段等价：会话 → 规划 RAG → 意图/计划 → 取数 → 质量门 → 业务 RAG（供同步与流式 synthesis 复用）。"""
        t_pipeline = perf_counter()
        request_id = (request_id or "").strip() or f"anl_{uuid4().hex[:12]}"
        plan_id = f"plan_{uuid4().hex[:10]}"
        node_latency_ms: dict[str, int] = {}
        node_status: dict[str, str] = {}
        degrade_reasons: list[str] = []
        self._conv.append_user_message(req.user_id, req.session_id, req.query)

        t_pc = perf_counter()
        plan_context, plan_rag_sources, plan_rag_chunks = self._retrieve_plan_rag(
            req.query, req.analysis_type, req.options.enable_rag
        )
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

        plan_template_version = self._resolve_plan_template_version_label(req)
        nl2sql_calls, gathered_data, task_status, acquire_latency_ms = await self._execute_data_plan(
            req=req,
            tasks=tasks,
            analysis_request_id=request_id,
            plan_template_version=plan_template_version,
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
        biz_rag_chunks: list[RetrievedChunk] = []
        used_business_rag = False
        if req.options.enable_rag:
            t_rag = perf_counter()
            context_snippets, biz_rag_sources, biz_rag_chunks = self._retrieve_business_rag(
                req.query, req.analysis_type
            )
            used_business_rag = len(context_snippets) > 0
            self._mark_node(node_latency_ms, node_status, "rag_enrichment", t_rag, ok=True)
            ANALYSIS_NODE_LATENCY.labels(node="rag_enrichment", analysis_type=req.analysis_type).observe(
                (perf_counter() - t_rag)
            )

        used_plan_rag = bool(plan_rag_sources)
        used_rag = used_plan_rag or used_business_rag
        rag_citations = self._build_analysis_rag_citations(
            plan_chunks=plan_rag_chunks if req.options.enable_rag else None,
            business_chunks=biz_rag_chunks if req.options.enable_rag else None,
        )

        pipeline_ms = int((perf_counter() - t_pipeline) * 1000)
        logger.info(
            "analysis_nl2sql_pipeline_summary %s",
            json.dumps(
                {
                    "request_id": request_id,
                    "plan_id": plan_id,
                    "analysis_type": req.analysis_type,
                    "user_id": req.user_id,
                    "session_id": req.session_id,
                    "pipeline_ms": pipeline_ms,
                    "node_latency_ms": dict(node_latency_ms),
                    "nl2sql_calls": [
                        {
                            "item_id": c.item_id,
                            "status": c.status,
                            "row_count": c.row_count,
                            "attempts": c.attempts,
                            "error": (c.error or "")[:400],
                        }
                        for c in nl2sql_calls
                    ],
                    "planner_warnings": planner_warnings,
                    "degrade_reasons": degrade_reasons,
                },
                ensure_ascii=False,
            ),
        )

        return _Nl2SqlPipelineThroughRagContext(
            request_id=request_id,
            plan_id=plan_id,
            tasks=tasks,
            nl2sql_calls=nl2sql_calls,
            gathered_data=gathered_data,
            task_status=task_status,
            quality_report=quality_report,
            context_snippets=context_snippets,
            plan_rag_sources=plan_rag_sources,
            biz_rag_sources=biz_rag_sources,
            rag_citations=rag_citations,
            used_rag=used_rag,
            used_plan_rag=used_plan_rag,
            used_business_rag=used_business_rag,
            intent_version=intent_version,
            data_plan_version=data_plan_version,
            plan_template_version=plan_template_version,
            planner_warnings=planner_warnings,
            intent_obj=intent_obj,
            node_latency_ms=node_latency_ms,
            node_status=node_status,
            degrade_reasons=degrade_reasons,
        )

    async def _run_with_nl2sql_sequential(self, req: AnalysisNL2SQLRequest) -> AnalysisV2Result:
        """无 LangGraph 时的顺序执行路径，与 nl2sql 图节点语义对齐。"""
        ctx = await self._run_nl2sql_pipeline_through_rag(req)

        t_syn = perf_counter()
        synthesis_prompt, synthesis_version = self._resolve_synthesis_stage_template(
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
            planning_ctx = json.dumps(ctx.intent_obj.model_dump(mode="json"), ensure_ascii=False)
        syn_outcome = await self._execute_synthesis(
            query=req.query,
            analysis_type=req.analysis_type,
            data_mode="nl2sql",
            data_blob=ctx.gathered_data,
            context_snippets=ctx.context_snippets,
            system_prompt=synthesis_prompt,
            planning_context=planning_ctx,
            chart_mode=req.options.chart_mode,
            user_id=req.user_id,
            task_status=ctx.task_status,
        )
        return self._finalize_nl2sql_sequential_v2(
            req,
            ctx,
            summary=syn_outcome.summary,
            synthesis_version=syn_outcome.synthesis_version,
            report_version=report_version,
            synthesis_started=t_syn,
            syn_outcome=syn_outcome,
        )

    def _finalize_nl2sql_sequential_v2(
        self,
        req: AnalysisNL2SQLRequest,
        ctx: _Nl2SqlPipelineThroughRagContext,
        *,
        summary: str,
        synthesis_version: str,
        report_version: str,
        synthesis_started: float,
        syn_outcome: _SynthesisRunOutcome | None = None,
    ) -> AnalysisV2Result:
        """顺序 NL2SQL 路径：在已有 summary 上组装 structured_report / evidence / trace（同步与流式后处理共用）。"""
        request_id = ctx.request_id
        plan_id = ctx.plan_id
        tasks = ctx.tasks
        nl2sql_calls = ctx.nl2sql_calls
        gathered_data = ctx.gathered_data
        quality_report = ctx.quality_report
        plan_rag_sources = ctx.plan_rag_sources
        biz_rag_sources = ctx.biz_rag_sources
        rag_citations = ctx.rag_citations
        used_rag = ctx.used_rag
        intent_version = ctx.intent_version
        data_plan_version = ctx.data_plan_version
        planner_warnings = ctx.planner_warnings
        node_latency_ms = ctx.node_latency_ms
        node_status = ctx.node_status
        degrade_reasons = ctx.degrade_reasons

        suggestions = self._build_suggestions(summary, req.analysis_type, req.options.max_suggestions)
        strategy_eff = syn_outcome.strategy_effective if syn_outcome else "v1"
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
            v2_tables=syn_outcome.v2_tables if syn_outcome else None,
            v2_charts=syn_outcome.v2_charts if syn_outcome else None,
            v2_sections=syn_outcome.v2_sections if syn_outcome else None,
            synthesis_strategy_effective=strategy_eff,
        )
        self._mark_node(node_latency_ms, node_status, "synthesis", synthesis_started, ok=True)
        ANALYSIS_NODE_LATENCY.labels(node="synthesis", analysis_type=req.analysis_type).observe(
            (perf_counter() - synthesis_started)
        )

        self._conv.append_assistant_message(req.user_id, req.session_id, summary)
        evidence = AnalysisEvidence(
            used_rag=used_rag,
            rag_sources=(plan_rag_sources + biz_rag_sources)[:64],
            rag_citations=rag_citations,
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
                "synthesis_strategy": (
                    syn_outcome.strategy_configured if syn_outcome else self._configured_synthesis_strategy(req.analysis_type)
                ),
                "synthesis_strategy_effective": strategy_eff,
            },
            execution_summary={
                "analysis_type": req.analysis_type,
                "data_mode": "nl2sql",
                "started_at": datetime.now(timezone.utc).isoformat(),
                "used_rag": used_rag,
                "planned_calls": len(tasks),
                "synthesis_strategy": (
                    syn_outcome.strategy_configured if syn_outcome else self._configured_synthesis_strategy(req.analysis_type)
                ),
                "synthesis_strategy_effective": strategy_eff,
                "synthesis_fallback_reason": (
                    syn_outcome.strategy_fallback_reason if syn_outcome else None
                ),
                "synthesis_slot_trace": syn_outcome.slot_trace if syn_outcome else [],
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

    def _synthesis_strategy_for_type(self, analysis_type: str) -> str | None:
        """按 analysis_type 读取专项 synthesis 策略覆盖（v1/v2）。"""
        mapping: dict[str, str | None] = {
            "overheat_guidance": self._analysis_cfg.synthesis_strategy_overheat_guidance,
            "maintenance_strategy": self._analysis_cfg.synthesis_strategy_maintenance_strategy,
            "four_tube_health_interpretation": (
                self._analysis_cfg.synthesis_strategy_four_tube_health_interpretation
            ),
            "leakage_burst_analysis": self._analysis_cfg.synthesis_strategy_leakage_burst_analysis,
            "custom": self._analysis_cfg.synthesis_strategy_custom,
        }
        per = mapping.get(analysis_type)
        if per in ("v1", "v2"):
            return per
        return None

    def _configured_synthesis_strategy(self, analysis_type: str) -> str:
        if analysis_type in ("img_diag_defect_ident", "img_diag_leakage_burst"):
            return "v1"
        per = self._synthesis_strategy_for_type(analysis_type)
        if per:
            return per
        base = (self._analysis_cfg.synthesis_strategy or "v1").strip().lower()
        return base if base in ("v1", "v2") else "v1"

    def _resolve_synthesis_strategy_effective(
        self, analysis_type: str
    ) -> tuple[str, str | None]:
        configured = self._configured_synthesis_strategy(analysis_type)
        if configured == "v2" and not synthesis_v2_registry_available(analysis_type):
            return "v1", "v2_registry_missing"
        return configured, None

    def _plan_template_version_for_type(self, analysis_type: str) -> str | None:
        mapping: dict[str, str | None] = {
            "overheat_guidance": self._analysis_cfg.plan_template_version_overheat_guidance,
            "maintenance_strategy": self._analysis_cfg.plan_template_version_maintenance_strategy,
            "four_tube_health_interpretation": (
                self._analysis_cfg.plan_template_version_four_tube_health_interpretation
            ),
            "leakage_burst_analysis": self._analysis_cfg.plan_template_version_leakage_burst_analysis,
            "img_diag_defect_ident": self._analysis_cfg.plan_template_version_img_diag_defect_ident,
            "img_diag_leakage_burst": self._analysis_cfg.plan_template_version_img_diag_leakage_burst,
            "custom": self._analysis_cfg.plan_template_version_custom,
        }
        per = (mapping.get(analysis_type) or "").strip()
        return per or None

    def _synthesis_template_version_for_type(self, analysis_type: str) -> str | None:
        mapping: dict[str, str | None] = {
            "overheat_guidance": self._analysis_cfg.synthesis_template_version_overheat_guidance,
            "maintenance_strategy": self._analysis_cfg.synthesis_template_version_maintenance_strategy,
            "four_tube_health_interpretation": (
                self._analysis_cfg.synthesis_template_version_four_tube_health_interpretation
            ),
            "leakage_burst_analysis": (
                self._analysis_cfg.synthesis_template_version_leakage_burst_analysis
            ),
            "img_diag_defect_ident": (
                self._analysis_cfg.synthesis_template_version_img_diag_defect_ident
            ),
            "img_diag_leakage_burst": (
                self._analysis_cfg.synthesis_template_version_img_diag_leakage_burst
            ),
            "custom": self._analysis_cfg.synthesis_template_version_custom,
        }
        per = (mapping.get(analysis_type) or "").strip()
        return per or None

    @staticmethod
    def _merge_template_version(
        *,
        per_type: str | None,
        global_ver: str | None,
        effective_strategy: str,
    ) -> str | None:
        """
        专项 > 全局；均未配置时：effective v2 默认 v2，v1 返回 None（hash 选版）。
        """
        explicit = (per_type or "").strip() or (global_ver or "").strip() or None
        if explicit:
            return explicit
        if effective_strategy == "v2":
            return "v2"
        return None

    def _resolve_plan_template_version(self, analysis_type: str) -> str | None:
        eff, _ = self._resolve_synthesis_strategy_effective(analysis_type)
        return self._merge_template_version(
            per_type=self._plan_template_version_for_type(analysis_type),
            global_ver=self._analysis_cfg.plan_template_version,
            effective_strategy=eff,
        )

    def _resolve_plan_template_version_label(self, req: AnalysisNL2SQLRequest) -> str:
        """
        与 acquire_data / QA 闭环一致的 plan 模板版本标签。
        显式配置优先；否则与 _build_data_plan_from_template 相同用 get_template(version=None) 探测命中版本。
        """
        explicit = self._resolve_plan_template_version(req.analysis_type)
        if explicit:
            return explicit
        scene = f"analysis_plan_{req.analysis_type}"
        tpl = self._prompts.get_template(scene=scene, user_id=req.user_id, version=None)
        if tpl is not None and getattr(tpl, "version", None):
            return str(tpl.version)
        return "unknown"

    def _resolve_synthesis_template_version(self, analysis_type: str) -> str | None:
        eff, _ = self._resolve_synthesis_strategy_effective(analysis_type)
        return self._merge_template_version(
            per_type=self._synthesis_template_version_for_type(analysis_type),
            global_ver=self._analysis_cfg.synthesis_template_version,
            effective_strategy=eff,
        )

    def _resolve_synthesis_stage_template(
        self,
        *,
        analysis_type: str,
        user_id: str,
        default_text: str,
    ) -> tuple[str, str]:
        """加载 analysis_synthesis 模板，应用 ANALYSIS_SYNTHESIS_TEMPLATE_VERSION(_<TYPE>)。"""
        tpl_ver = self._resolve_synthesis_template_version(analysis_type)
        return self._resolve_stage_template(
            stage="analysis_synthesis",
            analysis_type=analysis_type,
            user_id=user_id,
            default_text=default_text,
            template_version=tpl_ver,
        )

    def _make_synthesis_v2_engine(self) -> AnalysisSynthesisV2Engine:
        return AnalysisSynthesisV2Engine(
            llm_client=self._llm,
            prompts=self._prompts,
            gathered_json_max_chars=self._analysis_cfg.synthesis_gathered_json_max_chars,
            segment_max_tokens=self._analysis_cfg.synthesis_v2_segment_max_tokens,
            max_parallel_llm=self._analysis_cfg.synthesis_v2_max_parallel_llm,
            table_max_rows=self._analysis_cfg.synthesis_v2_table_max_rows,
            synthesis_timeout_seconds=self._analysis_cfg.synthesis_timeout_seconds,
            emit_structured_sse=self._analysis_cfg.synthesis_v2_enable_structured_sse_events,
            stream_chunk_chars=self._analysis_cfg.synthesis_v2_stream_chunk_chars,
            stream_chunk_delay_ms=self._analysis_cfg.synthesis_v2_stream_chunk_delay_ms,
            idle_heartbeat_seconds=self._analysis_cfg.synthesis_v2_idle_heartbeat_seconds,
            json_fallback=self._json_fallback,
        )

    def _resolve_overheat_report_context(
        self,
        analysis_type: str,
        query: str,
        gathered_data: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        if analysis_type != "overheat_guidance":
            return None
        from app.llm.graphs.overheat_synthesis_render import (
            enrich_overheat_report_context_from_gathered,
            infer_overheat_report_context,
        )

        ctx = infer_overheat_report_context(query)
        if gathered_data and isinstance(gathered_data, dict):
            ctx = enrich_overheat_report_context_from_gathered(ctx, gathered_data)
        return ctx

    async def _execute_synthesis(
        self,
        *,
        query: str,
        analysis_type: str,
        data_mode: str,
        data_blob: dict,
        context_snippets: list[str],
        system_prompt: str,
        planning_context: str | None = None,
        chart_mode: str = "auto",
        user_id: str = "",
        task_status: dict[str, str] | None = None,
    ) -> _SynthesisRunOutcome:
        configured = self._configured_synthesis_strategy(analysis_type)
        effective, fallback = self._resolve_synthesis_strategy_effective(analysis_type)
        if effective == "v2":
            engine = self._make_synthesis_v2_engine()
            gathered = data_blob if isinstance(data_blob, dict) else {}
            report_context = self._resolve_overheat_report_context(
                analysis_type, query, gathered
            )
            v2_result = await engine.run_sync(
                analysis_type=analysis_type,
                query=query,
                data_mode=data_mode,
                gathered_data=cast(dict[str, list[dict]], gathered),
                context_snippets=context_snippets,
                planning_context=planning_context,
                chart_mode=chart_mode,
                task_status=task_status,
                report_context=report_context,
            )
            return _SynthesisRunOutcome(
                summary=v2_result.summary,
                synthesis_version=v2_result.synthesis_version,
                strategy_configured=configured,
                strategy_effective="v2",
                strategy_fallback_reason=fallback,
                v2_tables=v2_result.tables,
                v2_charts=v2_result.charts,
                v2_sections=v2_result.sections,
                slot_trace=v2_result.slot_trace,
            )
        summary = await self._generate_summary(
            query=query,
            analysis_type=analysis_type,
            data_mode=data_mode,
            data_blob=data_blob,
            context_snippets=context_snippets,
            system_prompt=system_prompt,
            planning_context=planning_context,
        )
        _, synthesis_version = self._resolve_synthesis_stage_template(
            analysis_type=analysis_type,
            user_id=user_id,
            default_text=system_prompt,
        )
        return _SynthesisRunOutcome(
            summary=summary,
            synthesis_version=synthesis_version,
            strategy_configured=configured,
            strategy_effective="v1",
            strategy_fallback_reason=fallback,
        )

    async def _iter_synthesis_v2_stream(
        self,
        *,
        analysis_type: str,
        query: str,
        data_mode: str,
        gathered_data: dict[str, list[dict]],
        context_snippets: list[str],
        planning_context: str | None,
        chart_mode: str,
        task_status: dict[str, str] | None = None,
    ) -> AsyncIterator[tuple[dict[str, Any], _SynthesisRunOutcome | None]]:
        engine = self._make_synthesis_v2_engine()
        report_context = self._resolve_overheat_report_context(
            analysis_type, query, gathered_data
        )
        if self._analysis_cfg.synthesis_v2_stream_live_first:
            event_source = engine.iter_stream_events_live_first(
                analysis_type=analysis_type,
                query=query,
                data_mode=data_mode,
                gathered_data=gathered_data,
                context_snippets=context_snippets,
                planning_context=planning_context,
                chart_mode=chart_mode,
                task_status=task_status,
                report_context=report_context,
            )
        else:
            event_source = engine.iter_stream_events(
                analysis_type=analysis_type,
                query=query,
                data_mode=data_mode,
                gathered_data=gathered_data,
                context_snippets=context_snippets,
                planning_context=planning_context,
                chart_mode=chart_mode,
                task_status=task_status,
                report_context=report_context,
            )
        async for event, result in event_source:
            if result is not None:
                configured = self._configured_synthesis_strategy(analysis_type)
                _, fallback = self._resolve_synthesis_strategy_effective(analysis_type)
                yield (
                    {},
                    _SynthesisRunOutcome(
                        summary=result.summary,
                        synthesis_version=result.synthesis_version,
                        strategy_configured=configured,
                        strategy_effective="v2",
                        strategy_fallback_reason=fallback,
                        v2_tables=result.tables,
                        v2_charts=result.charts,
                        v2_sections=result.sections,
                        slot_trace=result.slot_trace,
                    ),
                )
            elif event:
                yield (event, None)

    def _build_summary_user_content(
        self,
        *,
        query: str,
        analysis_type: str,
        data_mode: str,
        data_blob: dict,
        context_snippets: list[str],
        planning_context: str | None = None,
    ) -> str:
        """synthesis 的 user 消息：本轮事实数据、RAG 与可选规划意图（不含预制 system 模板）。"""
        max_chars = int(self._analysis_cfg.synthesis_gathered_json_max_chars)
        data_preview = json.dumps(
            data_blob,
            ensure_ascii=False,
            default=self._json_fallback,
        )[:max_chars]
        rag_text = "\n".join(f"- {s}" for s in context_snippets[:8])
        pc = (planning_context or "").strip()
        planning_block = f"\n分阶段规划意图(结构化要点):\n{pc[:2000]}\n" if pc else ""
        return (
            f"分析类型: {analysis_type}\n"
            f"数据来源模式: {data_mode}\n"
            f"用户问题: {query}\n"
            f"{planning_block}"
            f"数据摘要(JSON截断): {data_preview}\n"
            f"RAG参考片段:\n{rag_text}"
        ).strip()

    def _build_summary_messages(
        self,
        *,
        query: str,
        analysis_type: str,
        data_mode: str,
        data_blob: dict,
        context_snippets: list[str],
        system_prompt: str,
        planning_context: str | None = None,
    ) -> list[dict[str, str]]:
        """与 `_generate_summary` / 流式 synthesis 共用：system=预制模板，user=事实与 RAG。"""
        system_content = (system_prompt or "").strip() or (
            "你是一名综合分析助手，请基于事实数据给出结论和建议。"
        )
        user_content = self._build_summary_user_content(
            query=query,
            analysis_type=analysis_type,
            data_mode=data_mode,
            data_blob=data_blob,
            context_snippets=context_snippets,
            planning_context=planning_context,
        )
        return [
            {"role": "system", "content": system_content},
            {"role": "user", "content": user_content},
        ]

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
        messages = self._build_summary_messages(
            query=query,
            analysis_type=analysis_type,
            data_mode=data_mode,
            data_blob=data_blob,
            context_snippets=context_snippets,
            system_prompt=system_prompt,
            planning_context=planning_context,
        )
        try:
            summary = await self._llm.chat(  # type: ignore[arg-type]
                model=None,
                messages=messages,
                timeout=self._analysis_cfg.synthesis_timeout_seconds,
                max_tokens=self._analysis_cfg.synthesis_max_tokens,
            )
            return summary
        except Exception:  # noqa: BLE001
            logger.exception("analysis graph summary generation failed")
            return "综合分析生成失败，已返回基础报告，请稍后重试。"

    async def _stream_summary_text(
        self,
        *,
        query: str,
        analysis_type: str,
        data_mode: str,
        data_blob: dict,
        context_snippets: list[str],
        system_prompt: str,
        planning_context: str | None = None,
    ) -> AsyncIterator[str]:
        """流式生成 summary（Markdown 文本增量），提示词与非流式 synthesis 一致。"""
        messages = self._build_summary_messages(
            query=query,
            analysis_type=analysis_type,
            data_mode=data_mode,
            data_blob=data_blob,
            context_snippets=context_snippets,
            system_prompt=system_prompt,
            planning_context=planning_context,
        )
        async for chunk in self._llm.stream_chat(  # type: ignore[union-attr]
            model=None,
            messages=messages,
            timeout=float(self._analysis_cfg.synthesis_timeout_seconds),
            max_tokens=self._analysis_cfg.synthesis_max_tokens,
        ):
            yield chunk

    async def iter_nl2sql_stream_events(
        self,
        req: AnalysisNL2SQLRequest,
        *,
        on_complete: Callable[[AnalysisV2Result], Awaitable[None]] | None = None,
    ) -> AsyncIterator[dict[str, Any]]:
        """
        NL2SQL 流式事件源：先 `_run_nl2sql_pipeline_through_rag`（此阶段无 SSE），再 synthesis 流式输出。

        Yields 事件 dict（字段 `event` 标识类型，详见 `app.api.analysis.run_analysis_with_nl2sql_stream`）：

        - `meta`：取数完成后首条
        - `summary_delta`：Markdown 增量（v1 整篇 LLM token；v2 按槽位顺序，见 `iter_stream_events`）
        - `synthesis_loading` / `table_payload` / `chart_payload`：仅 v2 且配置开启
        - `summary_complete` → `structured_async_enqueued` → `finished`：收尾三连（`finished` 为尾帧，与 AI 问答同形 `{"finished":true,"meta":{...}}`）

        结束后 `create_task(_nl2sql_stream_background_finalize)` 异步写完整 JSON + trace。
        """
        ANALYSIS_REQUEST_COUNT.labels(
            analysis_type=req.analysis_type, data_mode="nl2sql", status="started"
        ).inc()
        try:
            stream_request_id = f"anl_{uuid4().hex[:12]}"
            t_pipeline = perf_counter()
            logger.info(
                "analysis_nl2sql_stream_pipeline_start request_id=%s analysis_type=%s user_id=%s session_id=%s",
                stream_request_id,
                req.analysis_type,
                req.user_id,
                req.session_id,
            )
            ctx = await self._run_nl2sql_pipeline_through_rag(req, request_id=stream_request_id)
            synthesis_prompt, synthesis_version = self._resolve_synthesis_stage_template(
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
                planning_ctx = json.dumps(ctx.intent_obj.model_dump(mode="json"), ensure_ascii=False)

            configured_strategy = self._configured_synthesis_strategy(req.analysis_type)
            effective_strategy, _ = self._resolve_synthesis_strategy_effective(req.analysis_type)
            yield {
                "event": "meta",
                "request_id": ctx.request_id,
                "plan_id": ctx.plan_id,
                "analysis_type": req.analysis_type,
                "data_mode": "nl2sql",
                "orchestrator": "sequential_stream",
                "template_versions": {
                    "synthesis": synthesis_version,
                    "report": report_version,
                    "synthesis_strategy": configured_strategy,
                    "synthesis_strategy_effective": effective_strategy,
                },
            }

            t_syn = perf_counter()
            syn_outcome: _SynthesisRunOutcome | None = None
            try:
                if effective_strategy == "v2":
                    async for event, outcome in self._iter_synthesis_v2_stream(
                        analysis_type=req.analysis_type,
                        query=req.query,
                        data_mode="nl2sql",
                        gathered_data=ctx.gathered_data,
                        context_snippets=ctx.context_snippets,
                        planning_context=planning_ctx,
                        chart_mode=req.options.chart_mode,
                        task_status=ctx.task_status,
                    ):
                        if outcome is not None:
                            syn_outcome = outcome
                            continue
                        if event:
                            yield event
                else:
                    parts: list[str] = []
                    async for chunk in self._stream_summary_text(
                        query=req.query,
                        analysis_type=req.analysis_type,
                        data_mode="nl2sql",
                        data_blob=ctx.gathered_data,
                        context_snippets=ctx.context_snippets,
                        system_prompt=synthesis_prompt,
                        planning_context=planning_ctx,
                    ):
                        parts.append(chunk)
                        yield {"event": "summary_delta", "text": chunk}
                    summary_v1 = "".join(parts)
                    _, syn_ver = self._resolve_synthesis_stage_template(
                        analysis_type=req.analysis_type,
                        user_id=req.user_id,
                        default_text=synthesis_prompt,
                    )
                    syn_outcome = _SynthesisRunOutcome(
                        summary=summary_v1,
                        synthesis_version=syn_ver,
                        strategy_configured=configured_strategy,
                        strategy_effective="v1",
                    )
            except Exception:  # noqa: BLE001
                logger.exception("analysis nl2sql stream summary failed")
                fb = "综合分析生成失败，已返回基础报告，请稍后重试。"
                yield {"event": "summary_delta", "text": fb}
                syn_outcome = _SynthesisRunOutcome(
                    summary=fb,
                    synthesis_version=synthesis_version,
                    strategy_configured=configured_strategy,
                    strategy_effective=effective_strategy,
                )

            summary = syn_outcome.summary if syn_outcome else ""
            synthesis_ms = int((perf_counter() - t_syn) * 1000)
            yield {
                "event": "summary_complete",
                "request_id": ctx.request_id,
                "chars": len(summary),
                "synthesis_ms": synthesis_ms,
                "markdown": summary,
            }

            asyncio.create_task(
                self._nl2sql_stream_background_finalize(
                    req,
                    ctx,
                    summary=summary,
                    synthesis_version=(
                        syn_outcome.synthesis_version if syn_outcome else synthesis_version
                    ),
                    report_version=report_version,
                    synthesis_started=t_syn,
                    on_complete=on_complete,
                    syn_outcome=syn_outcome,
                )
            )
            yield {"event": "structured_async_enqueued", "request_id": ctx.request_id}

            first_nl2sql_sql = next(
                (c.sql for c in ctx.nl2sql_calls if c.status == "success" and (c.sql or "").strip()),
                None,
            )
            finished_meta = build_analysis_finished_meta(
                request_id=ctx.request_id,
                plan_id=ctx.plan_id,
                analysis_type=req.analysis_type,
                data_mode="nl2sql",
                used_rag=ctx.used_rag,
                used_plan_rag=ctx.used_plan_rag,
                used_business_rag=ctx.used_business_rag,
                rag_citations=ctx.rag_citations,
                start_ts=t_pipeline,
                synthesis_strategy_effective=(
                    syn_outcome.strategy_effective if syn_outcome else effective_strategy
                ),
                synthesis_ms=synthesis_ms,
                used_nl2sql=True,
                nl2sql_sql=first_nl2sql_sql,
            )
            yield analysis_finished_sse_event(finished_meta)

            ANALYSIS_REQUEST_COUNT.labels(
                analysis_type=req.analysis_type, data_mode="nl2sql", status="success"
            ).inc()
        except Exception:
            ANALYSIS_REQUEST_COUNT.labels(
                analysis_type=req.analysis_type, data_mode="nl2sql", status="failed"
            ).inc()
            raise

    async def _nl2sql_stream_background_finalize(
        self,
        req: AnalysisNL2SQLRequest,
        ctx: _Nl2SqlPipelineThroughRagContext,
        *,
        summary: str,
        synthesis_version: str,
        report_version: str,
        synthesis_started: float,
        on_complete: Callable[[AnalysisV2Result], Awaitable[None]] | None,
        syn_outcome: _SynthesisRunOutcome | None = None,
    ) -> None:
        """流式 summary 结束后：组装与同步路径一致的 `AnalysisV2Result`，写日志并投递扩展钩子。"""
        try:
            result = self._finalize_nl2sql_sequential_v2(
                req,
                ctx,
                summary=summary,
                synthesis_version=synthesis_version,
                report_version=report_version,
                synthesis_started=synthesis_started,
                syn_outcome=syn_outcome,
            )
            payload = result.model_dump(mode="json")
            dumped = json.dumps(payload, ensure_ascii=False)
            if len(dumped) > 16000:
                dumped = dumped[:16000] + "...(truncated)"
            logger.info(
                "analysis_nl2sql_stream_full_json request_id=%s json=%s",
                ctx.request_id,
                dumped,
            )
            await dispatch_analysis_nl2sql_stream_structured(payload)
            if on_complete is not None:
                await on_complete(result)
        except Exception:  # noqa: BLE001
            logger.exception(
                "analysis_nl2sql_stream_background_finalize failed request_id=%s",
                ctx.request_id,
            )

    @staticmethod
    def _json_fallback(value: Any) -> str:
        """将 datetime/Decimal/其它非 JSON 类型兜底序列化为字符串。"""
        if hasattr(value, "isoformat"):
            try:
                return str(value.isoformat())
            except Exception:  # noqa: BLE001
                return str(value)
        return str(value)

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
        if analysis_type == "four_tube_health_interpretation":
            return [
                {
                    "priority": 1,
                    "category": "inspection",
                    "owner": "运行值长",
                    "eta": "本周",
                    "trigger": "high_risk_zone",
                    "rationale": "解读结论中高风险集中区域应提高巡检频次并控制壁温波动。",
                    "action": "按简报所列高风险受热面/管段安排加密壁温监视与飞灰走廊巡查。",
                },
                {
                    "priority": 2,
                    "category": "maintenance",
                    "owner": "检修班组",
                    "eta": "窗口内",
                    "trigger": "thinning_or_defect",
                    "rationale": "减薄或缺陷与寿命/风险解读一致时，检修侧应测厚复核或分级处置。",
                    "action": "对建议书中的必换/必检项执行测厚复核与备件准备，轻微项纳入下次小修复查。",
                },
            ]
        if analysis_type == "leakage_burst_analysis":
            return [
                {
                    "priority": 1,
                    "category": "inspection",
                    "owner": "检修班组",
                    "eta": "immediate",
                    "trigger": "leak_or_burst_event",
                    "rationale": "泄爆/泄漏后应优先扩大同区域与同类型管段排查并留存证据。",
                    "action": "对事件相邻排管开展宏观检查与测厚抽检，核对最小壁厚与缺口形态并拍照归档。",
                },
                {
                    "priority": 2,
                    "category": "operation_adjustment",
                    "owner": "运行专工",
                    "eta": "24h",
                    "trigger": "pre_event_overheat",
                    "rationale": "泄爆前若存在超温或热偏差，应复核运行边界以防同类复发。",
                    "action": "核查事发区域壁温轨迹、配风与负荷波动，必要时优化燃烧配风并加强该区域壁温监视。",
                },
            ]
        if analysis_type == "img_diag_defect_ident":
            return [
                {
                    "priority": 1,
                    "category": "inspection",
                    "owner": "检修班组",
                    "eta": "immediate",
                    "trigger": "defect_identified",
                    "rationale": "图像识别缺陷后需按风险等级开展扩检并留存证据。",
                    "action": "对缺陷管段及相邻管子开展宏观检查、测厚/硬度抽检，记录形貌与壁厚最小值并拍照归档。",
                },
                {
                    "priority": 2,
                    "category": "operation_monitoring",
                    "owner": "运行专工",
                    "eta": "24h",
                    "trigger": "moderate_or_high_risk_defect",
                    "rationale": "中高风险缺陷需加强运行监护并明确复测周期。",
                    "action": "按处置方案加强该区域壁温与工况监视，落实监护频次与报警阈值复核。",
                },
            ]
        if analysis_type == "img_diag_leakage_burst":
            return [
                {
                    "priority": 1,
                    "category": "inspection",
                    "owner": "检修班组",
                    "eta": "immediate",
                    "trigger": "leak_or_burst_event",
                    "rationale": "泄爆后应优先扩大同区域与同类型管段排查并留存爆口证据。",
                    "action": "对事件相邻排管开展宏观检查与测厚抽检，核对爆口形貌、最小壁厚并拍照归档。",
                },
                {
                    "priority": 2,
                    "category": "operation_adjustment",
                    "owner": "运行专工",
                    "eta": "24h",
                    "trigger": "pre_event_overheat_or_operation",
                    "rationale": "事故近3天若存在超温或运行偏差，应复核运行边界以防同类复发。",
                    "action": "核查事发区域壁温轨迹、吹灰与配风记录，必要时优化燃烧配风并加强监视。",
                },
                {
                    "priority": 3,
                    "category": "prevention",
                    "owner": "技术专工",
                    "eta": "7d",
                    "trigger": "root_cause_identified",
                    "rationale": "三层溯源完成后应落实同类爆管预防与同区域改造评估。",
                    "action": "依据根因类别制定防磨/防腐/运行优化措施，参考知识库同类案例编制预防清单。",
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
        v2_tables: list[dict[str, Any]] | None = None,
        v2_charts: list[dict[str, Any]] | None = None,
        v2_sections: list[dict[str, Any]] | None = None,
        synthesis_strategy_effective: str | None = None,
    ) -> dict:
        """由摘要与数据覆盖组装 `structured_report`（sections/tables/charts 等）。"""
        records = AnalysisGraphRunner._flatten_records(data_coverage)
        charts: list[dict[str, Any]] = []
        if chart_mode != "off" and synthesis_strategy_effective != "v2":
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
        sections: list[dict[str, Any]] = []
        if v2_sections:
            sections.extend(v2_sections)
        else:
            sections.append({"title": "结论摘要", "content": summary})
        sections.append(
            {
                "title": "执行说明",
                "content": (
                    "数据覆盖概览: "
                    f"{json.dumps(data_coverage, ensure_ascii=False, default=AnalysisGraphRunner._json_fallback)}"
                ),
            }
        )
        tables: list[dict[str, Any]] = list(v2_tables or [])
        tables.append(
            {
                "title": "建议清单",
                "columns": ["priority", "category", "owner", "eta", "trigger", "rationale", "action"],
                "rows": suggestions,
            }
        )
        merged_charts = list(v2_charts or [])
        if merged_charts:
            out_charts = merged_charts
        else:
            out_charts = charts if chart_mode != "minimal" else charts[:1]
        meta: dict[str, Any] = {
            "analysis_type": analysis_type,
            "report_style": report_style,
            "report_template": report_template,
        }
        if synthesis_strategy_effective:
            meta["synthesis_strategy_effective"] = synthesis_strategy_effective
        return {
            "meta": meta,
            "sections": sections,
            "tables": tables,
            "charts": out_charts,
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
        template_version: str | None = None,
    ) -> tuple[str, str]:
        """
        分层模板解析策略（生产可用）：
        1) 优先匹配 stage + analysis_type；
        2) 回退 stage；
        3) 最终回退 analysis；

        例：`analysis_type=overheat_guidance` 且 `stage=analysis_synthesis` 时，
        依次尝试：
        - `analysis_synthesis_overheat_guidance`
        - `analysis_synthesis`
        - `analysis`
        """
        candidate_scenes = [
            f"{stage}_{analysis_type}",
            stage,
            "analysis",
        ]
        ver = (template_version or "").strip() or None
        for scene in candidate_scenes:
            tpl = self._prompts.get_template(scene=scene, user_id=user_id, version=ver)
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
        warnings.extend(self._collect_nl2sql_time_intent_warnings(calls))
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

    @classmethod
    def _collect_nl2sql_time_intent_warnings(cls, calls: list[AnalysisNL2SQLCall]) -> list[str]:
        """从 NL2SQL 子调用 question_intent / error 汇总时间窗改写告警，供质量门与合成。"""
        seen: set[str] = set()
        out: list[str] = []
        for call in calls:
            intent = call.question_intent if isinstance(call.question_intent, dict) else {}
            for code in intent.get("time_rewrite_warnings") or []:
                if not isinstance(code, str) or code in seen:
                    continue
                seen.add(code)
                out.append(_TIME_REWRITE_WARNING_CN.get(code, code))
            err = (call.error or "").lower()
            if "unresolved time placeholders" in err:
                if _UNRESOLVED_TIME_PLACEHOLDER_WARNING not in seen:
                    seen.add(_UNRESOLVED_TIME_PLACEHOLDER_WARNING)
                    out.append(_UNRESOLVED_TIME_PLACEHOLDER_WARNING)
        return out

    @staticmethod
    def _required_fields_by_type(analysis_type: str) -> list[str]:
        if analysis_type == "overheat_guidance":
            return ["time", "temperature", "zone"]
        if analysis_type == "maintenance_strategy":
            return ["time", "thickness", "zone"]
        if analysis_type == "four_tube_health_interpretation":
            # 健康指数/风险等多为业务结果表字段，列名差异大；质量门仅强约束时间类锚点
            return ["time"]
        if analysis_type == "leakage_burst_analysis":
            return ["time", "zone"]
        if analysis_type == "img_diag_defect_ident":
            return ["time", "zone", "thickness", "temperature"]
        if analysis_type == "img_diag_leakage_burst":
            return ["time", "zone", "thickness", "temperature"]
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

    def _plan_rag_recall_rerank_queries(self, user_query: str, analysis_type: str) -> tuple[str, str | None]:
        """
        规划前 RAG 主检索句与可选重排句（见 `AnalysisConfig.plan_rag_query_mode`）。
        返回 (recall_query, rerank_query)；rerank_query 仅在 Hybrid 重排阶段使用。
        """
        q = (user_query or "").strip()
        mode = (self._analysis_cfg.plan_rag_query_mode or "two_stage").strip().lower()
        cn = (_PLAN_RAG_ANALYSIS_TYPE_CN.get(analysis_type) or "").strip()
        if mode == "legacy":
            return (f"{analysis_type} {q}".strip(), None)
        if mode == "user_only":
            return (q, None)
        if mode == "cn_label_prefix":
            if cn:
                return (f"{cn} {q}".strip(), None)
            return (f"{analysis_type} {q}".strip(), None)
        if mode != "two_stage":
            logger.warning("unknown plan_rag_query_mode=%s; fallback to two_stage", mode)
        rr = f"{cn} {q}".strip() if cn else f"{analysis_type} {q}".strip()
        return (q, rr)

    def _retrieve_plan_rag(
        self, query: str, analysis_type: str, enable_rag: bool
    ) -> tuple[list[str], list[dict[str, Any]], list[RetrievedChunk]]:
        """规划前 RAG：逐 nl2sql_* 命名空间检索，scene 固定为 nl2sql。"""
        if not enable_rag:
            return [], [], []
        recall_q, rerank_q = self._plan_rag_recall_rerank_queries(query, analysis_type)
        namespaces = ["nl2sql_schema", "nl2sql_biz_knowledge", "nl2sql_qa_examples"]
        results: list[str] = []
        sources: list[dict[str, Any]] = []
        chunks: list[RetrievedChunk] = []
        for ns in namespaces:
            try:
                parts, src, ns_chunks = self._retrieve_rag_with_sources(
                    query=recall_q,
                    namespace=ns,
                    top_k=3,
                    scene="nl2sql",
                    rerank_query=rerank_q,
                )
            except Exception:  # noqa: BLE001
                logger.exception("analysis plan rag retrieve failed namespace=%s", ns)
                continue
            if parts:
                results.extend(parts[:3])
                sources.extend(src[:3])
                chunks.extend(ns_chunks[:3])
        return results[:9], sources[:9], chunks

    @staticmethod
    def _build_business_rag_recall_query(user_query: str, analysis_type: str) -> str:
        """构造 business RAG 召回句；超温专项追加材质-超温领域词（方案 B）。"""
        q = (user_query or "").strip()
        base = f"{analysis_type} {q}".strip()
        if analysis_type == "overheat_guidance":
            return f"{base} {_OVERHEAT_BUSINESS_RAG_BOOST}".strip()
        if analysis_type == "img_diag_defect_ident":
            return f"{base} {_DEFECT_IDENT_BUSINESS_RAG_BOOST}".strip()
        if analysis_type == "img_diag_leakage_burst":
            return f"{base} {_LEAKAGE_BURST_IMG_DIAG_BUSINESS_RAG_BOOST}".strip()
        return base

    @staticmethod
    def _build_business_rag_rerank_query(user_query: str, analysis_type: str) -> str | None:
        """超温专项 business RAG 重排句；其它专项不重排。"""
        if analysis_type == "overheat_guidance":
            uq = (user_query or "").strip()
            return f"锅炉管壁超温 规格材质 受热面 {uq}".strip()
        if analysis_type == "img_diag_defect_ident":
            uq = (user_query or "").strip()
            return f"锅炉缺陷识别 处置方案 检修工序 {uq}".strip()
        if analysis_type == "img_diag_leakage_burst":
            uq = (user_query or "").strip()
            return f"锅炉泄爆溯源 爆管原因 事故案例 规程条文 {uq}".strip()
        return None

    def _retrieve_business_rag(
        self, query: str, analysis_type: str
    ) -> tuple[list[str], list[dict[str, Any]], list[RetrievedChunk]]:
        """结论前业务 RAG：全库检索但排除 nl2sql_* 命名空间，scene=analysis。"""
        try:
            return self._retrieve_rag_with_sources(
                query=self._build_business_rag_recall_query(query, analysis_type),
                rerank_query=self._build_business_rag_rerank_query(query, analysis_type),
                namespace=None,
                top_k=8,
                scene="analysis",
                exclude_namespaces=_ANALYSIS_RAG_CITATIONS_EXCLUDED_NAMESPACES,
            )
        except Exception:  # noqa: BLE001
            logger.exception("analysis business rag retrieve failed")
            return [], [], []

    def _build_data_plan(self, req: AnalysisNL2SQLRequest, *, plan_context: list[str]) -> list[_PlanTask]:
        """数据计划：先 YAML 模板 `analysis_plan_<type>`；为空时用代码内置默认任务，再拼 data_requirements_hint 与 plan_context 引导。"""
        templated = self._build_data_plan_from_template(req, plan_context=plan_context)
        if templated:
            return templated
        hints = req.data_requirements_hint or []
        if req.analysis_type == "overheat_guidance":
            # 与 prompts_bak_new.yaml · analysis_plan_overheat_guidance 语义对齐（模板缺失时的兜底）
            base = [
                _PlanTask(
                    "q1",
                    "超温事实明细",
                    self._compose_plan_task_question(req.query, "查询超温事件明细与时间分布"),
                    mandatory=True,
                ),
                _PlanTask(
                    "q2",
                    "运行参数关联",
                    self._compose_plan_task_question(req.query, "查询风门开度、蒸汽流量等运行参数"),
                    mandatory=True,
                    dependency_ids=["q1"],
                ),
                _PlanTask(
                    "q3",
                    "燃烧器状态",
                    self._compose_plan_task_question(req.query, "查询燃烧器状态及切换记录"),
                    mandatory=False,
                    dependency_ids=["q1"],
                ),
            ]
        elif req.analysis_type == "maintenance_strategy":
            base = [
                _PlanTask(
                    "q1",
                    "壁厚测量数据",
                    self._compose_plan_task_question(req.query, "查询壁厚测量结果与趋势"),
                    mandatory=True,
                ),
                _PlanTask(
                    "q2",
                    "换管历史记录",
                    self._compose_plan_task_question(req.query, "查询换管历史、材质与时间信息"),
                    mandatory=True,
                ),
                _PlanTask(
                    "q3",
                    "超温频次统计",
                    self._compose_plan_task_question(req.query, "按区域统计超温频次"),
                    mandatory=False,
                    dependency_ids=["q1"],
                ),
            ]
        elif req.analysis_type == "four_tube_health_interpretation":
            # 与 prompts_bak_new.yaml · analysis_plan_four_tube_health_interpretation 语义对齐（模板缺失时的兜底）
            base = [
                _PlanTask(
                    "q1",
                    "四管健康评估结果",
                    self._compose_plan_task_question(
                        req.query,
                        "查询四管或受热面健康评估结果：健康指数/评分、风险等级、剩余寿命或寿命区间、评估时间、管段或受热面定位字段（仅使用 catalog 中真实表与列）",
                    ),
                    mandatory=True,
                ),
                _PlanTask(
                    "q2",
                    "测厚与减薄速率",
                    self._compose_plan_task_question(
                        req.query, "查询 overhaul_thickness_rate、overhaul_record 与 overhaul_record_tubes 中的壁厚、减薄速率与缺陷摘要"
                    ),
                    mandatory=True,
                ),
                _PlanTask(
                    "q3",
                    "超温与运行劣化佐证",
                    self._compose_plan_task_question(
                        req.query, "查询 monitor_hotarea_temp 及可关联的 base_temp_point/base_temp_device 的超温频次与极值"
                    ),
                    mandatory=False,
                ),
                _PlanTask(
                    "q4",
                    "泄爆泄漏履历",
                    self._compose_plan_task_question(req.query, "查询 overhaul_leakage 同类位置历史泄漏或爆管记录摘要"),
                    mandatory=False,
                ),
            ]
        elif req.analysis_type == "leakage_burst_analysis":
            # 与 prompts_bak_new.yaml · analysis_plan_leakage_burst_analysis 语义对齐（模板缺失时的兜底）
            base = [
                _PlanTask(
                    "q1",
                    "泄爆/泄漏履历",
                    self._compose_plan_task_question(
                        req.query,
                        "查询 overhaul_leakage 泄爆或泄漏记录：发生时间、位置或管段、原因或结论类字段（以 catalog 列为准）",
                    ),
                    mandatory=True,
                ),
                _PlanTask(
                    "q2",
                    "同区域测厚与缺陷",
                    self._compose_plan_task_question(
                        req.query, "查询 overhaul_record 与 overhaul_record_tubes 中相关位置测厚、缺陷与换管处置摘要"
                    ),
                    mandatory=True,
                ),
                _PlanTask(
                    "q3",
                    "泄爆前超温佐证",
                    self._compose_plan_task_question(
                        req.query, "查询 monitor_hotarea_temp 及可关联测点配置的超温频次与极值"
                    ),
                    mandatory=True,
                ),
                _PlanTask(
                    "q4",
                    "减薄速率",
                    self._compose_plan_task_question(req.query, "查询 overhaul_thickness_rate 减薄速率与寿命相关指标"),
                    mandatory=False,
                ),
            ]
        elif req.analysis_type == "img_diag_defect_ident":
            ph = _IMG_DIAG_SQL_PLACEHOLDER_CN
            base = [
                _PlanTask(
                    "q1",
                    "管段基础参数",
                    self._compose_plan_task_question(
                        req.query,
                        f"查询用户问题指定区域的管段基础参数：规格材质、壁厚限值、胀粗率限值、壁温限值、累计运行时长（monitor_boiler_start_stop 子查询用 @t_start/@t_end）。{ph}",
                    ),
                    mandatory=True,
                ),
                *self._img_diag_q2_plan_tasks(query=req.query, ph=ph, leakage=False),
                _PlanTask(
                    "q3",
                    "壁温超温数据",
                    self._compose_plan_task_question(
                        req.query,
                        f"查询对应管段累计超温时长、超温峰值、壁温偏差（monitor_hotarea_temp）。{ph}",
                    ),
                    mandatory=True,
                ),
                _PlanTask(
                    "q4",
                    "吹灰运行数据",
                    self._compose_plan_task_question(
                        req.query,
                        f"查询对应区域吹灰器吹灰频次、吹扫压力、累计吹扫时长（base_soot_blower/monitor_soot_blower_run_record）。{ph}",
                    ),
                    mandatory=False,
                ),
                _PlanTask(
                    "q5",
                    "烟气煤质数据",
                    self._compose_plan_task_question(
                        req.query,
                        f"查询对应区域烟温、烟速、飞灰浓度等烟气煤质相关测点数据摘要（sis_pi_data）。{ph}",
                    ),
                    mandatory=False,
                ),
            ]
        elif req.analysis_type == "img_diag_leakage_burst":
            ph = _IMG_DIAG_SQL_PLACEHOLDER_LEAKAGE_CN
            base = [
                _PlanTask(
                    "q1",
                    "管段基础参数",
                    self._compose_plan_task_question(
                        req.query,
                        f"以用户问题解析的事故时刻为锚点向前3天，查询该区域管段基础参数：规格材质、壁厚限值、胀粗率限值、壁温限值、累计运行时长。{ph}",
                    ),
                    mandatory=True,
                ),
                *self._img_diag_q2_plan_tasks(query=req.query, ph=ph, leakage=True),
                _PlanTask(
                    "q3",
                    "壁温超温数据",
                    self._compose_plan_task_question(
                        req.query,
                        f"事故锚点向前3天内查询对应管段累计超温时长、超温峰值、壁温偏差。{ph}",
                    ),
                    mandatory=True,
                ),
                _PlanTask(
                    "q4",
                    "吹灰运行数据",
                    self._compose_plan_task_question(
                        req.query,
                        f"事故锚点向前3天内查询对应区域吹灰器吹灰频次、吹扫压力、累计吹扫时长。{ph}",
                    ),
                    mandatory=False,
                ),
                _PlanTask(
                    "q5",
                    "烟气煤质数据",
                    self._compose_plan_task_question(
                        req.query,
                        f"事故锚点向前3天内查询对应区域烟温、烟速、飞灰浓度等烟气煤质相关测点数据摘要。{ph}",
                    ),
                    mandatory=False,
                ),
            ]
        else:
            base = [
                _PlanTask(
                    "q1",
                    "关键事实数据",
                    self._compose_plan_task_question(req.query, "查询核心事实数据"),
                    mandatory=True,
                ),
                _PlanTask(
                    "q2",
                    "关联维度数据",
                    self._compose_plan_task_question(req.query, "查询关联维度和补充信息"),
                    mandatory=False,
                    dependency_ids=["q1"],
                ),
            ]

        if hints:
            for i, h in enumerate(hints, start=1):
                qid = f"h{i}"
                base.append(
                    _PlanTask(
                        item_id=qid,
                        purpose=f"提示补充:{h}",
                        question=self._compose_plan_task_question(req.query, f"补充查询与「{h}」直接相关的数据"),
                        mandatory=False,
                    )
                )
        if plan_context:
            guide = self._plan_context_guide_text(plan_context)
            if guide:
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
        plan_ver = self._resolve_plan_template_version(req.analysis_type)
        tpl = self._prompts.get_template(scene=scene, user_id=req.user_id, version=plan_ver)
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
        guide = self._plan_context_guide_text(plan_context) if plan_context else ""
        for idx, item in enumerate(raw_items, start=1):
            if not isinstance(item, dict):
                continue
            specific = str(item.get("question") or "").strip()
            q = self._compose_plan_task_question(req.query, specific)
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
                    question=self._compose_plan_task_question(req.query, f"补充查询与「{h}」直接相关的数据"),
                    mandatory=False,
                )
            )
        return tasks

    async def _run_single_nl2sql_plan_task(
        self,
        req: AnalysisNL2SQLRequest,
        task: _PlanTask,
        *,
        analysis_request_id: str | None = None,
        plan_template_version: str | None = None,
    ) -> tuple[AnalysisNL2SQLCall, dict[str, list[dict]]]:
        """单次 plan 任务：LLM 生成 SQL + 执行（封装于 NL2SQLService.query），最多 2 次尝试。"""
        max_attempts = 2
        last_error: str | None = None
        final_sql = ""
        last_question_intent: dict[str, Any] | None = None
        include_intent = trace_include_question_intent()
        for attempt in range(1, max_attempts + 1):
            try:
                resp = await self._nl2sql.query(
                    NL2SQLQueryRequest(
                        user_id=req.user_id,
                        session_id=req.session_id,
                        question=task.question,
                        analysis_type=req.analysis_type,
                        analysis_request_id=analysis_request_id,
                        plan_item_id=task.item_id,
                        plan_template_version=plan_template_version,
                        time_intent_text=(req.query or "").strip(),
                    ),
                    record_conversation=False,
                    include_parsed_intent=include_intent,
                )
                final_sql = resp.sql
                rows = resp.rows[: req.options.max_rows_per_query]
                call = AnalysisNL2SQLCall(
                    item_id=task.item_id,
                    purpose=task.purpose,
                    question=task.question,
                    sql=final_sql,
                    row_count=len(rows),
                    status="success",
                    attempts=attempt,
                    dependency_ids=task.dependency_ids,
                    question_intent=resp.parsed_intent,
                )
                ANALYSIS_NL2SQL_CALL_COUNT.labels(analysis_type=req.analysis_type, status="success").inc()
                return call, {task.item_id: rows}
            except NL2SQLExecutionError as exc:
                last_error = exc.brief_message
                if exc.parsed_intent is not None:
                    last_question_intent = exc.parsed_intent
                logger.error(
                    "analysis nl2sql task failed item=%s attempt=%s analysis_request_id=%s error_code=%s detail=%s",
                    task.item_id,
                    attempt,
                    analysis_request_id or "-",
                    exc.error_code,
                    exc.log_detail(),
                )
            except Exception as exc:  # noqa: BLE001
                last_error = str(exc)
                logger.exception(
                    "analysis nl2sql task failed item=%s attempt=%s analysis_request_id=%s",
                    task.item_id,
                    attempt,
                    analysis_request_id or "-",
                )
        err_text = last_error or "nl2sql_query_failed"
        call = AnalysisNL2SQLCall(
            item_id=task.item_id,
            purpose=task.purpose,
            question=task.question,
            sql=final_sql,
            row_count=0,
            status="failed",
            attempts=max_attempts,
            dependency_ids=task.dependency_ids,
            error=err_text,
            question_intent=last_question_intent if include_intent else None,
        )
        ANALYSIS_NL2SQL_CALL_COUNT.labels(analysis_type=req.analysis_type, status="failed").inc()
        return call, {}

    async def _execute_data_plan_sequential(
        self,
        *,
        req: AnalysisNL2SQLRequest,
        tasks: list[_PlanTask],
        analysis_request_id: str | None = None,
        plan_template_version: str | None = None,
    ) -> tuple[list[AnalysisNL2SQLCall], dict[str, list[dict]], dict[str, str]]:
        """与并行版语义一致，仅按 plan_tasks 顺序串行调度（并行关闭或单任务时使用）。"""
        ptv = plan_template_version or self._resolve_plan_template_version_label(req)
        calls: list[AnalysisNL2SQLCall] = []
        gathered_data: dict[str, list[dict]] = {}
        task_status: dict[str, str] = {}
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
            if self._should_skip_optional_overheat_q5(task, req.analysis_type, gathered_data):
                calls.append(
                    self._skipped_plan_call(task, error="no_overheat_events_q1_empty")
                )
                task_status[task.item_id] = "optional_skipped"
                ANALYSIS_NL2SQL_CALL_COUNT.labels(
                    analysis_type=req.analysis_type, status="skipped"
                ).inc()
                continue
            call, gd = await self._run_single_nl2sql_plan_task(
                req,
                task,
                analysis_request_id=analysis_request_id,
                plan_template_version=ptv,
            )
            calls.append(call)
            gathered_data.update(gd)
            task_status[task.item_id] = self._task_status_from_call(task, call)
        return calls, gathered_data, task_status

    async def _execute_data_plan(
        self,
        *,
        req: AnalysisNL2SQLRequest,
        tasks: list[_PlanTask],
        analysis_request_id: str | None = None,
        plan_template_version: str | None = None,
    ) -> tuple[list[AnalysisNL2SQLCall], dict[str, list[dict]], dict[str, str], int]:
        """执行 NL2SQL：依赖未满足则 skipped；每项最多 2 次尝试。默认同层无依赖任务并行（含生成 SQL 与查库）。"""
        ptv = plan_template_version or self._resolve_plan_template_version_label(req)
        t_data = perf_counter()
        order_idx = {t.item_id: i for i, t in enumerate(tasks)}
        task_by_id = {t.item_id: t for t in tasks}

        if not self._analysis_cfg.nl2sql_acquire_parallel_enabled or len(tasks) <= 1:
            calls, gathered_data, task_status = await self._execute_data_plan_sequential(
                req=req,
                tasks=tasks,
                analysis_request_id=analysis_request_id,
                plan_template_version=ptv,
            )
            duration_s = perf_counter() - t_data
            ANALYSIS_NODE_LATENCY.labels(node="acquire_data", analysis_type=req.analysis_type).observe(duration_s)
            return calls, gathered_data, task_status, int(duration_s * 1000)

        calls = []
        gathered_data = {}
        task_status = {}
        unfinished = {t.item_id for t in tasks}
        max_par = max(1, self._analysis_cfg.nl2sql_acquire_max_parallel)
        sem = asyncio.Semaphore(max_par)

        async def bounded(task: _PlanTask) -> tuple[AnalysisNL2SQLCall, dict[str, list[dict]]]:
            if self._should_skip_optional_overheat_q5(task, req.analysis_type, gathered_data):
                return self._skipped_plan_call(task, error="no_overheat_events_q1_empty"), {}
            async with sem:
                return await self._run_single_nl2sql_plan_task(
                    req,
                    task,
                    analysis_request_id=analysis_request_id,
                    plan_template_version=ptv,
                )

        while unfinished:
            runnable: list[_PlanTask] = []
            for t in tasks:
                if t.item_id not in unfinished:
                    continue
                if not all(d not in unfinished for d in t.dependency_ids):
                    continue
                if not all(task_status.get(d) == "success" for d in t.dependency_ids):
                    continue
                runnable.append(t)
            if runnable:
                logger.info(
                    "analysis acquire_data parallel wave item_ids=%s unfinished_before=%d analysis_request_id=%s",
                    [x.item_id for x in runnable],
                    len(unfinished),
                    analysis_request_id or "-",
                )
                t_wave = perf_counter()
                results = await asyncio.gather(*[bounded(t) for t in runnable])
                wave_ms = int((perf_counter() - t_wave) * 1000)
                outcomes = {call.item_id: call.status for call, _ in results}
                logger.info(
                    "analysis_nl2sql_acquire_wave_end request_id=%s item_ids=%s wave_ms=%s outcomes=%s",
                    analysis_request_id or "-",
                    json.dumps([x.item_id for x in runnable], ensure_ascii=False),
                    wave_ms,
                    json.dumps(outcomes, ensure_ascii=False),
                )
                for call, gd_part in results:
                    calls.append(call)
                    gathered_data.update(gd_part)
                    tid = call.item_id
                    unfinished.discard(tid)
                    pt = task_by_id[tid]
                    task_status[tid] = self._task_status_from_call(pt, call)
                    if call.status == "skipped":
                        ANALYSIS_NL2SQL_CALL_COUNT.labels(
                            analysis_type=req.analysis_type, status="skipped"
                        ).inc()
                continue
            for t in tasks:
                if t.item_id not in unfinished:
                    continue
                calls.append(
                    AnalysisNL2SQLCall(
                        item_id=t.item_id,
                        purpose=t.purpose,
                        question=t.question,
                        sql="",
                        row_count=0,
                        status="skipped",
                        attempts=0,
                        dependency_ids=t.dependency_ids,
                        error="dependency_not_satisfied",
                    )
                )
                task_status[t.item_id] = "mandatory_failed" if t.mandatory else "optional_skipped"
                ANALYSIS_NL2SQL_CALL_COUNT.labels(analysis_type=req.analysis_type, status="skipped").inc()
                unfinished.discard(t.item_id)

        calls.sort(key=lambda c: order_idx.get(c.item_id, 10**9))
        duration_s = perf_counter() - t_data
        ANALYSIS_NODE_LATENCY.labels(node="acquire_data", analysis_type=req.analysis_type).observe(duration_s)
        return calls, gathered_data, task_status, int(duration_s * 1000)
