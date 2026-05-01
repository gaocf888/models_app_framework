from __future__ import annotations

"""
看图诊断（img_diag）编排：视觉理解 ‖ NL2SQL（规划→取数→质量门）‖ 业务 RAG 并行，再文本合成。

对外入口：`AnalysisImgDiagGraphRunner.run_with_img_diag`。
"""

import asyncio
import json
from datetime import datetime, timezone
from time import perf_counter
from typing import Any, cast
from uuid import uuid4

from app.core.logging import get_logger
from app.core.metrics import ANALYSIS_REQUEST_COUNT
from app.llm.graphs.analysis_graph_runner import AnalysisGraphRunner
from app.models.analysis import (
    AnalysisEvidence,
    AnalysisImgDiagRequest,
    AnalysisNL2SQLCall,
    AnalysisNL2SQLRequest,
    AnalysisTrace,
    AnalysisV2Result,
)
from app.models.analysis_nl2sql_llm import extract_json_object_from_llm_text

logger = get_logger(__name__)


class AnalysisImgDiagGraphRunner(AnalysisGraphRunner):
    """在 `AnalysisGraphRunner` 能力之上提供看图诊断并行编排。"""

    @staticmethod
    def bridge_nl2sql_query(req: AnalysisImgDiagRequest) -> str:
        """供 NL2SQL 规划与取数使用的拼接问题（含机组 ID 与位置）。"""
        struct_txt = ""
        if req.leak_location_struct:
            try:
                struct_txt = json.dumps(req.leak_location_struct, ensure_ascii=False)
            except Exception:  # noqa: BLE001
                struct_txt = ""
        lines = [
            "【看图诊断】结合机组与泄漏/拍照位置查询结构化数据，支撑爆管/泄漏原因分析与处置建议。",
            f"机组ID: {req.unit_id}",
            f"泄漏/拍照位置: {req.leak_location_text}",
        ]
        if struct_txt:
            lines.append(f"位置结构化(JSON): {struct_txt}")
        lines.append(f"用户问题: {req.query}")
        return "\n".join(lines)

    @staticmethod
    def business_rag_query(req: AnalysisImgDiagRequest) -> str:
        """业务 RAG 检索语句（不含视觉结论，可与视觉并行）。"""
        return (
            f"{req.query}\n机组ID:{req.unit_id}\n设备位置:{req.leak_location_text}\n"
            "爆管 泄漏 过热器 蠕变 磨损 冲蚀 处置要点 检修建议"
        )

    def _substitute_img_diag_placeholders(self, state: dict[str, Any], req: AnalysisImgDiagRequest) -> None:
        raw_tasks = list(state.get("plan_tasks") or [])
        uid = req.unit_id
        loc = req.leak_location_text
        struct_json = ""
        if req.leak_location_struct:
            try:
                struct_json = json.dumps(req.leak_location_struct, ensure_ascii=False)
            except Exception:  # noqa: BLE001
                struct_json = ""
        out: list[dict[str, Any]] = []
        for item in raw_tasks:
            if not isinstance(item, dict):
                continue
            q = str(item.get("question") or "")
            q = q.replace("{unit_id}", uid).replace("{location}", loc)
            q = q.replace("{location_struct}", struct_json or loc)
            row = dict(item)
            row["question"] = q
            out.append(row)
        state["plan_tasks"] = out

    async def _lane_vision(self, req: AnalysisImgDiagRequest) -> tuple[dict[str, Any], int]:
        t0 = perf_counter()
        tpl = self._prompts.get_template(scene="analysis_img_diag_vision", user_id=req.user_id, version=None)
        instructions = (
            tpl.content.strip()
            if tpl and tpl.content.strip()
            else "你是承压部件检修图像分析助手，仅描述可见证据；输出必须为单个 JSON 对象。"
        )
        header = (
            f"机组ID: {req.unit_id}\n泄漏/拍照位置: {req.leak_location_text}\n用户问题: {req.query}\n\n{instructions}"
        )
        content: list[dict[str, Any]] = [{"type": "text", "text": header}]
        for url in req.image_urls:
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

    async def _lane_nl2sql_until_gate(self, nl_req: AnalysisNL2SQLRequest, img_req: AnalysisImgDiagRequest) -> dict[str, Any]:
        state: dict[str, Any] = {"nl2sql_request": nl_req.model_dump(mode="json")}
        state.update(await self._lg_nl2sql_normalize_request(state))
        state.update(await self._lg_nl2sql_plan_context_rag(state))
        state.update(await self._lg_nl2sql_intent_llm(state))
        state.update(await self._lg_nl2sql_plan_llm_merge(state))
        self._substitute_img_diag_placeholders(state, img_req)
        state.update(await self._lg_nl2sql_acquire_data(state))
        state.update(await self._lg_nl2sql_data_quality_gate(state))
        return state

    async def run_with_img_diag(self, req: AnalysisImgDiagRequest) -> AnalysisV2Result:
        ANALYSIS_REQUEST_COUNT.labels(analysis_type="img_diag", data_mode="img_diag", status="started").inc()
        lane_timeout = float(self._analysis_cfg.img_diag_lane_timeout_seconds)
        nl_req = AnalysisNL2SQLRequest(
            user_id=req.user_id,
            session_id=req.session_id,
            analysis_type="img_diag",
            query=self.bridge_nl2sql_query(req),
            data_requirements_hint=list(req.data_requirements_hint or []),
            options=req.options,
        )
        degrade: list[str] = []
        parallel_trace: dict[str, Any] = {}

        async def vision_safe() -> tuple[dict[str, Any], int, str]:
            try:
                data, ms = await asyncio.wait_for(self._lane_vision(req), timeout=lane_timeout)
                return data, ms, "success"
            except asyncio.TimeoutError:
                degrade.append("img_diag_vision_timeout")
                logger.warning("img_diag vision lane timeout after %ss", lane_timeout)
                return {"vision_lane_error": "timeout"}, int(lane_timeout * 1000), "timeout"
            except Exception as exc:  # noqa: BLE001
                degrade.append("img_diag_vision_failed")
                logger.warning("img_diag vision lane failed: %s", exc)
                return {"vision_lane_error": str(exc)}, 0, "failed"

        async def rag_safe() -> tuple[list[str], list[dict[str, Any]], int, str]:
            if not req.options.enable_rag:
                return [], [], 0, "skipped"
            t0 = perf_counter()
            try:
                snippets, sources = await asyncio.wait_for(
                    asyncio.to_thread(lambda: self._retrieve_business_rag(self.business_rag_query(req), "img_diag")),
                    timeout=lane_timeout,
                )
                ms = int((perf_counter() - t0) * 1000)
                return list(snippets), list(sources), ms, "success"
            except asyncio.TimeoutError:
                degrade.append("img_diag_business_rag_timeout")
                return [], [], int(lane_timeout * 1000), "timeout"
            except Exception as exc:  # noqa: BLE001
                degrade.append("img_diag_business_rag_failed")
                logger.warning("img_diag business rag failed: %s", exc)
                return [], [], int((perf_counter() - t0) * 1000), "failed"

        async def nl_safe() -> tuple[dict[str, Any], str]:
            try:
                st = await asyncio.wait_for(self._lane_nl2sql_until_gate(nl_req, req), timeout=lane_timeout)
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
                # strict 质量门等
                degrade.append(f"img_diag_nl2sql_blocked:{exc}")
                raise
            except Exception as exc:  # noqa: BLE001
                degrade.append("img_diag_nl2sql_failed")
                logger.exception("img_diag nl2sql lane failed")
                return {
                    "nl2sql_calls": [],
                    "gathered_data": {},
                    "plan_tasks": [],
                    "plan_context": list(),
                    "plan_rag_sources": [],
                    "quality_report": {"warnings": [str(exc)]},
                    "task_status": {},
                    "node_latency_ms": {},
                    "planner_warnings": [str(exc)],
                }, "failed"

        try:
            vision_coro = vision_safe()
            rag_coro = rag_safe()
            nl_coro = nl_safe()
            v_pack, r_pack, nl_pack = await asyncio.gather(vision_coro, rag_coro, nl_coro)

            vision_data, vision_ms, vision_status = v_pack
            biz_snippets, biz_sources, rag_ms, rag_status = r_pack
            nl_state, nl_status = nl_pack

            parallel_trace = {
                "vision_lane_ms": vision_ms,
                "vision_lane_status": vision_status,
                "business_rag_lane_ms": rag_ms,
                "business_rag_lane_status": rag_status,
                "nl2sql_lane_status": nl_status,
            }

            calls_raw = list(nl_state.get("nl2sql_calls") or [])
            calls = [AnalysisNL2SQLCall.model_validate(x) for x in calls_raw if isinstance(x, dict)]
            gathered_data = cast(dict[str, list[dict]], nl_state.get("gathered_data") or {})
            raw_tasks = list(nl_state.get("plan_tasks") or [])
            tasks = [self._plan_task_from_dict(x) for x in raw_tasks if isinstance(x, dict)]
            plan_rag_sources = list(nl_state.get("plan_rag_sources") or [])
            quality_report = cast(dict[str, Any], nl_state.get("quality_report") or {})

            merged_rag_sources = (plan_rag_sources + biz_sources)[:64]
            used_rag = len(biz_snippets) > 0 or len(plan_rag_sources) > 0

            planning_ctx_parts = list(nl_state.get("plan_context") or [])
            if self._analysis_cfg.nl2sql_llm_planner_enabled:
                ir = nl_state.get("intent_llm_result")
                if isinstance(ir, dict):
                    planning_ctx_parts.append(json.dumps(ir, ensure_ascii=False))
            planning_ctx = "\n".join(planning_ctx_parts)[:2000] if planning_ctx_parts else None

            synthesis_prompt, synthesis_version = self._resolve_stage_template(
                stage="analysis_synthesis",
                analysis_type="img_diag",
                user_id=req.user_id,
                default_text="你是电厂承压管系看图诊断助手，需融合图像证据、数据库摘要与知识库片段给出结构化结论。",
            )
            _rp, report_version = self._resolve_stage_template(
                stage="analysis_report",
                analysis_type="img_diag",
                user_id=req.user_id,
                default_text="输出章节含：可能原因与置信度、证据链（区分图像可见/库表事实/RAG）、检查建议、处置建议、免责声明。",
            )
            del _rp

            merged_blob: dict[str, Any] = {
                "structured_queries_snapshot": gathered_data,
                "vision_findings": vision_data,
                "unit_id": req.unit_id,
                "leak_location_text": req.leak_location_text,
            }

            t_syn = perf_counter()
            summary = await self._generate_summary(
                query=req.query,
                analysis_type="img_diag",
                data_mode="img_diag",
                data_blob=merged_blob,
                context_snippets=biz_snippets,
                system_prompt=synthesis_prompt,
                planning_context=planning_ctx,
            )
            syn_ms = int((perf_counter() - t_syn) * 1000)

            suggestions = self._build_suggestions(summary, "img_diag", req.options.max_suggestions)
            data_cov = {
                "mode": "img_diag",
                "planned_calls": len(tasks),
                "success_calls": sum(1 for c in calls if c.status == "success"),
                "failed_calls": sum(1 for c in calls if c.status == "failed"),
                "skipped_calls": sum(1 for c in calls if c.status == "skipped"),
                "records": self._extract_records_from_gathered(gathered_data),
                "data_quality_report": quality_report,
                "parallel_lane_trace": parallel_trace,
            }
            structured_report = self._build_structured_report(
                summary=summary,
                suggestions=suggestions,
                analysis_type="img_diag",
                report_style=req.options.report_style,
                report_template=req.options.report_template,
                chart_mode=req.options.chart_mode,
                data_coverage=data_cov,
            )
            structured_report["vision_findings"] = vision_data
            structured_report["unit_id"] = req.unit_id
            structured_report["leak_location_text"] = req.leak_location_text

            request_id = str(nl_state.get("request_id") or f"anl_{uuid4().hex[:12]}")
            plan_id = str(nl_state.get("plan_id") or f"plan_{uuid4().hex[:10]}")
            node_ms = dict(nl_state.get("node_latency_ms") or {})
            node_ms["vision_understanding_parallel"] = vision_ms
            node_ms["business_rag_parallel"] = rag_ms
            node_ms["synthesis"] = syn_ms

            node_status = dict(nl_state.get("node_status") or {})
            node_status["vision_understanding_parallel"] = vision_status
            node_status["business_rag_parallel"] = rag_status

            self._conv.append_assistant_message(req.user_id, req.session_id, summary)

            evidence = AnalysisEvidence(
                used_rag=used_rag,
                rag_sources=merged_rag_sources,
                nl2sql_calls=calls,
                data_coverage=data_cov,
                vision_findings=vision_data,
            )

            trace = AnalysisTrace(
                plan_id=plan_id,
                node_latency_ms=node_ms,
                template_versions={
                    "intent": str(nl_state.get("intent_version") or ""),
                    "data_plan": str(nl_state.get("data_plan_version") or ""),
                    "synthesis": synthesis_version,
                    "report": report_version,
                },
                execution_summary={
                    "analysis_type": "img_diag",
                    "data_mode": "img_diag",
                    "started_at": datetime.now(timezone.utc).isoformat(),
                    "used_rag": used_rag,
                    "planned_calls": len(tasks),
                    "orchestrator": "asyncio_gather_parallel",
                    "graph_nodes": [
                        "normalize_request",
                        "parallel_vision_nl2sql_rag",
                        "synthesis",
                        "finalize",
                    ],
                    "parallel_lane_trace": parallel_trace,
                    "planner_warnings": [w for w in (nl_state.get("planner_warnings") or []) if isinstance(w, str)],
                },
                node_status=node_status,
                data_plan_trace=[
                    {
                        "item_id": c.item_id,
                        "purpose": c.purpose,
                        "status": c.status,
                        "row_count": c.row_count,
                    }
                    for c in calls
                ],
                degrade_reasons=sorted(set(degrade)),
            )

            result = AnalysisV2Result(
                request_id=request_id,
                analysis_type="img_diag",
                summary=summary,
                structured_report=structured_report,
                evidence=evidence,
                trace=trace,
            )
            ANALYSIS_REQUEST_COUNT.labels(analysis_type="img_diag", data_mode="img_diag", status="success").inc()
            return result
        except Exception:
            ANALYSIS_REQUEST_COUNT.labels(analysis_type="img_diag", data_mode="img_diag", status="failed").inc()
            raise
