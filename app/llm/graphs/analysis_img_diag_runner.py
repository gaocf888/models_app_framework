from __future__ import annotations

"""
看图诊断编排（缺陷识别 img_diag_defect_ident / 泄爆分析 img_diag_leakage_burst）：
  视觉臂 ‖ NL2SQL 臂（规划→取数→质量门）并行
  → 业务 RAG 臂（串行，query 含用户问题 + 视觉 JSON 摘要）
  → synthesis → finalize

入参：`img_diag_subtype` + `query` + `image_urls`（泄爆可选图）。
可选前置：scope HITL（LangGraph）确认机组/受热面后再进入视觉‖NL2SQL 并行臂。
"""

import asyncio
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from time import perf_counter
from typing import Any, AsyncIterator, Awaitable, Callable, cast
from uuid import uuid4

from app.core.config import get_app_config
from app.core.logging import get_logger
from app.core.metrics import ANALYSIS_REQUEST_COUNT
from app.llm.graphs.analysis_finished_meta import (
    analysis_finished_sse_event,
    build_analysis_finished_meta,
)
from app.llm.graphs.analysis_graph_runner import AnalysisGraphRunner, _ANALYSIS_RAG_CITATIONS_EXCLUDED_NAMESPACES
from app.llm.graphs.analysis_img_diag_vision import (
    build_vision_multimodal_content,
    build_vision_rag_hint_query,
    format_vision_rag_hints_block,
)
from app.llm.graphs.img_diag_scope_graph import ImgDiagScopeHitlRunner
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
from app.services.analysis_img_diag_image_preprocessor import AnalysisImgDiagImagePreprocessor
from app.services.analysis_stream_hooks import dispatch_analysis_nl2sql_stream_structured
from app.services.chatbot_image_utils import build_user_message_with_images

logger = get_logger(__name__)

IMG_DIAG_DEFECT_IDENT_TYPE: AnalysisType = "img_diag_defect_ident"
IMG_DIAG_LEAKAGE_BURST_TYPE: AnalysisType = "img_diag_leakage_burst"

# 合成输入：各 plan item 的时间语义（与 reference SQL 一致；q2b/q2c 为历史全量）
_IMG_DIAG_PLAN_TIME_SCOPE: dict[str, str] = {
    "q1": "anchor_lookback_3d",
    "q2a": "anchor_lookback_3d",
    "q2b": "historical_no_time_filter",
    "q2c": "historical_no_time_filter",
    "q2d": "anchor_lookback_3d",
    "q2e": "anchor_lookback_3d",
    "q3": "anchor_lookback_3d",
    "q4": "anchor_lookback_3d",
    "q5": "anchor_lookback_3d",
}
_IMG_DIAG_SYNTHESIS_ROW_CAPS: dict[str, int] = {
    "q2b": 12,
    "q2c": 10,
    "q5": 15,
}
_IMG_DIAG_SYNTHESIS_DEFAULT_ROW_CAP = 40
_IMG_DIAG_TIME_SCOPE_LABELS: dict[str, str] = {
    "anchor_lookback_3d": "事故锚点向前3天",
    "historical_no_time_filter": "历史全量（SQL 无近3天时间过滤）",
    "unknown": "未知",
}


class ImgDiagScopeInterrupt(Exception):
    """scope HITL 需要用户确认时抛出（同步 API 转为 409）。"""

    def __init__(self, payload: dict[str, Any]) -> None:
        self.payload = payload
        super().__init__(payload.get("prompt") or "scope confirmation required")


