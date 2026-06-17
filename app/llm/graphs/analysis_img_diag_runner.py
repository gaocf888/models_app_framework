from __future__ import annotations

"""
看图诊断编排（缺陷识别 img_diag_defect_ident / 泄爆分析 img_diag_leakage_burst）：
  视觉臂 ‖ NL2SQL 臂（规划→取数→质量门）并行
  → 业务 RAG 臂（串行，query 含用户问题 + 视觉 JSON 摘要）
  → synthesis → finalize

入参：`img_diag_subtype` + `query` + `image_urls`（泄爆可选图）；时间/范围由 NL2SQL 基座从 `query` 解析。
"""

import asyncio
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from time import perf_counter
from typing import Any, AsyncIterator, Awaitable, Callable, cast
from uuid import uuid4

from app.core.logging import get_logger
from app.core.metrics import ANALYSIS_REQUEST_COUNT
from app.llm.graphs.analysis_finished_meta import (
    analysis_finished_sse_event,
    build_analysis_finished_meta,
)
from app.llm.graphs.analysis_graph_runner import AnalysisGraphRunner
from app.models.analysis import (
    AnalysisEvidence,
    AnalysisImgDiagRequest,
    AnalysisNL2SQLCall,
    AnalysisNL2SQLRequest,
    AnalysisTrace,
    AnalysisType,
    AnalysisV2Result,
    DataMode,
    ImgDiagSubtype,
)
from app.models.analysis_nl2sql_llm import extract_json_object_from_llm_text
from app.services.analysis_stream_hooks import dispatch_analysis_nl2sql_stream_structured

logger = get_logger(__name__)

IMG_DIAG_DEFECT_IDENT_TYPE: AnalysisType = "img_diag_defect_ident"
IMG_DIAG_LEAKAGE_BURST_TYPE: AnalysisType = "img_diag_leakage_burst"


@dataclass(frozen=True)
class _ImgDiagSubtypeProfile:
    subtype: ImgDiagSubtype
    analysis_type: AnalysisType
    data_mode: DataMode
    vision_scene: str
    rag_scene_label: str
    prefetch_rag_intent: str
    augmented_rag_intent: str
    synthesis_default: str
    report_default: str
    stream_fallback_summary: str
    orchestrator_stream_id: str
    images_required: bool


_IMG_DIAG_PROFILES: dict[ImgDiagSubtype, _ImgDiagSubtypeProfile] = {
    "defect_ident": _ImgDiagSubtypeProfile(
        subtype="defect_ident",
        analysis_type=IMG_DIAG_DEFECT_IDENT_TYPE,
        data_mode="img_diag_defect_ident",
        vision_scene="analysis_img_diag_vision_defect_ident",
        rag_scene_label="缺陷识别",
        prefetch_rag_intent="规程通识 缺陷处置 运行监护 检修工艺",
        augmented_rag_intent="同类型缺陷历史处置案例 打磨补焊 换管 防磨瓦 运行监护 复测周期",
        synthesis_default=(
            "你是电厂承压管系缺陷识别分析师，需融合图像证据、数据库摘要与知识库片段"
            "输出分风险等级的现场处置方案。"
        ),
        report_default="输出章节含：缺陷判定、证据链、处置方案、历史案例、解析范围说明、免责声明。",
        stream_fallback_summary="缺陷识别分析生成失败，已返回基础报告，请稍后重试。",
        orchestrator_stream_id="img_diag_defect_ident_stream",
        images_required=True,
    ),
    "leakage_burst": _ImgDiagSubtypeProfile(
        subtype="leakage_burst",
        analysis_type=IMG_DIAG_LEAKAGE_BURST_TYPE,
        data_mode="img_diag_leakage_burst",
        vision_scene="analysis_img_diag_vision_leakage_burst",
        rag_scene_label="泄爆分析",
        prefetch_rag_intent="规程通识 爆管预防 运行监护 检修工艺 标准条文",
        augmented_rag_intent=(
            "同位置同类型历史事故案例 同类型机组典型故障处理经验 标准规程条文 "
            "防控技术资料 同类爆管预防措施 同区域改造案例"
        ),
        synthesis_default=(
            "你是电厂锅炉受热面泄爆溯源高级分析师，需融合图像证据（若有）、"
            "事故近3天库表摘要与知识库片段，按五大类方向×三层逻辑输出泄爆原因分析报告。"
        ),
        report_default=(
            "输出章节含：结论摘要、三层溯源（直接/中期/根因）、五大类验证表、"
            "证据链、同类案例与规程、延伸问题、解析范围说明、免责声明。"
        ),
        stream_fallback_summary="泄爆分析生成失败，已返回基础报告，请稍后重试。",
        orchestrator_stream_id="img_diag_leakage_burst_stream",
        images_required=False,
    ),
}


