"""
综合分析 synthesis v2：占位模板槽位、程序表/图、多段 LLM 有限并行、按序串行流式输出。

见 docs/综合分析优化版本实现方案(v2版本).md
当前仅配置了 综合分析-超温分析 的槽位注册表，其他专项后续使用v2分支时，需要在此文件中单独增加配置（需要与 synthesis提示词模板对应）
"""

from __future__ import annotations

import asyncio
import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, AsyncIterator, Callable, Literal

from app.core.logging import get_logger
from app.llm.prompt_registry import PromptTemplateRegistry

logger = get_logger(__name__)

SlotKind = Literal["llm_narrative", "table_deterministic", "chart_structured", "static_markdown"]

STREAM_CHUNK_CHARS = 480


@dataclass(frozen=True)
class SynthesisV2Slot:
    id: str
    kind: SlotKind
    title: str
    source_item_ids: tuple[str, ...] = ()
    narrative_instruction: str = ""
    table_id: str = ""
    static_body: str = ""
    stream_live: bool = False


@dataclass
class SynthesisV2SlotOutput:
    slot_id: str
    kind: SlotKind
    title: str
    markdown: str
    table: dict[str, Any] | None = None
    chart: dict[str, Any] | None = None
    charts: list[dict[str, Any]] = field(default_factory=list)
    error: str | None = None


@dataclass
class SynthesisV2RunResult:
    summary: str
    synthesis_version: str
    synthesis_strategy_effective: str = "v2"
    sections: list[dict[str, Any]] = field(default_factory=list)
    tables: list[dict[str, Any]] = field(default_factory=list)
    charts: list[dict[str, Any]] = field(default_factory=list)
    slot_trace: list[dict[str, Any]] = field(default_factory=list)


# ---------------------------------------------------------------------------
# 槽位注册表（P0：超温）；其它专项后续扩展
# ---------------------------------------------------------------------------

def _overheat_v2_slots() -> list[SynthesisV2Slot]:
    return [
        SynthesisV2Slot(
            id="s01",
            kind="llm_narrative",
            title="一、报告基础信息",
            narrative_instruction=(
                "仅撰写「一、报告基础信息」章节：报告编号、生成时间、机组信息、监测部位、数据来源、"
                "分析主体、异常等级、超温测点数量等。使用输入数据中的真实字段，缺失项标注「待补充」。"
            ),
            stream_live=True,
        ),
        SynthesisV2Slot(
            id="s02",
            kind="llm_narrative",
            title="二、超温事件概况",
            narrative_instruction=(
                "仅撰写「二、超温事件概况」：超温起止时段、运行工况、按严重程度分类的测点汇总、"
                "温度极值、分布特征。必须引用数据摘要中的事实，禁止编造。"
            ),
        ),
        SynthesisV2Slot(
            id="s03",
            kind="table_deterministic",
            title="三、超温数据统计分析（数据表）",
            source_item_ids=("q1",),
            table_id="overheat_q1_summary",
        ),
        SynthesisV2Slot(
            id="s04",
            kind="llm_narrative",
            title="三、超温数据统计分析（分析叙述）",
            narrative_instruction=(
                "在上一节数据表之后，撰写「三、超温数据统计分析」的文字分析：多测点温度统计、"
                "关联参数联动、多测点对比。结合 q1/q2 数据摘要。"
            ),
        ),
        SynthesisV2Slot(
            id="s05",
            kind="table_deterministic",
            title="关联参数与测点明细（数据表）",
            source_item_ids=("q2",),
            table_id="overheat_q2_detail",
        ),
        SynthesisV2Slot(
            id="s06",
            kind="llm_narrative",
            title="四、超温核心原因智能诊断",
            narrative_instruction=(
                "撰写「四、超温核心原因智能诊断」，分 (一) 共性原因 与 (二) 区域专属原因，"
                "每条原因标注置信度（高/中/低）并给出数据依据。"
            ),
        ),
        SynthesisV2Slot(
            id="s07",
            kind="llm_narrative",
            title="五、超温带来的安全危害评估",
            narrative_instruction="撰写「五、超温带来的安全危害评估」，涵盖短期、中期、长期安全与经济影响。",
        ),
        SynthesisV2Slot(
            id="s08",
            kind="llm_narrative",
            title="六、智能处置调控措施",
            narrative_instruction=(
                "撰写「六、智能处置调控措施」：(一) 紧急处置 (二) 运行优化调整 "
                "(三) 检修预防措施 (四) 长效防控方案。建议须可执行。"
            ),
        ),
        SynthesisV2Slot(
            id="s09",
            kind="table_deterministic",
            title="七、历史缺陷与检修记录（数据表）",
            source_item_ids=("q3",),
            table_id="overheat_q3_defects",
        ),
        SynthesisV2Slot(
            id="s10",
            kind="llm_narrative",
            title="七、整改完成情况与效果验证",
            narrative_instruction=(
                "在数据表之后撰写「七、整改完成情况&效果验证」：已执行操作、效果验证、"
                "关联参数验证、后续跟踪。若数据不足则说明待现场补录。"
            ),
        ),
        SynthesisV2Slot(
            id="s11",
            kind="llm_narrative",
            title="八、总结结论与后续管控建议",
            narrative_instruction=(
                "撰写「八、总结结论&后续管控建议」：事件定性、重复风险等级、日常重点盯防、"
                "大模型优化建议。"
            ),
        ),
        SynthesisV2Slot(
            id="s12",
            kind="chart_structured",
            title="九、附件（趋势与分布图）",
            source_item_ids=("q1", "q2"),
            table_id="overheat_charts",
        ),
        SynthesisV2Slot(
            id="s13",
            kind="static_markdown",
            title="",
            static_body="**九、附件**\n\n以上图表与数据表为本次分析的结构化附件，可与正文对照审计。",
        ),
    ]