@dataclass(frozen=True)
class _ImgDiagSubtypeProfile:
    subtype: ImgDiagSubtype
    analysis_type: AnalysisType
    data_mode: DataMode
    vision_scene: str
    vision_observe_scene: str
    vision_rag_hint_intent: str
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
        vision_observe_scene="analysis_img_diag_vision_defect_ident_observe",
        vision_rag_hint_intent=(
            "锅炉四管受热面常见缺陷 TOP10 可见形貌特征 表面形貌 识别要点 "
            "飞灰冲刷磨损沟槽 点蚀腐蚀坑 胀粗 轴向裂纹 周向裂纹 焊口 防磨瓦 氧化皮"
        ),
        rag_scene_label="缺陷识别",
        prefetch_rag_intent="规程通识 缺陷处置 运行监护 检修工艺",
        augmented_rag_intent="同类型缺陷历史处置案例 打磨补焊 换管 防磨瓦 运行监护 复测周期",
        synthesis_default=(
            "你是电厂承压管系缺陷识别分析师，需融合图像证据、数据库摘要与知识库片段，"
            "输出分风险等级的现场处置方案与历史同类案例摘要。"
        ),
        report_default=(
            "输出章节含：缺陷判定与风险等级、分风险等级处置方案（结合知识库）、"
            "历史同类案例摘要（最多3条）、结尾 AI 辅助分析说明。"
        ),
        stream_fallback_summary="缺陷识别分析生成失败，已返回基础报告，请稍后重试。",
        orchestrator_stream_id="img_diag_defect_ident_stream",
        images_required=True,
    ),
    "leakage_burst": _ImgDiagSubtypeProfile(
        subtype="leakage_burst",
        analysis_type=IMG_DIAG_LEAKAGE_BURST_TYPE,
        data_mode="img_diag_leakage_burst",
        vision_scene="analysis_img_diag_vision_leakage_burst",
        vision_observe_scene="analysis_img_diag_vision_leakage_burst_observe",
        vision_rag_hint_intent=(
            "锅炉爆管泄爆常见形貌 TOP10 爆口特征 环向开口 纵向裂口 穿孔泄漏 "
            "边缘减薄 邻管牵连 冲刷沟槽 腐蚀产物 胀粗开裂"
        ),
        rag_scene_label="泄爆分析",
        prefetch_rag_intent="规程通识 爆管预防 运行监护 检修工艺 标准条文",
        augmented_rag_intent=(
            "同位置同类型历史事故案例 同类型机组典型故障处理经验 标准规程条文 "
            "防控技术资料 同类爆管预防措施 同区域改造案例"
        ),
        synthesis_default=(
            "你是电厂锅炉受热面泄爆溯源高级分析师，需融合图像证据（若有）、"
            "库表摘要与知识库片段；结论摘要按三层逻辑总括，事故分析按五大类逐项综合叙述，"
            "并给出同类案例处置方案与事故预防措施。"
        ),
        report_default=(
            "输出章节含：结论摘要（含三层总结）、解析范围与时间窗、事故分析（五大类）、"
            "证据链、同类案例及处置方案、事故预防措施；结尾 AI 辅助说明。"
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
    confirmed_scope_intent: dict[str, Any] | None = None


class AnalysisImgDiagGraphRunner(AnalysisGraphRunner):
    """看图诊断（缺陷识别 / 泄爆分析）：scope HITL（可选）→ 并行 + 串行 RAG 编排。"""

    def _get_scope_hitl_runner(self) -> ImgDiagScopeHitlRunner:
        runner = getattr(self, "_scope_hitl_runner", None)
        if runner is None:
            runner = ImgDiagScopeHitlRunner(
                llm_client=self._llm,
                prompt_registry=self._prompts,
            )
            self._scope_hitl_runner = runner
        return runner

    async def _run_scope_hitl_phase(
        self,
        req: AnalysisImgDiagRequest,
        *,
        request_id: str | None = None,
    ) -> dict[str, Any]:
        """scope 人机协同；status=skipped|confirmed|interrupt|error。"""
        runner = self._get_scope_hitl_runner()
        return await runner.run_until_scope_confirmed_or_interrupt(
            req.model_dump(mode="json"),
            request_id=request_id,
        )

    @staticmethod
    def _image_urls_diag(image_urls: list[str] | None) -> dict[str, Any]:
        """供 vision/resume 诊断：统计有效 URL 数量并截断预览（避免日志过长）。"""
        raw = list(image_urls or [])
        urls = [u for u in raw if isinstance(u, str) and u.strip()]
        previews: list[str] = []
        for u in urls[:3]:
            s = u.strip()
            previews.append(s if len(s) <= 120 else f"{s[:117]}...")
        return {"url_count": len(urls), "raw_list_len": len(raw), "url_previews": previews}

    @staticmethod
    def _scope_interrupt_sse_event(result: dict[str, Any]) -> dict[str, Any]:
        intr = result.get("interrupt_payload") or {}
        return {
            "event": "img_diag_scope_input_required",
            "request_id": result.get("request_id"),
            "resume_token": result.get("resume_token"),
            "prompt": intr.get("prompt"),
            "scope_draft": intr.get("scope_draft"),
            "scope_draft_display": intr.get("scope_draft_display"),
            "missing_fields": intr.get("missing_fields") or [],
            "validation_error": intr.get("validation_error"),
            "suggested_actions": intr.get("suggested_actions")
            or ["confirm_scope", "edit_scope", "abort"],
            "interrupt_reason": intr.get("interrupt_reason"),
        }

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
    def _unique_purpose_label(base: str, used: set[str]) -> str:
        label = base
        suffix = 2
        while label in used:
            label = f"{base}·{suffix}"
            suffix += 1
        used.add(label)
        return label

    @classmethod
    def _synthesis_catalog_rule(
        cls,
        *,
        row_count: int,
        rows_in_snapshot: int,
        time_scope: str,
    ) -> tuple[str, str]:
        if row_count <= 0:
            return (
                "empty",
                "库表未检索到任何记录；「库表要点」与三层溯源/判定依据中"
                "不得写支持性数值或管排号；仅可写「库表未检索到」并标注证据不足；"
                "知识库内容仅可出现在「知识库要点」小节，不得冒充库表事实。",
            )
        if time_scope == "historical_no_time_filter":
            truncated = rows_in_snapshot < row_count
            status = "historical_truncated" if truncated else "historical_full"
            rule = (
                "本主题为历史全量统计，非事故近3天窗口；"
                "禁止描述为「近3天」「近期」「事故前3天」；"
                "管排号、壁厚、速率等数值仅可引用 structured_queries_snapshot 中"
                "对应 purpose 的可见行，禁止归纳 snapshot 未出现的排号。"
            )
            if truncated:
                rule += f"（snapshot 仅含 {rows_in_snapshot}/{row_count} 行样本，勿外推全量）"
            return status, rule
        if rows_in_snapshot < row_count:
            return (
                "truncated",
                f"snapshot 含 {rows_in_snapshot}/{row_count} 行；"
                "仅可引用 snapshot 内出现的字段与数值，禁止外推。",
            )
        return (
            "ok",
            "仅可引用 structured_queries_snapshot 中本 purpose 可见行的字段与数值。",
        )

    @classmethod
    def _prepare_img_diag_synthesis_queries(
        cls,
        gathered_data: dict[str, list[dict]],
        plan_tasks: list[dict[str, Any]],
        calls: list[AnalysisNL2SQLCall],
        *,
        analysis_type: str,
    ) -> tuple[dict[str, list[dict]], list[dict[str, Any]]]:
        """合成输入：purpose 语义键 + 行数上限 + 可查 catalog（空表/历史全量约束）。"""
        if analysis_type not in (IMG_DIAG_DEFECT_IDENT_TYPE, IMG_DIAG_LEAKAGE_BURST_TYPE):
            return gathered_data, []
        id_to_purpose: dict[str, str] = {}
        plan_order: list[str] = []
        for task in plan_tasks:
            if not isinstance(task, dict):
                continue
            item_id = str(task.get("item_id") or "").strip()
            purpose = str(task.get("purpose") or "").strip()
            if item_id and purpose:
                id_to_purpose[item_id] = purpose
                plan_order.append(item_id)
        call_rows: dict[str, int] = {}
        for call in calls:
            item_id = str(call.item_id or "").strip()
            if item_id:
                call_rows[item_id] = int(call.row_count or 0)
        ordered_ids = plan_order + [
            k for k in gathered_data if k not in plan_order
        ]
        labeled: dict[str, list[dict]] = {}
        catalog: list[dict[str, Any]] = []
        used_labels: set[str] = set()
        for item_id in ordered_ids:
            rows = list(gathered_data.get(item_id) or [])
            purpose = id_to_purpose.get(item_id, "结构化查询数据")
            label = cls._unique_purpose_label(purpose, used_labels)
            row_count = call_rows.get(item_id, len(rows))
            cap = _IMG_DIAG_SYNTHESIS_ROW_CAPS.get(
                item_id, _IMG_DIAG_SYNTHESIS_DEFAULT_ROW_CAP
            )
            snapshot_rows = rows[:cap] if len(rows) > cap else rows
            labeled[label] = snapshot_rows
            time_scope = _IMG_DIAG_PLAN_TIME_SCOPE.get(item_id, "unknown")
            status, rule = cls._synthesis_catalog_rule(
                row_count=row_count,
                rows_in_snapshot=len(snapshot_rows),
                time_scope=time_scope,
            )
            catalog.append(
                {
                    "purpose": purpose,
                    "row_count": row_count,
                    "rows_in_snapshot": len(snapshot_rows),
                    "time_scope": time_scope,
                    "time_scope_label": _IMG_DIAG_TIME_SCOPE_LABELS.get(
                        time_scope, time_scope
                    ),
                    "synthesis_status": status,
                    "synthesis_rule": rule,
                }
            )
        return labeled, catalog

    @staticmethod
    def _gathered_data_for_synthesis(
        gathered_data: dict[str, list[dict]],
        plan_tasks: list[dict[str, Any]],
        *,
        analysis_type: str,
    ) -> dict[str, list[dict]]:
        """合成 snapshot（兼容旧调用；catalog 见 _prepare_img_diag_synthesis_queries）。"""
        snapshot, _ = AnalysisImgDiagGraphRunner._prepare_img_diag_synthesis_queries(
            gathered_data,
            plan_tasks,
            [],
            analysis_type=analysis_type,
        )
        return snapshot

    @staticmethod
    def _append_report_constraints(synthesis_prompt: str, report_prompt: str) -> str:
        report = (report_prompt or "").strip()
        if not report:
            return synthesis_prompt
        return f"{synthesis_prompt.rstrip()}\n\n【报告格式约束】\n{report}"

    @staticmethod
    def _vision_synthesis_log_summary(vision_data: Any) -> dict[str, Any]:
        """合成前日志：vision_findings 摘要（不含 raw 全文）。"""
        if not isinstance(vision_data, dict):
            return {"present": False}
        if vision_data.get("vision_skipped"):
            return {
                "present": True,
                "vision_skipped": True,
                "reason": vision_data.get("reason"),
            }
        highlight_keys = (
            "burst_type",
            "location_visual",
            "burst_signals",
            "damage_morphology",
            "opening_shape",
            "defect_type",
            "risk_level",
            "confidence_notes",
        )
        summary: dict[str, Any] = {
            "present": True,
            "vision_skipped": False,
            "keys": [k for k in vision_data if k not in ("raw", "parse_error")][:24],
        }
        for key in highlight_keys:
            if key in vision_data and vision_data[key] is not None:
                val = vision_data[key]
                if isinstance(val, str) and len(val) > 200:
                    val = val[:200] + "…"
                summary[key] = val
        return summary

    def _log_vision_before_synthesis(self, pack: _ImgDiagPack) -> None:
        summary = self._vision_synthesis_log_summary(pack.vision_data)
        catalog = pack.merged_blob.get("structured_queries_catalog") or []
        empty_purposes = [
            c.get("purpose")
            for c in catalog
            if isinstance(c, dict) and int(c.get("row_count") or 0) <= 0
        ]
        logger.info(
            "img_diag synthesis vision_context subtype=%s request_id=%s user_id=%s session_id=%s %s",
            pack.profile.subtype,
            pack.request_id,
            pack.req.user_id,
            pack.req.session_id,
            json.dumps(summary, ensure_ascii=False, default=str),
        )
        if catalog:
            logger.info(
                "img_diag synthesis queries_catalog request_id=%s empty_purposes=%s catalog=%s",
                pack.request_id,
                empty_purposes,
                json.dumps(catalog, ensure_ascii=False, default=str),
            )

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
        if analysis_type in (IMG_DIAG_DEFECT_IDENT_TYPE, IMG_DIAG_LEAKAGE_BURST_TYPE):
            return self._build_img_diag_summary_user_content(
                query=query,
                analysis_type=analysis_type,
                data_mode=data_mode,
                data_blob=data_blob,
                context_snippets=context_snippets,
                planning_context=planning_context,
            )
        return super()._build_summary_user_content(
            query=query,
            analysis_type=analysis_type,
            data_mode=data_mode,
            data_blob=data_blob,
            context_snippets=context_snippets,
            planning_context=planning_context,
        )

    def _build_img_diag_summary_user_content(
        self,
        *,
        query: str,
        analysis_type: str,
        data_mode: str,
        data_blob: dict,
        context_snippets: list[str],
        planning_context: str | None = None,
    ) -> str:
        """看图诊断 synthesis user 消息：vision_findings、catalog 完整保留，snapshot 按预算截断。"""
        max_chars = int(self._analysis_cfg.synthesis_gathered_json_max_chars)
        vision_data = data_blob.get("vision_findings")
        catalog = data_blob.get("structured_queries_catalog")
        rest_blob = {
            k: v
            for k, v in data_blob.items()
            if k not in ("vision_findings", "structured_queries_catalog")
        }
        vision_block = json.dumps(
            {"vision_findings": vision_data},
            ensure_ascii=False,
            default=self._json_fallback,
        )
        catalog_block = json.dumps(
            {"structured_queries_catalog": catalog},
            ensure_ascii=False,
            default=self._json_fallback,
        )
        vision_cap = min(6000, max(800, max_chars // 3))
        if len(vision_block) > vision_cap:
            vision_block = vision_block[:vision_cap]
        catalog_cap = min(4000, max(600, max_chars // 4))
        if len(catalog_block) > catalog_cap:
            catalog_block = catalog_block[:catalog_cap]
        rest_budget = max(500, max_chars - len(vision_block) - len(catalog_block) - 200)
        rest_preview = json.dumps(
            rest_blob,
            ensure_ascii=False,
            default=self._json_fallback,
        )[:rest_budget]
        rag_text = "\n".join(f"- {s}" for s in context_snippets[:8])
        pc = (planning_context or "").strip()
        planning_block = f"\n分阶段规划意图(结构化要点):\n{pc[:2000]}\n" if pc else ""
        vision_skipped = (
            isinstance(vision_data, dict) and bool(vision_data.get("vision_skipped"))
        )
        vision_hint = (
            "（vision_skipped=true，报告须说明未提供图片）"
            if vision_skipped
            else "（vision_skipped 不为 true 时，报告「图像可见」须引用下列字段，禁止写未提供/无图）"
        )
        catalog_hint = "（synthesis_status=empty 的主题禁止写库表支持性结论）"
        return (
            f"分析类型: {analysis_type}\n"
            f"数据来源模式: {data_mode}\n"
            f"用户问题: {query}\n"
            f"{planning_block}"
            f"视觉结构化结果(JSON，合成时必须优先使用){vision_hint}:\n{vision_block}\n"
            f"库表查询目录(JSON，合成前必读；row_count=0 须写库表未检索到，禁止用知识库冒充){catalog_hint}:\n{catalog_block}\n"
            f"库表明细 snapshot(JSON截断，数值/管排号仅可引用本块与目录允许范围):\n{rest_preview}\n"
            f"RAG参考片段:\n{rag_text}"
        ).strip()

    @staticmethod
    def _finished_meta_parsed_intent(
        pack: _ImgDiagPack,
    ) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
        scope = pack.confirmed_scope_intent or pack.parsed_scope_intent or None
        time_part = pack.parsed_time_intent or None
        if scope:
            scope = {
                k: v
                for k, v in scope.items()
                if v is not None and (not isinstance(v, str) or v.strip())
            } or None
        if time_part:
            time_part = {
                k: v
                for k, v in time_part.items()
                if v is not None and (not isinstance(v, str) or str(v).strip())
            } or None
        return scope, time_part

    def _build_img_diag_finished_meta(
        self,
        pack: _ImgDiagPack,
        *,
        request_id: str,
        plan_id: str,
        start_ts: float,
        synthesis_ms: int,
        image_urls: list[str],
    ) -> dict[str, Any]:
        first_nl2sql_sql = next(
            (c.sql for c in pack.calls if c.status == "success" and (c.sql or "").strip()),
            None,
        )
        parsed_scope, parsed_time = self._finished_meta_parsed_intent(pack)
        return build_analysis_finished_meta(
            request_id=request_id,
            plan_id=plan_id,
            analysis_type=pack.profile.analysis_type,
            data_mode=pack.profile.data_mode,
            used_rag=pack.used_rag,
            used_plan_rag=pack.used_plan_rag,
            used_business_rag=pack.used_business_rag,
            rag_citations=pack.rag_citations,
            start_ts=start_ts,
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
            parsed_scope_intent=parsed_scope,
            parsed_time_intent=parsed_time,
        )

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

    def _get_img_preprocessor(self) -> AnalysisImgDiagImagePreprocessor:
        proc = getattr(self, "_img_diag_preprocessor", None)
        if proc is None:
            proc = AnalysisImgDiagImagePreprocessor()
            self._img_diag_preprocessor = proc
        return proc

    def _vision_template_content(self, scene: str, user_id: str, fallback: str) -> str:
        tpl = self._prompts.get_template(scene=scene, user_id=user_id, version=None)
        if tpl and tpl.content.strip():
            return tpl.content.strip()
        return fallback

    async def _retrieve_vision_rag_hint_block(
        self,
        req: AnalysisImgDiagRequest,
        profile: _ImgDiagSubtypeProfile,
    ) -> tuple[str, list[str], str]:
        """视觉臂前置 RAG：召回 TOP N 常见缺陷/爆口形貌对照清单。"""
        cfg = self._analysis_cfg
        top_n = int(cfg.img_diag_vision_rag_hint_top_n)
        if not cfg.img_diag_vision_rag_hint_enabled or not req.options.enable_rag:
            block, items = format_vision_rag_hints_block([], top_n=top_n, subtype=profile.subtype)
            return block, items, ""

        rag_q = build_vision_rag_hint_query(
            req,
            rag_scene_label=profile.rag_scene_label,
            hint_intent=profile.vision_rag_hint_intent,
        )
        snippets: list[str] = []
        try:
            snippets, _, _ = await asyncio.to_thread(
                lambda: self._retrieve_rag_with_sources(
                    query=self._build_business_rag_recall_query(rag_q, profile.analysis_type),
                    rerank_query=self._build_business_rag_rerank_query(rag_q, profile.analysis_type),
                    namespace=None,
                    top_k=max(top_n + 2, 12),
                    scene="analysis",
                    exclude_namespaces=_ANALYSIS_RAG_CITATIONS_EXCLUDED_NAMESPACES,
                )
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("img_diag vision rag hint retrieve failed subtype=%s err=%s", profile.subtype, exc)
        block, items = format_vision_rag_hints_block(snippets, top_n=top_n, subtype=profile.subtype)
        return block, items, rag_q

    async def _run_vision_llm(
        self,
        *,
        image_urls: list[str],
        text_header: str,
        max_tokens: int,
    ) -> str:
        cfg = self._analysis_cfg
        messages = [
            {
                "role": "user",
                "content": build_vision_multimodal_content(
                    text_header=text_header,
                    image_urls=image_urls,
                ),
            }
        ]
        return await self._llm.chat(
            model=get_app_config().llm.default_model,  # type: ignore[arg-type]
            messages=messages,
            timeout=float(cfg.img_diag_vision_timeout_seconds),
            temperature=float(cfg.img_diag_vision_temperature),
            max_tokens=max_tokens,
        )

    async def _lane_vision(
        self, req: AnalysisImgDiagRequest, profile: _ImgDiagSubtypeProfile
    ) -> tuple[dict[str, Any], int]:
        url_diag = self._image_urls_diag(req.image_urls)
        urls = [u for u in (req.image_urls or []) if isinstance(u, str) and u.strip()]
        if not urls:
            logger.info(
                "img_diag vision skipped subtype=%s user_id=%s session_id=%s "
                "reason=no_image_provided url_count=0 raw_list_len=%s",
                profile.subtype,
                req.user_id,
                req.session_id,
                url_diag["raw_list_len"],
            )
            return (
                {
                    "vision_skipped": True,
                    "reason": "no_image_provided",
                    "notes": "未提供图片，泄爆分析将仅依据用户问题、库表与知识库推理。",
                },
                0,
            )
        t0 = perf_counter()
        cfg = self._analysis_cfg
        vision_model = get_app_config().llm.default_model

        processed_urls = await self._get_img_preprocessor().preprocess_urls(urls)
        rag_block, rag_items, rag_q = await self._retrieve_vision_rag_hint_block(req, profile)

        observe_fallback = (
            "先逐条列出照片中可见事实（部位、颜色、纹理、走向、尺寸线索），不要分类、不要 JSON。"
            if profile.subtype == "defect_ident"
            else "先逐条列出爆口/损伤的可见事实（开口形状、边缘、邻管、飞溅痕迹等），不要分类、不要 JSON。"
        )
        observe_instructions = self._vision_template_content(
            profile.vision_observe_scene,
            req.user_id,
            observe_fallback,
        )
        observe_header = (
            f"用户问题: {req.query}\n\n{rag_block}\n\n{observe_instructions}"
        )

        json_fallback = (
            "你是承压部件缺陷图像分析助手；基于下列「可见事实观察」与对照清单输出单个 JSON 对象。"
            if profile.subtype == "defect_ident"
            else "你是锅炉泄爆/爆口图像分析助手；基于下列「可见事实观察」与对照清单输出单个 JSON 对象。"
        )
        json_instructions = self._vision_template_content(
            profile.vision_scene,
            req.user_id,
            json_fallback,
        )

        logger.info(
            "img_diag vision start subtype=%s user_id=%s session_id=%s "
            "url_count=%s processed=%s model=%s two_stage=%s rag_hint_items=%s rag_q_len=%s url_previews=%s",
            profile.subtype,
            req.user_id,
            req.session_id,
            len(urls),
            len(processed_urls),
            vision_model,
            bool(cfg.img_diag_vision_two_stage_enabled),
            len(rag_items),
            len(rag_q or ""),
            url_diag["url_previews"],
        )

        observations = ""
        if cfg.img_diag_vision_two_stage_enabled:
            observations = (
                await self._run_vision_llm(
                    image_urls=processed_urls,
                    text_header=observe_header,
                    max_tokens=int(cfg.img_diag_vision_observe_max_tokens),
                )
            ).strip()

        json_header_parts = [f"用户问题: {req.query}", rag_block]
        if observations:
            json_header_parts.append(f"【阶段一·可见事实观察（须优先采信）】\n{observations}")
        json_header_parts.append(json_instructions)
        json_header = "\n\n".join(json_header_parts)

        raw = await self._run_vision_llm(
            image_urls=processed_urls,
            text_header=json_header,
            max_tokens=int(cfg.img_diag_vision_json_max_tokens),
        )
        ms = int((perf_counter() - t0) * 1000)
        parsed = extract_json_object_from_llm_text(raw)
        if parsed is None:
            try:
                parsed = json.loads(raw.strip())
            except Exception:  # noqa: BLE001
                parsed = {"raw_text": (raw or "")[:8000], "parse_error": "vision_output_not_json"}
        if isinstance(parsed, dict):
            if observations:
                parsed["visual_observations"] = observations[:4000]
            if rag_items:
                parsed["vision_rag_hint_items"] = rag_items[:12]
                parsed["vision_rag_hints_used"] = True
            if cfg.img_diag_vision_preprocess_enabled:
                parsed["vision_image_preprocessed"] = True
            if cfg.img_diag_vision_two_stage_enabled:
                parsed["vision_two_stage"] = True
        parse_ok = isinstance(parsed, dict) and "parse_error" not in parsed
        logger.info(
            "img_diag vision done subtype=%s url_count=%s ms=%s parse_ok=%s "
            "two_stage=%s rag_hints=%s result_keys=%s",
            profile.subtype,
            len(urls),
            ms,
            parse_ok,
            bool(cfg.img_diag_vision_two_stage_enabled),
            len(rag_items),
            list(parsed.keys())[:14] if isinstance(parsed, dict) else [],
        )
        return parsed, ms

    async def _img_diag_normalize_request(
        self,
        state: dict[str, Any],
        *,
        image_urls: list[str],
        request_id: str | None = None,
    ) -> dict[str, Any]:
        """写入 request_id/plan_id 与会话用户消息（含图片 URL 块，供 GET /chatbot/sessions/messages 解析）。"""
        req = AnalysisNL2SQLRequest.model_validate(state["nl2sql_request"])
        t0 = perf_counter()
        urls = [u for u in image_urls if isinstance(u, str) and u.strip()]
        content = (
            build_user_message_with_images(
                req.query,
                urls,
                original_image_urls=urls,
                processed_image_urls=urls,
            )
            if urls
            else req.query
        )
        self._conv.append_user_message(req.user_id, req.session_id, content)
        ms = int((perf_counter() - t0) * 1000)
        rid = (request_id or "").strip() or f"anl_{uuid4().hex[:12]}"
        return {
            "request_id": rid,
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

    async def _lane_nl2sql_until_gate(
        self,
        nl_req: AnalysisNL2SQLRequest,
        *,
        image_urls: list[str] | None = None,
        request_id: str | None = None,
    ) -> dict[str, Any]:
        state: dict[str, Any] = {"nl2sql_request": nl_req.model_dump(mode="json")}
        state.update(
            await self._img_diag_normalize_request(
                state,
                image_urls=list(image_urls or []),
                request_id=request_id,
            )
        )
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

    async def _gather_img_diag_pack(
        self,
        req: AnalysisImgDiagRequest,
        *,
        confirmed_scope: dict[str, Any] | None = None,
        scope_intent_text: str | None = None,
        request_id: str | None = None,
    ) -> _ImgDiagPack:
        profile = self._profile(req)
        pack_url_diag = self._image_urls_diag(req.image_urls)
        logger.info(
            "img_diag gather_pack start subtype=%s request_id=%s scope_hitl_confirmed=%s "
            "image_urls url_count=%s raw_list_len=%s url_previews=%s",
            profile.subtype,
            (request_id or "").strip() or "-",
            bool(confirmed_scope),
            pack_url_diag["url_count"],
            pack_url_diag["raw_list_len"],
            pack_url_diag["url_previews"],
        )
        lane_timeout = float(self._analysis_cfg.img_diag_lane_timeout_seconds)
        at = profile.analysis_type
        nl_req = AnalysisNL2SQLRequest(
            user_id=req.user_id,
            session_id=req.session_id,
            analysis_type=at,
            query=req.query.strip(),
            data_requirements_hint=list(req.data_requirements_hint or []),
            options=req.options,
            confirmed_scope=confirmed_scope,
            scope_intent_text=scope_intent_text,
        )
        degrade: list[str] = []

        async def vision_safe() -> tuple[dict[str, Any], int, str]:
            try:
                data, ms = await asyncio.wait_for(
                    self._lane_vision(req, profile), timeout=lane_timeout
                )
                if data.get("vision_skipped"):
                    status = "skipped"
                else:
                    status = "success"
                logger.info(
                    "img_diag vision lane outcome subtype=%s status=%s ms=%s "
                    "skip_reason=%s lane_error=%s",
                    profile.subtype,
                    status,
                    ms,
                    data.get("reason"),
                    data.get("vision_lane_error"),
                )
                return data, ms, status
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
                    self._lane_nl2sql_until_gate(
                        nl_req,
                        image_urls=req.image_urls,
                        request_id=request_id,
                    ),
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
        if not parsed_intent_snapshot and confirmed_scope:
            parsed_intent_snapshot = {
                "parse_mode": "human_confirmed",
                "scope_question": scope_intent_text or req.query,
                "scope": {
                    "boiler": confirmed_scope.get("boiler"),
                    "device_name": confirmed_scope.get("device_name"),
                    "piperow_name": confirmed_scope.get("piperow_name"),
                    "row_no": confirmed_scope.get("row_no"),
                    "tube_no": confirmed_scope.get("tube_no"),
                },
                "time_window": confirmed_scope.get("time_window"),
                "time_anchor": confirmed_scope.get("time_anchor"),
            }
        parsed_time_intent, parsed_scope_intent = self.split_parsed_intent_snapshot(parsed_intent_snapshot)
        parsed_scope = parsed_scope_intent or self._scope_from_intent(parsed_intent_snapshot)
        if confirmed_scope:
            parsed_scope = dict(confirmed_scope)

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
            "scope_hitl_confirmed": bool(confirmed_scope),
            "scope_intent_text": (scope_intent_text[:500] if scope_intent_text else None),
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
        report_prompt, report_version = self._resolve_stage_template(
            stage="analysis_report",
            analysis_type=at,
            user_id=req.user_id,
            default_text=profile.report_default,
        )
        synthesis_prompt = self._append_report_constraints(synthesis_prompt, report_prompt)

        synthesis_data, queries_catalog = self._prepare_img_diag_synthesis_queries(
            gathered_data,
            [t for t in raw_tasks if isinstance(t, dict)],
            calls,
            analysis_type=at,
        )

        merged_blob: dict[str, Any] = {
            "vision_findings": vision_data,
            "structured_queries_catalog": queries_catalog,
            "structured_queries_snapshot": synthesis_data,
            "parsed_intent": parsed_intent_snapshot,
            "parsed_time_intent": parsed_time_intent,
            "parsed_scope_intent": parsed_scope_intent,
            "confirmed_scope_intent": confirmed_scope,
            "user_query": req.query,
            "img_diag_subtype": profile.subtype,
            "quality_report": quality_report,
        }

        rid = str(nl_state.get("request_id") or request_id or f"anl_{uuid4().hex[:12]}")
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
            confirmed_scope_intent=confirmed_scope,
            profile=profile,
            request_id=rid,
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
            "confirmed_scope_intent": pack.confirmed_scope_intent,
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
        structured_report["confirmed_scope_intent"] = pack.confirmed_scope_intent
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
                "orchestrator": "scope_hitl_then_vision_nl_parallel_then_serial_rag"
                if pack.confirmed_scope_intent
                else "vision_nl_parallel_then_serial_rag",
                "graph_nodes": (
                    [
                        "scope_preflight_llm",
                        "scope_human_confirm",
                        "scope_db_validate",
                        "normalize_request",
                        "parallel_vision_nl2sql",
                        "serial_business_rag",
                        "synthesis",
                        "finalize",
                    ]
                    if pack.confirmed_scope_intent
                    else [
                        "normalize_request",
                        "parallel_vision_nl2sql",
                        "serial_business_rag",
                        "synthesis",
                        "finalize",
                    ]
                ),
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
            scope_result = await self._run_scope_hitl_phase(req)
            if scope_result.get("status") == "interrupt":
                raise ImgDiagScopeInterrupt(self._scope_interrupt_sse_event(scope_result))
            if scope_result.get("status") == "error":
                raise ValueError(scope_result.get("message") or "scope hitl failed")
            confirmed = (
                scope_result.get("confirmed_scope_intent")
                if scope_result.get("status") == "confirmed"
                else None
            )
            scope_text = (
                scope_result.get("scope_intent_text")
                if scope_result.get("status") == "confirmed"
                else None
            )
            rid = scope_result.get("request_id")
            pack = await self._gather_img_diag_pack(
                req,
                confirmed_scope=confirmed,
                scope_intent_text=scope_text,
                request_id=rid,
            )
            t_syn = perf_counter()
            self._log_vision_before_synthesis(pack)
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
            scope_result = await self._run_scope_hitl_phase(req)
            if scope_result.get("status") == "interrupt":
                yield self._scope_interrupt_sse_event(scope_result)
                return
            if scope_result.get("status") == "error":
                raise ValueError(scope_result.get("message") or "scope hitl failed")
            confirmed = (
                scope_result.get("confirmed_scope_intent")
                if scope_result.get("status") == "confirmed"
                else None
            )
            scope_text = (
                scope_result.get("scope_intent_text")
                if scope_result.get("status") == "confirmed"
                else None
            )
            rid = scope_result.get("request_id")
            pack = await self._gather_img_diag_pack(
                req,
                confirmed_scope=confirmed,
                scope_intent_text=scope_text,
                request_id=rid,
            )
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
            self._log_vision_before_synthesis(pack)
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
            finished_meta = self._build_img_diag_finished_meta(
                pack,
                request_id=request_id,
                plan_id=plan_id,
                start_ts=t_pipeline,
                synthesis_ms=synthesis_ms,
                image_urls=image_urls,
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

    async def iter_img_diag_scope_resume_stream_events(
        self,
        *,
        resume_token: str,
        user_id: str,
        session_id: str,
        action: str,
        payload: dict[str, Any] | None = None,
        on_complete: Callable[[AnalysisV2Result], Awaitable[None]] | None = None,
    ) -> AsyncIterator[dict[str, Any]]:
        """scope HITL resume 后继续看图诊断主流（meta → synthesis → finished）。"""
        token_preview = (
            f"{resume_token[:20]}..." if len(resume_token) > 20 else resume_token
        )
        logger.info(
            "img_diag scope resume start user_id=%s session_id=%s action=%s resume_token=%s",
            user_id,
            session_id,
            action,
            token_preview,
        )
        runner = self._get_scope_hitl_runner()
        scope_result = await runner.resume_until_confirmed_or_interrupt(
            resume_token=resume_token,
            user_id=user_id,
            session_id=session_id,
            action=action,
            payload=payload,
        )
        logger.info(
            "img_diag scope resume phase done status=%s request_id=%s",
            scope_result.get("status"),
            scope_result.get("request_id"),
        )
        if scope_result.get("status") == "interrupt":
            yield self._scope_interrupt_sse_event(scope_result)
            return
        if scope_result.get("status") == "error":
            yield {
                "event": "img_diag_error",
                "message": scope_result.get("message") or "scope resume failed",
            }
            return
        req_dict = scope_result.get("img_diag_request") or {}
        dict_url_diag = self._image_urls_diag(
            req_dict.get("image_urls") if isinstance(req_dict, dict) else None
        )
        req = AnalysisImgDiagRequest.model_validate(req_dict)
        req_url_diag = self._image_urls_diag(req.image_urls)
        logger.info(
            "img_diag scope resume restored img_diag_request request_id=%s "
            "dict_url_count=%s validated_url_count=%s raw_dict_list_len=%s url_previews=%s",
            scope_result.get("request_id"),
            dict_url_diag["url_count"],
            req_url_diag["url_count"],
            dict_url_diag["raw_list_len"],
            req_url_diag["url_previews"],
        )
        profile = self._profile(req)
        at = profile.analysis_type
        data_mode = profile.data_mode
        confirmed = scope_result.get("confirmed_scope_intent")
        scope_text = scope_result.get("scope_intent_text")
        t_pipeline = perf_counter()
        pack = await self._gather_img_diag_pack(
            req,
            confirmed_scope=confirmed,
            scope_intent_text=scope_text,
            request_id=scope_result.get("request_id"),
        )
        logger.info(
            "img_diag scope resume gather_pack done request_id=%s vision_lane_status=%s "
            "vision_skipped=%s vision_ms=%s",
            pack.request_id,
            pack.vision_status,
            bool(pack.vision_data.get("vision_skipped")),
            pack.vision_ms,
        )
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
        self._log_vision_before_synthesis(pack)
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
            logger.exception("analysis img_diag scope resume stream summary failed")
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
        finished_meta = self._build_img_diag_finished_meta(
            pack,
            request_id=request_id,
            plan_id=plan_id,
            start_ts=t_pipeline,
            synthesis_ms=synthesis_ms,
            image_urls=image_urls,
        )
        yield analysis_finished_sse_event(finished_meta)

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