@dataclass
class _ImgDiagPack:
    """并行臂完成后、合成前后的中间态（同步 / 流式 synthesis 共用）。"""

    req: AnalysisImgDiagRequest
    degrade: list[str]
    parallel_trace: dict[str, Any]
    vision_data: dict[str, Any]
    vision_ms: int
    vision_status: str
    biz_snippets: list[str]
    biz_sources: list[dict[str, Any]]
    rag_ms: int
    rag_status: str
    rag_query: str
    nl_state: dict[str, Any]
    nl_status: str
    calls: list[AnalysisNL2SQLCall]
    gathered_data: dict[str, list[dict]]
    planned_calls: int
    plan_rag_sources: list[dict[str, Any]]
    quality_report: dict[str, Any]
    merged_rag_sources: list[dict[str, Any]]
    rag_citations: list[dict[str, Any]]
    used_rag: bool
    used_plan_rag: bool
    used_business_rag: bool
    planning_ctx: str | None
    synthesis_prompt: str
    synthesis_version: str
    report_version: str
    merged_blob: dict[str, Any]
    parsed_intent_snapshot: dict[str, Any]
    parsed_time_intent: dict[str, Any]
    parsed_scope_intent: dict[str, Any]
    profile: _ImgDiagSubtypeProfile
    request_id: str
    plan_id: str