SYNTHESIS_V2_SLOT_REGISTRIES: dict[str, list[SynthesisV2Slot]] = {
    "overheat_guidance": _overheat_v2_slots(),
}


def synthesis_v2_registry_available(analysis_type: str) -> bool:
    return analysis_type in SYNTHESIS_V2_SLOT_REGISTRIES


def get_synthesis_v2_slots(analysis_type: str) -> list[SynthesisV2Slot]:
    return list(SYNTHESIS_V2_SLOT_REGISTRIES.get(analysis_type, ()))


# ---------------------------------------------------------------------------
# 表 / 图渲染
# ---------------------------------------------------------------------------


def _pick_columns(rows: list[dict], max_cols: int = 12) -> list[str]:
    if not rows:
        return []
    keys: list[str] = []
    seen: set[str] = set()
    for row in rows[:20]:
        if not isinstance(row, dict):
            continue
        for k in row.keys():
            if k not in seen:
                seen.add(k)
                keys.append(str(k))
            if len(keys) >= max_cols:
                break
    return keys


def _escape_md_cell(v: Any) -> str:
    if v is None:
        return ""
    s = str(v).replace("|", "\\|").replace("\n", " ")
    return s[:200] if len(s) > 200 else s


def render_markdown_table(
    rows: list[dict],
    *,
    max_rows: int,
    title: str,
) -> tuple[str, dict[str, Any]]:
    if not rows:
        body = f"### {title}\n\n（无数据）\n"
        return body, {
            "id": "",
            "title": title,
            "format": "markdown",
            "content": "（无数据）",
            "columns": [],
            "rows": [],
            "row_count": 0,
        }
    cols = _pick_columns(rows)
    if not cols:
        body = f"### {title}\n\n（无法解析列）\n"
        return body, {"title": title, "format": "markdown", "content": body, "columns": [], "rows": [], "row_count": 0}
    trimmed = rows[:max_rows]
    header = "| " + " | ".join(cols) + " |"
    sep = "| " + " | ".join(["---"] * len(cols)) + " |"
    lines = [f"### {title}", "", header, sep]
    for row in trimmed:
        if not isinstance(row, dict):
            continue
        lines.append("| " + " | ".join(_escape_md_cell(row.get(c)) for c in cols) + " |")
    if len(rows) > max_rows:
        lines.append("")
        lines.append(f"> 共 {len(rows)} 条记录，仅展示前 {max_rows} 条。")
    md = "\n".join(lines) + "\n\n"
    table_rows = [{c: row.get(c) for c in cols} for row in trimmed if isinstance(row, dict)]
    return md, {
        "id": re.sub(r"[^\w\-]", "_", title)[:64],
        "title": title,
        "format": "markdown",
        "content": md,
        "columns": cols,
        "rows": table_rows,
        "row_count": len(rows),
        "truncated": len(rows) > max_rows,
    }