class AnalysisImgDiagGraphRunner(AnalysisGraphRunner):
    """看图诊断（缺陷识别 / 泄爆分析）：并行 + 串行 RAG 编排。"""

    @staticmethod
    def _profile(req: AnalysisImgDiagRequest) -> _ImgDiagSubtypeProfile:
        return _IMG_DIAG_PROFILES[req.img_diag_subtype]

    @classmethod
    def business_rag_query(
        cls,
        req: AnalysisImgDiagRequest,
        vision_data: dict[str, Any],
        *,
        parsed_scope: dict[str, Any] | None = None,
        profile: _ImgDiagSubtypeProfile | None = None,
    ) -> str:
        """业务 RAG 检索语句：用户问题 + 视觉结论 + 可选 NL2SQL 解析范围。"""
        prof = profile or cls._profile(req)
        scope_parts: list[str] = []
        if parsed_scope:
            for key in ("boiler", "device_name", "piperow_name", "row_no", "tube_no"):
                val = parsed_scope.get(key)
                if val is not None and str(val).strip():
                    scope_parts.append(f"{key}={val}")
        scope_line = " ".join(scope_parts)
        lines = [req.query.strip(), f"场景:{prof.rag_scene_label}"]

        if prof.subtype == "leakage_burst":
            burst_type = vision_data.get("burst_type") or vision_data.get("defect_type") or ""
            defect_signals = vision_data.get("defect_signals") or vision_data.get("burst_signals") or []
            severity = vision_data.get("severity") or vision_data.get("risk_level") or ""
            affected = vision_data.get("affected_surface") or ""
            failure_hints = vision_data.get("failure_mode_hints") or []
            signals_txt = (
                ", ".join(str(x) for x in defect_signals[:12])
                if isinstance(defect_signals, list)
                else str(defect_signals)
            )
            hints_txt = (
                ", ".join(str(x) for x in failure_hints[:8])
                if isinstance(failure_hints, list)
                else str(failure_hints)
            )
            lines.extend([
                f"爆口/泄漏形貌:{burst_type}",
                f"严重度:{severity}",
                f"受热面:{affected}",
                f"可见爆口线索:{signals_txt}",
            ])
            if hints_txt:
                lines.append(f"形貌机理线索:{hints_txt}")
        else:
            defect_type = vision_data.get("defect_type") or ""
            defect_signals = vision_data.get("defect_signals") or []
            risk_level = vision_data.get("risk_level") or vision_data.get("severity") or ""
            affected = vision_data.get("affected_surface") or ""
            failure_hints = vision_data.get("failure_mode_hints") or []
            signals_txt = (
                ", ".join(str(x) for x in defect_signals[:12])
                if isinstance(defect_signals, list)
                else str(defect_signals)
            )
            hints_txt = (
                ", ".join(str(x) for x in failure_hints[:8])
                if isinstance(failure_hints, list)
                else str(failure_hints)
            )
            lines.extend([
                f"缺陷类型:{defect_type}",
                f"风险等级:{risk_level}",
                f"受热面:{affected}",
                f"可见缺陷信号:{signals_txt}",
            ])
            if hints_txt:
                lines.append(f"形貌线索:{hints_txt}")

        if scope_line:
            lines.append(f"已解析范围:{scope_line}")
        lines.append(f"检索意图:{prof.augmented_rag_intent}")
        return "\n".join(lines)

    @staticmethod
    def _extract_parsed_intent_snapshot(calls: list[AnalysisNL2SQLCall]) -> dict[str, Any]:
        for call in calls:
            intent = call.question_intent
            if isinstance(intent, dict) and intent:
                return intent
        return {}

    @staticmethod
    def _scope_from_intent(intent: dict[str, Any]) -> dict[str, Any]:
        scope = intent.get("scope")
        if isinstance(scope, dict):
            return scope
        return {}

    @staticmethod
    def split_parsed_intent_snapshot(
        intent: dict[str, Any],
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        """从 NL2SQL parsed_intent 拆出时间窗与范围，供 trace / 审计。"""
        if not intent:
            return {}, {}
        time_part = {
            "time_window": intent.get("time_window"),
            "time_window_tag": intent.get("time_window_tag"),
            "time_anchor": intent.get("time_anchor"),
            "time_anchor_tag": intent.get("time_anchor_tag"),
            "statistical_time_range": intent.get("statistical_time_range"),
            "scope_question": intent.get("scope_question"),
        }
        scope_raw = intent.get("scope")
        scope_part = dict(scope_raw) if isinstance(scope_raw, dict) else {}
        return time_part, scope_part

    @staticmethod
    def _merge_rag_text_lists(primary: list[str], secondary: list[str], *, limit: int = 24) -> list[str]:
        seen: set[str] = set()
        out: list[str] = []
        for item in list(primary) + list(secondary):
            text = (item or "").strip()
            if not text:
                continue
            key = text[:240]
            if key in seen:
                continue
            seen.add(key)
            out.append(text)
            if len(out) >= limit:
                break
        return out

    @staticmethod
    def _merge_rag_source_lists(
        primary: list[dict[str, Any]],
        secondary: list[dict[str, Any]],
        *,
        limit: int = 64,
    ) -> list[dict[str, Any]]:
        seen: set[str] = set()
        out: list[dict[str, Any]] = []
        for row in list(primary) + list(secondary):
            if not isinstance(row, dict):
                continue
            key = str(row.get("chunk_id") or row.get("doc_name") or json.dumps(row, ensure_ascii=False))[:240]
            if key in seen:
                continue
            seen.add(key)
            out.append(row)
            if len(out) >= limit:
                break
        return out

    @classmethod
    def prefetch_business_rag_query(
        cls, req: AnalysisImgDiagRequest, profile: _ImgDiagSubtypeProfile | None = None
    ) -> str:
        """hybrid/parallel 预检 RAG：仅用户问题，不含视觉结论。"""
        prof = profile or cls._profile(req)
        return (
            f"{req.query.strip()}\n"
            f"场景:{prof.rag_scene_label}\n"
            f"检索意图:{prof.prefetch_rag_intent}"
        )

    async def _lane_business_rag_prefetch(
        self,
        req: AnalysisImgDiagRequest,
        profile: _ImgDiagSubtypeProfile,
    ) -> tuple[list[str], list[dict[str, Any]], list[Any], int, str, str]:
        if not req.options.enable_rag:
            return [], [], [], 0, "skipped", ""
        rag_query = self.prefetch_business_rag_query(req, profile)
        t0 = perf_counter()
        try:
            snippets, sources, chunks = await asyncio.to_thread(
                lambda: self._retrieve_business_rag(rag_query, profile.analysis_type)
            )
            ms = int((perf_counter() - t0) * 1000)
            return list(snippets), list(sources), list(chunks), ms, "success", rag_query
        except Exception as exc:  # noqa: BLE001
            logger.warning("img_diag %s prefetch rag failed: %s", profile.subtype, exc)
            return [], [], [], int((perf_counter() - t0) * 1000), "failed", rag_query

    async def _lane_vision(
        self, req: AnalysisImgDiagRequest, profile: _ImgDiagSubtypeProfile
    ) -> tuple[dict[str, Any], int]:
        urls = [u for u in (req.image_urls or []) if isinstance(u, str) and u.strip()]
        if not urls:
            return (
                {
                    "vision_skipped": True,
                    "reason": "no_image_provided",
                    "notes": "未提供图片，泄爆分析将仅依据用户问题、库表与知识库推理。",
                },
                0,
            )
        t0 = perf_counter()
        tpl = self._prompts.get_template(
            scene=profile.vision_scene,
            user_id=req.user_id,
            version=None,
        )
        default_instructions = (
            "你是承压部件缺陷图像分析助手，仅描述可见证据；输出必须为单个 JSON 对象。"
            if profile.subtype == "defect_ident"
            else "你是锅炉受热面泄爆/爆口图像分析助手，仅描述可见证据；输出必须为单个 JSON 对象。"
        )
        instructions = (
            tpl.content.strip()
            if tpl and tpl.content.strip()
            else default_instructions
        )
        header = f"用户问题: {req.query}\n\n{instructions}"
        content: list[dict[str, Any]] = [{"type": "text", "text": header}]
        for url in urls:
            content.append({"type": "image_url", "image_url": {"url": url}})
        messages = [{"role": "user", "content": content}]
        vision_model = self._analysis_cfg.img_diag_vision_model
        timeout = float(self._analysis_cfg.img_diag_vision_timeout_seconds)
        raw = await self._llm.chat(
            model=vision_model,  # type: ignore[arg-type]
            messages=messages,
            timeout=timeout,
        )
        ms = int((perf_counter() - t0) * 1000)
        parsed = extract_json_object_from_llm_text(raw)
        if parsed is None:
            try:
                parsed = json.loads(raw.strip())
            except Exception:  # noqa: BLE001
                parsed = {"raw_text": (raw or "")[:8000], "parse_error": "vision_output_not_json"}
        return parsed, ms

    async def _lane_nl2sql_until_gate(
        self,
        nl_req: AnalysisNL2SQLRequest,
    ) -> dict[str, Any]:
        state: dict[str, Any] = {"nl2sql_request": nl_req.model_dump(mode="json")}
        state.update(await self._lg_nl2sql_normalize_request(state))
        state.update(await self._lg_nl2sql_plan_context_rag(state))
        state.update(await self._lg_nl2sql_intent_llm(state))
        state.update(await self._lg_nl2sql_plan_llm_merge(state))
        state.update(await self._lg_nl2sql_acquire_data(state))
        state.update(await self._lg_nl2sql_data_quality_gate(state))
        return state

    async def _lane_business_rag(
        self,
        req: AnalysisImgDiagRequest,
        vision_data: dict[str, Any],
        profile: _ImgDiagSubtypeProfile,
        *,
        parsed_scope: dict[str, Any] | None = None,
    ) -> tuple[list[str], list[dict[str, Any]], list[Any], int, str, str]:
        if not req.options.enable_rag:
            return [], [], [], 0, "skipped", ""
        rag_query = self.business_rag_query(
            req, vision_data, parsed_scope=parsed_scope, profile=profile
        )
        t0 = perf_counter()
        try:
            snippets, sources, chunks = await asyncio.to_thread(
                lambda: self._retrieve_business_rag(rag_query, profile.analysis_type)
            )
            ms = int((perf_counter() - t0) * 1000)
            return list(snippets), list(sources), list(chunks), ms, "success", rag_query
        except Exception as exc:  # noqa: BLE001
            logger.warning("img_diag %s business rag failed: %s", profile.subtype, exc)
            return [], [], [], int((perf_counter() - t0) * 1000), "failed", rag_query

    async def _gather_img_diag_pack(self, req: AnalysisImgDiagRequest) -> _ImgDiagPack:
        profile = self._profile(req)
        lane_timeout = float(self._analysis_cfg.img_diag_lane_timeout_seconds)
        at = profile.analysis_type
        nl_req = AnalysisNL2SQLRequest(
            user_id=req.user_id,
            session_id=req.session_id,
            analysis_type=at,
            query=req.query.strip(),
            data_requirements_hint=list(req.data_requirements_hint or []),
            options=req.options,
        )
        degrade: list[str] = []

        async def vision_safe() -> tuple[dict[str, Any], int, str]:
            try:
                data, ms = await asyncio.wait_for(
                    self._lane_vision(req, profile), timeout=lane_timeout
                )
                if data.get("vision_skipped"):
                    return data, ms, "skipped"
                return data, ms, "success"
            except asyncio.TimeoutError:
                degrade.append("img_diag_vision_timeout")
                logger.warning("img_diag vision lane timeout after %ss", lane_timeout)
                return {"vision_lane_error": "timeout"}, int(lane_timeout * 1000), "timeout"
            except Exception as exc:  # noqa: BLE001
                degrade.append("img_diag_vision_failed")
                logger.warning("img_diag vision lane failed: %s", exc)
                return {"vision_lane_error": str(exc)}, 0, "failed"

        rag_mode = (self._analysis_cfg.img_diag_rag_mode or "vision_augmented").strip().lower()

        async def nl_safe() -> tuple[dict[str, Any], str]:
            try:
                st = await asyncio.wait_for(
                    self._lane_nl2sql_until_gate(nl_req),
                    timeout=lane_timeout,
                )
                return st, "success"
            except asyncio.TimeoutError:
                degrade.append("img_diag_nl2sql_timeout")
                logger.warning("img_diag nl2sql lane timeout after %ss", lane_timeout)
                return {
                    "nl2sql_calls": [],
                    "gathered_data": {},
                    "plan_tasks": [],
                    "plan_context": [],
                    "plan_rag_sources": [],
                    "quality_report": {"warnings": ["nl2sql lane timeout"]},
                    "task_status": {},
                    "node_latency_ms": {},
                    "planner_warnings": [],
                }, "timeout"
            except ValueError as exc:
                degrade.append(f"img_diag_nl2sql_blocked:{exc}")
                raise
            except Exception as exc:  # noqa: BLE001
                degrade.append("img_diag_nl2sql_failed")
                logger.exception("img_diag nl2sql lane failed")
                return {
                    "nl2sql_calls": [],
                    "gathered_data": {},
                    "plan_tasks": [],
                    "plan_context": [],
                    "plan_rag_sources": [],
                    "quality_report": {"warnings": [str(exc)]},
                    "task_status": {},
                    "node_latency_ms": {},
                    "planner_warnings": [str(exc)],
                }, "failed"

        async def prefetch_safe() -> tuple[list[str], list[dict[str, Any]], list[Any], int, str, str]:
            if not req.options.enable_rag or rag_mode not in ("hybrid", "parallel"):
                return [], [], [], 0, "skipped", ""
            try:
                return await asyncio.wait_for(
                    self._lane_business_rag_prefetch(req, profile),
                    timeout=lane_timeout,
                )
            except asyncio.TimeoutError:
                degrade.append("img_diag_business_rag_prefetch_timeout")
                return [], [], [], int(lane_timeout * 1000), "timeout", ""
            except Exception:  # noqa: BLE001
                degrade.append("img_diag_business_rag_prefetch_failed")
                return [], [], [], 0, "failed", ""

        if rag_mode in ("hybrid", "parallel"):
            v_pack, nl_pack, pf_pack = await asyncio.gather(vision_safe(), nl_safe(), prefetch_safe())
        else:
            v_pack, nl_pack = await asyncio.gather(vision_safe(), nl_safe())
            pf_pack = ([], [], [], 0, "skipped", "")

        vision_data, vision_ms, vision_status = v_pack
        nl_state, nl_status = nl_pack
        pf_snippets, pf_sources, pf_chunks, pf_rag_ms, pf_rag_status, pf_rag_query = pf_pack

        calls_raw = list(nl_state.get("nl2sql_calls") or [])
        calls = [AnalysisNL2SQLCall.model_validate(x) for x in calls_raw if isinstance(x, dict)]
        parsed_intent_snapshot = self._extract_parsed_intent_snapshot(calls)
        parsed_time_intent, parsed_scope_intent = self.split_parsed_intent_snapshot(parsed_intent_snapshot)
        parsed_scope = parsed_scope_intent or self._scope_from_intent(parsed_intent_snapshot)

        biz_snippets: list[str] = list(pf_snippets)
        biz_sources: list[dict[str, Any]] = list(pf_sources)
        biz_chunks: list[Any] = list(pf_chunks)
        rag_ms = pf_rag_ms
        rag_status = pf_rag_status
        rag_query = pf_rag_query
        aug_rag_query = ""

        if req.options.enable_rag and rag_mode in ("vision_augmented", "hybrid"):
            try:
                r_pack = await asyncio.wait_for(
                    self._lane_business_rag(
                        req, vision_data, profile, parsed_scope=parsed_scope or None
                    ),
                    timeout=lane_timeout,
                )
                aug_snippets, aug_sources, aug_chunks, aug_ms, aug_status, aug_rag_query = r_pack
                rag_ms += aug_ms
                rag_status = aug_status if aug_status != "skipped" else rag_status
                biz_snippets = self._merge_rag_text_lists(biz_snippets, aug_snippets)
                biz_sources = self._merge_rag_source_lists(biz_sources, aug_sources)
                seen_chunk_ids: set[str] = set()
                merged_chunks: list[Any] = []
                for ch in list(biz_chunks) + list(aug_chunks):
                    cid = getattr(ch, "chunk_id", None) or str(ch)
                    if cid in seen_chunk_ids:
                        continue
                    seen_chunk_ids.add(str(cid))
                    merged_chunks.append(ch)
                biz_chunks = merged_chunks
                rag_query = aug_rag_query or rag_query
            except asyncio.TimeoutError:
                degrade.append("img_diag_business_rag_timeout")
                rag_status = "timeout"
                rag_ms += int(lane_timeout * 1000)
            except Exception as exc:  # noqa: BLE001
                degrade.append("img_diag_business_rag_failed")
                rag_status = "failed"
                logger.warning("img_diag business rag lane failed: %s", exc)
        elif req.options.enable_rag and rag_mode == "parallel":
            rag_query = pf_rag_query
            if pf_rag_status == "failed":
                degrade.append("img_diag_business_rag_parallel_failed")

        parallel_trace = {
            "img_diag_subtype": profile.subtype,
            "vision_lane_ms": vision_ms,
            "vision_lane_status": vision_status,
            "nl2sql_lane_status": nl_status,
            "business_rag_prefetch_ms": pf_rag_ms,
            "business_rag_prefetch_status": pf_rag_status,
            "business_rag_lane_ms": rag_ms,
            "business_rag_lane_status": rag_status,
            "rag_depends_on_vision": rag_mode in ("vision_augmented", "hybrid"),
            "rag_query_mode": rag_mode,
            "orchestrator_topology": (
                "vision_nl_parallel_prefetch_then_serial_rag"
                if rag_mode == "hybrid"
                else (
                    "vision_nl_rag_parallel"
                    if rag_mode == "parallel"
                    else "vision_nl_parallel_then_serial_rag"
                )
            ),
            "rag_prefetch_query": (pf_rag_query[:500] if pf_rag_query else None),
            "rag_augmented_query": (aug_rag_query[:500] if aug_rag_query else None),
        }

        gathered_data = cast(dict[str, list[dict]], nl_state.get("gathered_data") or {})
        raw_tasks = list(nl_state.get("plan_tasks") or [])
        planned_calls = sum(1 for x in raw_tasks if isinstance(x, dict))
        plan_rag_sources = list(nl_state.get("plan_rag_sources") or [])
        plan_rag_chunks = list(nl_state.get("plan_rag_chunks") or [])
        quality_report = cast(dict[str, Any], nl_state.get("quality_report") or {})

        merged_rag_sources = (plan_rag_sources + biz_sources)[:64]
        used_business_rag = len(biz_snippets) > 0
        used_plan_rag = len(plan_rag_sources) > 0
        used_rag = used_business_rag or used_plan_rag
        rag_citations = self._build_analysis_rag_citations(
            plan_chunks=plan_rag_chunks if req.options.enable_rag else None,
            business_chunks=biz_chunks if req.options.enable_rag else None,
        )

        planning_ctx_parts = list(nl_state.get("plan_context") or [])
        if self._analysis_cfg.nl2sql_llm_planner_enabled:
            ir = nl_state.get("intent_llm_result")
            if isinstance(ir, dict):
                planning_ctx_parts.append(json.dumps(ir, ensure_ascii=False))
        if parsed_intent_snapshot:
            planning_ctx_parts.append(
                json.dumps({"parsed_intent": parsed_intent_snapshot}, ensure_ascii=False)
            )
        planning_ctx = "\n".join(planning_ctx_parts)[:4000] if planning_ctx_parts else None

        synthesis_prompt, synthesis_version = self._resolve_stage_template(
            stage="analysis_synthesis",
            analysis_type=at,
            user_id=req.user_id,
            default_text=profile.synthesis_default,
        )
        _rp, report_version = self._resolve_stage_template(
            stage="analysis_report",
            analysis_type=at,
            user_id=req.user_id,
            default_text=profile.report_default,
        )
        del _rp

        merged_blob: dict[str, Any] = {
            "structured_queries_snapshot": gathered_data,
            "vision_findings": vision_data,
            "parsed_intent": parsed_intent_snapshot,
            "parsed_time_intent": parsed_time_intent,
            "parsed_scope_intent": parsed_scope_intent,
            "user_query": req.query,
            "img_diag_subtype": profile.subtype,
        }

        request_id = str(nl_state.get("request_id") or f"anl_{uuid4().hex[:12]}")
        plan_id = str(nl_state.get("plan_id") or f"plan_{uuid4().hex[:10]}")

        return _ImgDiagPack(
            req=req,
            degrade=degrade,
            parallel_trace=parallel_trace,
            vision_data=vision_data,
            vision_ms=vision_ms,
            vision_status=vision_status,
            biz_snippets=biz_snippets,
            biz_sources=biz_sources,
            rag_ms=rag_ms,
            rag_status=rag_status,
            rag_query=rag_query,
            nl_state=nl_state,
            nl_status=nl_status,
            calls=calls,
            gathered_data=gathered_data,
            planned_calls=planned_calls,
            plan_rag_sources=plan_rag_sources,
            quality_report=quality_report,
            merged_rag_sources=merged_rag_sources,
            rag_citations=rag_citations,
            used_rag=used_rag,
            used_plan_rag=used_plan_rag,
            used_business_rag=used_business_rag,
            planning_ctx=planning_ctx,
            synthesis_prompt=synthesis_prompt,
            synthesis_version=synthesis_version,
            report_version=report_version,
            merged_blob=merged_blob,
            parsed_intent_snapshot=parsed_intent_snapshot,
            parsed_time_intent=parsed_time_intent,
            parsed_scope_intent=parsed_scope_intent,
            profile=profile,
            request_id=request_id,
            plan_id=plan_id,
        )

    def _finalize_img_diag_v2(self, pack: _ImgDiagPack, summary: str, syn_ms: int) -> AnalysisV2Result:
        req = pack.req
        profile = pack.profile
        at = profile.analysis_type
        data_mode = profile.data_mode
        suggestions = self._build_suggestions(summary, at, req.options.max_suggestions)
        data_cov = {
            "mode": data_mode,
            "img_diag_subtype": profile.subtype,
            "planned_calls": pack.planned_calls,
            "success_calls": sum(1 for c in pack.calls if c.status == "success"),
            "failed_calls": sum(1 for c in pack.calls if c.status == "failed"),
            "skipped_calls": sum(1 for c in pack.calls if c.status == "skipped"),
            "records": self._extract_records_from_gathered(pack.gathered_data),
            "data_quality_report": pack.quality_report,
            "parallel_lane_trace": pack.parallel_trace,
            "parsed_intent": pack.parsed_intent_snapshot,
            "parsed_time_intent": pack.parsed_time_intent,
            "parsed_scope_intent": pack.parsed_scope_intent,
            "rag_query": pack.rag_query[:2000] if pack.rag_query else None,
        }
        structured_report = self._build_structured_report(
            summary=summary,
            suggestions=suggestions,
            analysis_type=at,
            report_style=req.options.report_style,
            report_template=req.options.report_template,
            chart_mode=req.options.chart_mode,
            data_coverage=data_cov,
        )
        structured_report["vision_findings"] = pack.vision_data
        structured_report["parsed_intent"] = pack.parsed_intent_snapshot
        structured_report["parsed_time_intent"] = pack.parsed_time_intent
        structured_report["parsed_scope_intent"] = pack.parsed_scope_intent
        structured_report["user_query"] = req.query
        structured_report["img_diag_subtype"] = profile.subtype

        nl_state = pack.nl_state
        node_ms = dict(nl_state.get("node_latency_ms") or {})
        node_ms["vision_understanding_parallel"] = pack.vision_ms
        node_ms["business_rag_serial"] = pack.rag_ms
        node_ms["synthesis"] = syn_ms

        node_status = dict(nl_state.get("node_status") or {})
        node_status["vision_understanding_parallel"] = pack.vision_status
        node_status["business_rag_serial"] = pack.rag_status

        self._conv.append_assistant_message(
            req.user_id,
            req.session_id,
            summary,
            rag_citations=pack.rag_citations or None,
        )

        evidence = AnalysisEvidence(
            used_rag=pack.used_rag,
            rag_sources=pack.merged_rag_sources,
            rag_citations=pack.rag_citations,
            nl2sql_calls=pack.calls,
            data_coverage=data_cov,
            vision_findings=pack.vision_data,
        )

        trace = AnalysisTrace(
            plan_id=pack.plan_id,
            node_latency_ms=node_ms,
            template_versions={
                "intent": str(nl_state.get("intent_version") or ""),
                "data_plan": str(nl_state.get("data_plan_version") or ""),
                "synthesis": pack.synthesis_version,
                "report": pack.report_version,
            },
            execution_summary={
                "analysis_type": at,
                "data_mode": data_mode,
                "img_diag_subtype": profile.subtype,
                "started_at": datetime.now(timezone.utc).isoformat(),
                "used_rag": pack.used_rag,
                "planned_calls": pack.planned_calls,
                "orchestrator": "vision_nl_parallel_then_serial_rag",
                "graph_nodes": [
                    "normalize_request",
                    "parallel_vision_nl2sql",
                    "serial_business_rag",
                    "synthesis",
                    "finalize",
                ],
                "parallel_lane_trace": pack.parallel_trace,
                "parsed_intent": pack.parsed_intent_snapshot,
                "parsed_time_intent": pack.parsed_time_intent,
                "parsed_scope_intent": pack.parsed_scope_intent,
                "planner_warnings": [
                    w for w in (nl_state.get("planner_warnings") or []) if isinstance(w, str)
                ],
            },
            node_status=node_status,
            data_plan_trace=[
                {
                    "item_id": c.item_id,
                    "purpose": c.purpose,
                    "status": c.status,
                    "row_count": c.row_count,
                }
                for c in pack.calls
            ],
            degrade_reasons=sorted(set(pack.degrade)),
        )

        return AnalysisV2Result(
            request_id=pack.request_id,
            analysis_type=at,
            summary=summary,
            structured_report=structured_report,
            evidence=evidence,
            trace=trace,
        )

    async def run_with_img_diag(self, req: AnalysisImgDiagRequest) -> AnalysisV2Result:
        profile = self._profile(req)
        at = profile.analysis_type
        data_mode = profile.data_mode
        ANALYSIS_REQUEST_COUNT.labels(
            analysis_type=at,
            data_mode=data_mode,
            status="started",
        ).inc()
        try:
            pack = await self._gather_img_diag_pack(req)
            t_syn = perf_counter()
            summary = await self._generate_summary(
                query=req.query,
                analysis_type=at,
                data_mode=data_mode,
                data_blob=pack.merged_blob,
                context_snippets=pack.biz_snippets,
                system_prompt=pack.synthesis_prompt,
                planning_context=pack.planning_ctx,
            )
            syn_ms = int((perf_counter() - t_syn) * 1000)
            result = self._finalize_img_diag_v2(pack, summary, syn_ms)
            ANALYSIS_REQUEST_COUNT.labels(
                analysis_type=at,
                data_mode=data_mode,
                status="success",
            ).inc()
            return result
        except Exception:
            ANALYSIS_REQUEST_COUNT.labels(
                analysis_type=at,
                data_mode=data_mode,
                status="failed",
            ).inc()
            raise

    async def iter_img_diag_stream_events(
        self,
        req: AnalysisImgDiagRequest,
        *,
        on_complete: Callable[[AnalysisV2Result], Awaitable[None]] | None = None,
    ) -> AsyncIterator[dict[str, Any]]:
        """看图诊断：并行臂 + 串行 RAG 后 SSE 流式 synthesis。"""
        profile = self._profile(req)
        at = profile.analysis_type
        data_mode = profile.data_mode
        ANALYSIS_REQUEST_COUNT.labels(
            analysis_type=at,
            data_mode=data_mode,
            status="started",
        ).inc()
        try:
            t_pipeline = perf_counter()
            pack = await self._gather_img_diag_pack(req)
            request_id = pack.request_id
            plan_id = pack.plan_id

            yield {
                "event": "meta",
                "request_id": request_id,
                "plan_id": plan_id,
                "analysis_type": at,
                "data_mode": data_mode,
                "img_diag_subtype": profile.subtype,
                "orchestrator": profile.orchestrator_stream_id,
                "template_versions": {
                    "synthesis": pack.synthesis_version,
                    "report": pack.report_version,
                },
            }

            t_syn = perf_counter()
            parts: list[str] = []
            try:
                async for chunk in self._stream_summary_text(
                    query=req.query,
                    analysis_type=at,
                    data_mode=data_mode,
                    data_blob=pack.merged_blob,
                    context_snippets=pack.biz_snippets,
                    system_prompt=pack.synthesis_prompt,
                    planning_context=pack.planning_ctx,
                ):
                    parts.append(chunk)
                    yield {"event": "summary_delta", "text": chunk}
            except Exception:  # noqa: BLE001
                logger.exception("analysis img_diag %s stream summary failed", profile.subtype)
                fb = profile.stream_fallback_summary
                parts = [fb]
                yield {"event": "summary_delta", "text": fb}

            summary = "".join(parts)
            synthesis_ms = int((perf_counter() - t_syn) * 1000)
            yield {
                "event": "summary_complete",
                "request_id": request_id,
                "chars": len(summary),
                "synthesis_ms": synthesis_ms,
            }

            asyncio.create_task(
                self._img_diag_stream_background_finalize(
                    pack,
                    summary=summary,
                    synthesis_ms=synthesis_ms,
                    on_complete=on_complete,
                )
            )
            yield {"event": "structured_async_enqueued", "request_id": request_id}

            image_urls = [u for u in (req.image_urls or []) if isinstance(u, str) and u.strip()]
            first_nl2sql_sql = next(
                (c.sql for c in pack.calls if c.status == "success" and (c.sql or "").strip()),
                None,
            )
            finished_meta = build_analysis_finished_meta(
                request_id=request_id,
                plan_id=plan_id,
                analysis_type=at,
                data_mode=data_mode,
                used_rag=pack.used_rag,
                used_plan_rag=pack.used_plan_rag,
                used_business_rag=pack.used_business_rag,
                rag_citations=pack.rag_citations,
                start_ts=t_pipeline,
                synthesis_ms=synthesis_ms,
                used_nl2sql=bool(pack.calls),
                nl2sql_sql=first_nl2sql_sql,
                processed_image_urls=image_urls,
                original_image_urls=image_urls,
                retrieval_attempts=int(pack.used_plan_rag) + int(pack.used_business_rag),
                rag_namespace=(
                    str(pack.rag_citations[0].get("namespace") or "").strip() or None
                    if pack.rag_citations
                    else None
                ),
            )
            yield analysis_finished_sse_event(finished_meta)

            ANALYSIS_REQUEST_COUNT.labels(
                analysis_type=at,
                data_mode=data_mode,
                status="success",
            ).inc()
        except Exception:
            ANALYSIS_REQUEST_COUNT.labels(
                analysis_type=at,
                data_mode=data_mode,
                status="failed",
            ).inc()
            raise

    async def _img_diag_stream_background_finalize(
        self,
        pack: _ImgDiagPack,
        *,
        summary: str,
        synthesis_ms: int,
        on_complete: Callable[[AnalysisV2Result], Awaitable[None]] | None,
    ) -> None:
        try:
            result = self._finalize_img_diag_v2(pack, summary, synthesis_ms)
            payload = result.model_dump(mode="json")
            dumped = json.dumps(payload, ensure_ascii=False)
            if len(dumped) > 16000:
                dumped = dumped[:16000] + "...(truncated)"
            logger.info(
                "analysis_img_diag_%s_stream_full_json request_id=%s json=%s",
                pack.profile.subtype,
                result.request_id,
                dumped,
            )
            await dispatch_analysis_nl2sql_stream_structured(payload)
            if on_complete is not None:
                await on_complete(result)
        except Exception:  # noqa: BLE001
            logger.exception(
                "analysis_img_diag_stream_background_finalize failed request_id=%s",
                pack.request_id,
            )