def _gather_item_rows(gathered_data: dict[str, list[dict]], item_ids: tuple[str, ...]) -> list[dict]:
    out: list[dict] = []
    for iid in item_ids:
        chunk = gathered_data.get(iid) or []
        if isinstance(chunk, list):
            out.extend([r for r in chunk if isinstance(r, dict)])
    return out


def _build_overheat_charts(records: list[dict], *, chart_mode: str) -> tuple[str, list[dict[str, Any]]]:
    if chart_mode == "off" or not records:
        return "", []
    trend: list[dict[str, Any]] = []
    zone_buckets: dict[str, int] = {}
    for r in records:
        t = r.get("start_time") or r.get("time") or r.get("timestamp")
        temp = r.get("highest_temp") or r.get("temperature") or r.get("temp")
        if t is not None and temp is not None:
            try:
                trend.append({"time": str(t), "temperature": float(temp)})
            except (TypeError, ValueError):
                pass
        zone = (
            str(r.get("device_name") or r.get("监测部位") or r.get("boiler_name") or "unknown")[:64]
        )
        zone_buckets[zone] = zone_buckets.get(zone, 0) + 1
    charts: list[dict[str, Any]] = []
    md_parts: list[str] = []
    if trend:
        spec = {
            "id": "overheat_temp_trend",
            "chart_type": "line",
            "title": "超温温度趋势",
            "spec": {
                "x_field": "time",
                "y_field": "temperature",
                "series_name": "highest_temp",
                "data": trend[:500],
            },
        }
        charts.append(spec)
        md_parts.append(f"- 趋势图：`{spec['title']}`（{len(trend)} 点）")
    if zone_buckets:
        bar_data = [{"zone": k, "count": v} for k, v in sorted(zone_buckets.items(), key=lambda x: -x[1])[:20]]
        spec = {
            "id": "overheat_zone_bar",
            "chart_type": "bar",
            "title": "区域超温次数",
            "spec": {
                "x_field": "zone",
                "y_field": "count",
                "series_name": "overheat_events",
                "data": bar_data,
            },
        }
        charts.append(spec)
        md_parts.append(f"- 分布图：`{spec['title']}`")
    md = ""
    if md_parts:
        md = "\n".join(md_parts) + "\n\n"
    return md, charts


# ---------------------------------------------------------------------------
# v2 引擎
# ---------------------------------------------------------------------------


class AnalysisSynthesisV2Engine:
    """多槽位 synthesis；生成并行、推送串行。"""

    def __init__(
        self,
        *,
        llm_client: Any,
        prompts: PromptTemplateRegistry,
        gathered_json_max_chars: int,
        segment_max_tokens: int,
        max_parallel_llm: int,
        table_max_rows: int,
        synthesis_timeout_seconds: float,
        emit_structured_sse: bool = True,
        json_fallback: Callable[[Any], Any] | None = None,
    ) -> None:
        self._llm = llm_client
        self._prompts = prompts
        self._gathered_json_max_chars = max(1000, gathered_json_max_chars)
        self._segment_max_tokens = max(256, segment_max_tokens)
        self._max_parallel_llm = max(1, max_parallel_llm)
        self._table_max_rows = max(1, table_max_rows)
        self._synthesis_timeout = synthesis_timeout_seconds
        self._emit_structured_sse = emit_structured_sse
        self._json_fallback = json_fallback or (lambda o: str(o))

    def _narrative_system_prompt(self, analysis_type: str) -> str:
        for scene in (
            f"analysis_synthesis_{analysis_type}_narrative",
            "analysis_synthesis_overheat_narrative",
            "analysis_synthesis",
        ):
            tpl = self._prompts.get_template(scene=scene, version="v1")
            if tpl and tpl.content.strip():
                return tpl.content.strip()
        return (
            "你是电站锅炉防磨防爆与超温分析专家。请严格按章节指令撰写，使用专业术语，"
            "禁止编造数值；仅输出所要求章节正文（Markdown），不要输出其它章节。"
        )

    def _build_segment_user_content(
        self,
        *,
        query: str,
        analysis_type: str,
        data_mode: str,
        gathered_data: dict[str, list[dict]],
        context_snippets: list[str],
        planning_context: str | None,
        slot: SynthesisV2Slot,
        item_ids: tuple[str, ...],
    ) -> str:
        subset: dict[str, list[dict]] = {}
        for iid in item_ids or gathered_data.keys():
            if iid in gathered_data:
                subset[iid] = gathered_data[iid]
        if not subset and gathered_data:
            subset = gathered_data
        data_preview = json.dumps(subset, ensure_ascii=False, default=self._json_fallback)[
            : self._gathered_json_max_chars
        ]
        rag_text = "\n".join(f"- {s}" for s in context_snippets[:8])
        pc = (planning_context or "").strip()
        planning_block = f"\n分阶段规划意图(结构化要点):\n{pc[:2000]}\n" if pc else ""
        return (
            f"分析类型: {analysis_type}\n"
            f"数据来源模式: {data_mode}\n"
            f"用户问题: {query}\n"
            f"{planning_block}"
            f"数据摘要(JSON截断): {data_preview}\n"
            f"RAG参考片段:\n{rag_text}\n\n"
            f"【本章写作任务】\n{slot.narrative_instruction}\n"
        ).strip()

    async def _render_llm_slot(
        self,
        *,
        query: str,
        analysis_type: str,
        data_mode: str,
        gathered_data: dict[str, list[dict]],
        context_snippets: list[str],
        planning_context: str | None,
        slot: SynthesisV2Slot,
    ) -> str:
        system_prompt = self._narrative_system_prompt(analysis_type)
        user_content = self._build_segment_user_content(
            query=query,
            analysis_type=analysis_type,
            data_mode=data_mode,
            gathered_data=gathered_data,
            context_snippets=context_snippets,
            planning_context=planning_context,
            slot=slot,
            item_ids=slot.source_item_ids,
        )
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_content},
        ]
        text = await self._llm.chat(
            model=None,
            messages=messages,
            timeout=self._synthesis_timeout,
            max_tokens=self._segment_max_tokens,
        )
        return (text or "").strip()

    async def _render_slot(
        self,
        *,
        query: str,
        analysis_type: str,
        data_mode: str,
        gathered_data: dict[str, list[dict]],
        context_snippets: list[str],
        planning_context: str | None,
        slot: SynthesisV2Slot,
        chart_mode: str,
    ) -> SynthesisV2SlotOutput:
        title = slot.title.strip()
        try:
            if slot.kind == "static_markdown":
                md = slot.static_body
                return SynthesisV2SlotOutput(slot.id, slot.kind, title, md)

            if slot.kind == "table_deterministic":
                rows = _gather_item_rows(gathered_data, slot.source_item_ids)
                md, tbl = render_markdown_table(
                    rows, max_rows=self._table_max_rows, title=title or slot.table_id
                )
                tbl["id"] = slot.table_id or tbl.get("id", slot.id)
                tbl["source_item_ids"] = list(slot.source_item_ids)
                return SynthesisV2SlotOutput(slot.id, slot.kind, title, md, table=tbl)

            if slot.kind == "chart_structured":
                rows = _gather_item_rows(gathered_data, slot.source_item_ids)
                md, charts = _build_overheat_charts(rows, chart_mode=chart_mode)
                md_block = f"### {title}\n\n{md}" if title else md
                return SynthesisV2SlotOutput(
                    slot.id,
                    slot.kind,
                    title,
                    md_block,
                    chart=charts[0] if charts else None,
                    charts=charts,
                    table=None,
                )

            if slot.kind == "llm_narrative":
                md = await self._render_llm_slot(
                    query=query,
                    analysis_type=analysis_type,
                    data_mode=data_mode,
                    gathered_data=gathered_data,
                    context_snippets=context_snippets,
                    planning_context=planning_context,
                    slot=slot,
                )
                if title:
                    md = f"### {title}\n\n{md}\n\n"
                return SynthesisV2SlotOutput(slot.id, slot.kind, title, md)

            return SynthesisV2SlotOutput(slot.id, slot.kind, title, "", error=f"unknown_kind:{slot.kind}")
        except Exception as exc:  # noqa: BLE001
            logger.exception("synthesis v2 slot failed slot_id=%s", slot.id)
            err_md = f"### {title}\n\n（本章生成失败：{exc}）\n\n"
            return SynthesisV2SlotOutput(slot.id, slot.kind, title, err_md, error=str(exc))

    async def _fill_all_slots_parallel(
        self,
        *,
        slots: list[SynthesisV2Slot],
        query: str,
        analysis_type: str,
        data_mode: str,
        gathered_data: dict[str, list[dict]],
        context_snippets: list[str],
        planning_context: str | None,
        chart_mode: str,
    ) -> list[SynthesisV2SlotOutput]:
        sem = asyncio.Semaphore(self._max_parallel_llm)

        async def _one(slot: SynthesisV2Slot) -> SynthesisV2SlotOutput:
            if slot.kind == "llm_narrative":
                async with sem:
                    return await self._render_slot(
                        query=query,
                        analysis_type=analysis_type,
                        data_mode=data_mode,
                        gathered_data=gathered_data,
                        context_snippets=context_snippets,
                        planning_context=planning_context,
                        slot=slot,
                        chart_mode=chart_mode,
                    )
            return await self._render_slot(
                query=query,
                analysis_type=analysis_type,
                data_mode=data_mode,
                gathered_data=gathered_data,
                context_snippets=context_snippets,
                planning_context=planning_context,
                slot=slot,
                chart_mode=chart_mode,
            )

        return list(await asyncio.gather(*[_one(s) for s in slots]))

    @staticmethod
    def _assemble_result(
        outputs: list[SynthesisV2SlotOutput],
        *,
        analysis_type: str,
    ) -> SynthesisV2RunResult:
        parts: list[str] = []
        sections: list[dict[str, Any]] = []
        tables: list[dict[str, Any]] = []
        charts: list[dict[str, Any]] = []
        trace: list[dict[str, Any]] = []
        for out in outputs:
            parts.append(out.markdown)
            if out.title and out.markdown.strip():
                sections.append({"title": out.title, "content": out.markdown.strip(), "slot_id": out.slot_id})
            if out.table:
                tables.append(out.table)
            if out.charts:
                charts.extend(out.charts)
            elif out.chart:
                charts.append(out.chart)
            trace.append(
                {
                    "slot_id": out.slot_id,
                    "kind": out.kind,
                    "title": out.title,
                    "chars": len(out.markdown),
                    "error": out.error,
                }
            )
        summary = "".join(parts)
        version = f"analysis_synthesis_{analysis_type}:v2_multi_slot"
        return SynthesisV2RunResult(
            summary=summary,
            synthesis_version=version,
            synthesis_strategy_effective="v2",
            sections=sections,
            tables=tables,
            charts=charts,
            slot_trace=trace,
        )

    async def run_sync(
        self,
        *,
        analysis_type: str,
        query: str,
        data_mode: str,
        gathered_data: dict[str, list[dict]],
        context_snippets: list[str],
        planning_context: str | None,
        chart_mode: str,
    ) -> SynthesisV2RunResult:
        slots = get_synthesis_v2_slots(analysis_type)
        outputs = await self._fill_all_slots_parallel(
            slots=slots,
            query=query,
            analysis_type=analysis_type,
            data_mode=data_mode,
            gathered_data=gathered_data,
            context_snippets=context_snippets,
            planning_context=planning_context,
            chart_mode=chart_mode,
        )
        return self._assemble_result(outputs, analysis_type=analysis_type)

    async def iter_stream_events(
        self,
        *,
        analysis_type: str,
        query: str,
        data_mode: str,
        gathered_data: dict[str, list[dict]],
        context_snippets: list[str],
        planning_context: str | None,
        chart_mode: str,
    ) -> AsyncIterator[dict[str, Any]]:
        """
        后台并行生成各槽位，按槽位顺序分块推送 summary_delta；
        表/图可额外推送 table_payload / chart_payload。
        """
        slots = get_synthesis_v2_slots(analysis_type)
        outputs = await self._fill_all_slots_parallel(
            slots=slots,
            query=query,
            analysis_type=analysis_type,
            data_mode=data_mode,
            gathered_data=gathered_data,
            context_snippets=context_snippets,
            planning_context=planning_context,
            chart_mode=chart_mode,
        )
        for out in outputs:
            text = out.markdown
            for i in range(0, len(text), STREAM_CHUNK_CHARS):
                yield {"event": "summary_delta", "text": text[i : i + STREAM_CHUNK_CHARS]}
            if self._emit_structured_sse and out.table:
                yield {
                    "event": "table_payload",
                    "slot_id": out.slot_id,
                    "table": out.table,
                }
            if self._emit_structured_sse:
                for ch in out.charts or ([out.chart] if out.chart else []):
                    yield {
                        "event": "chart_payload",
                        "slot_id": out.slot_id,
                        "chart": ch,
                    }

    async def iter_stream_events_live_first(
        self,
        *,
        analysis_type: str,
        query: str,
        data_mode: str,
        gathered_data: dict[str, list[dict]],
        context_snippets: list[str],
        planning_context: str | None,
        chart_mode: str,
    ) -> AsyncIterator[tuple[dict[str, Any], SynthesisV2RunResult | None]]:
        """
        首槽 LLM 真流式，其余槽并行预生成后按序推送；最终返回完整 SynthesisV2RunResult。
        Yields (event_dict, None) ；最后一次 yield (_, result)。
        """
        slots = get_synthesis_v2_slots(analysis_type)
        if not slots:
            result = SynthesisV2RunResult(summary="", synthesis_version="v2:empty")
            yield ({"event": "summary_delta", "text": ""}, result)
            return

        live_idx = next((i for i, s in enumerate(slots) if s.stream_live and s.kind == "llm_narrative"), 0)
        outputs: list[SynthesisV2SlotOutput | None] = [None] * len(slots)
        sem = asyncio.Semaphore(self._max_parallel_llm)

        async def _fill_index(i: int) -> None:
            slot = slots[i]
            if slot.kind == "llm_narrative":
                async with sem:
                    outputs[i] = await self._render_slot(
                        query=query,
                        analysis_type=analysis_type,
                        data_mode=data_mode,
                        gathered_data=gathered_data,
                        context_snippets=context_snippets,
                        planning_context=planning_context,
                        slot=slot,
                        chart_mode=chart_mode,
                    )
            else:
                outputs[i] = await self._render_slot(
                    query=query,
                    analysis_type=analysis_type,
                    data_mode=data_mode,
                    gathered_data=gathered_data,
                    context_snippets=context_snippets,
                    planning_context=planning_context,
                    slot=slot,
                    chart_mode=chart_mode,
                )

        bg_tasks = [asyncio.create_task(_fill_index(i)) for i in range(len(slots)) if i != live_idx]

        live_slot = slots[live_idx]
        live_parts: list[str] = []
        system_prompt = self._narrative_system_prompt(analysis_type)
        user_content = self._build_segment_user_content(
            query=query,
            analysis_type=analysis_type,
            data_mode=data_mode,
            gathered_data=gathered_data,
            context_snippets=context_snippets,
            planning_context=planning_context,
            slot=live_slot,
            item_ids=live_slot.source_item_ids,
        )
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_content},
        ]
        if live_slot.title:
            header = f"### {live_slot.title}\n\n"
            live_parts.append(header)
            yield ({"event": "summary_delta", "text": header}, None)

        async for chunk in self._llm.stream_chat(
            model=None,
            messages=messages,
            timeout=float(self._synthesis_timeout),
            max_tokens=self._segment_max_tokens,
        ):
            live_parts.append(chunk)
            yield ({"event": "summary_delta", "text": chunk}, None)
        live_parts.append("\n\n")
        yield ({"event": "summary_delta", "text": "\n\n"}, None)
        outputs[live_idx] = SynthesisV2SlotOutput(
            live_slot.id,
            live_slot.kind,
            live_slot.title,
            "".join(live_parts),
        )

        if bg_tasks:
            await asyncio.gather(*bg_tasks)

        for i, out in enumerate(outputs):
            if i == live_idx or out is None:
                continue
            text = out.markdown
            for j in range(0, len(text), STREAM_CHUNK_CHARS):
                yield ({"event": "summary_delta", "text": text[j : j + STREAM_CHUNK_CHARS]}, None)
            if self._emit_structured_sse and out.table:
                yield ({"event": "table_payload", "slot_id": out.slot_id, "table": out.table}, None)
            if self._emit_structured_sse:
                for ch in out.charts or ([out.chart] if out.chart else []):
                    yield ({"event": "chart_payload", "slot_id": out.slot_id, "chart": ch}, None)

        filled = [o for o in outputs if o is not None]
        result = self._assemble_result(filled, analysis_type=analysis_type)
        yield ({}, result)
