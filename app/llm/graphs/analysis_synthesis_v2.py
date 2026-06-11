"""
综合分析 synthesis v2：占位模板槽位、程序表/图、多段 LLM 有限并行、按序串行流式输出。

超温专项（20260602 四段式模板）：q1～q7 + 12 槽位，见 overheat_synthesis_render.py
"""

from __future__ import annotations

import asyncio
import json
import re
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, AsyncIterator, Callable, Literal

from app.core.logging import get_logger
from app.llm.graphs.overheat_synthesis_render import (
    OVERHEAT_CH1_INTRO,
    OVERHEAT_DOCX_AUTHORING_RULES,
    build_overheat_distribution_note,
    build_overheat_region_fact_packages,
    filter_overheat_synthesis_slots,
    format_overheat_entity_label,
    overheat_data_source_label,
    render_overheat_daily_section,
    render_overheat_weekly_section,
)
from app.llm.prompt_registry import PromptTemplateRegistry

logger = get_logger(__name__)

SlotKind = Literal[
    "llm_narrative",
    "table_deterministic",
    "chart_structured",
    "static_markdown",
    "template_deterministic",
]

# 历史默认（非 live_first 回退路径仍可由引擎 stream_chunk_chars 覆盖）
STREAM_CHUNK_CHARS = 480

_LLM_STREAM_END = object()


@dataclass(frozen=True)
class SynthesisV2Slot:
    id: str
    kind: SlotKind
    title: str
    source_item_ids: tuple[str, ...] = ()
    narrative_instruction: str = ""
    table_id: str = ""
    template_id: str = ""
    static_body: str = ""
    stream_live: bool = False
    boiler_name: str = ""


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

def _data_source_label(item_id: str) -> str:
    return overheat_data_source_label(item_id)


_OVERHEAT_INSTRUCTION_LINE_PATTERNS = (
    r"没有询问具体锅炉时，需要输出所有锅炉内容。?",
    r"异常等级分为[：:].*",
    r"该章节数据和报告要求[：:]?",
    r"Query\s*中如果是问.*",
    r"Query\s*中如果是指定了机组.*",
    r"（查询\s*sql\s*首先按照机组进行排序.*）",
    r"对具体锅炉的每条超温数据进行详细分析.*",
    r"2、若查询为周超温，则需要分析日超温趋势.*",
    r"关联本次出现的超温区域，根据知识片段出具针对性措施[，,]?以下内容为示例[：:]?",
    r"以下内容为示例[：:]?",
    r"关联本次超温出现的问题，拉取知识片段并推理评估",
    r"^负荷分析[：:].*",
    r"^主汽压力分析[：:].*",
    r"^炉膛负压分析[：:].*",
    r"^氧量分析[：:].*",
    r"^超温分布特征[：:].*",
    r"^日超温趋势分析[：:].*",
    r"^风险量化与后续建议[：:].*",
)


def _sanitize_report_narrative(text: str) -> str:
    """移除正文中不应出现的置信度/依据/q 编号及 docx 红色说明文字。"""
    if not (text or "").strip():
        return text or ""
    out = text
    # q 编号（含 q1、q2a 等）
    out = re.sub(r"\bq[0-9][a-z]?\b", "", out, flags=re.IGNORECASE)
    out = re.sub(r"\[q[0-9][a-z]?\]", "", out, flags=re.IGNORECASE)
    # 置信度 / 依据 常见写法
    out = re.sub(r"[\[（(]?置信度[：:\s]*[高中低][）)\]]?", "", out)
    out = re.sub(r"[（(]依据[：:][^）)\n]+[）)]", "", out)
    out = re.sub(r"依据[：:][^\n。；;]+", "", out)
    out = re.sub(r"数据依据[：:][^\n]+", "", out)
    for pat in _OVERHEAT_INSTRUCTION_LINE_PATTERNS:
        out = re.sub(rf"(?m)^\s*{pat}\s*$", "", out)
    out = re.sub(r"(?m)^\s*#+\s*超温原因剖析\s*$", "", out)
    out = re.sub(r"(?m)^\s*超温原因剖析\s*$", "", out)
    out = re.sub(r"(?m)^\s*#+\s*超温风险评估\s*$", "", out)
    out = re.sub(r"(?m)^\s*超温风险评估\s*$", "", out)
    out = re.sub(r"(?m)^\s*#+\s*紧急处置\s*$", "", out)
    out = re.sub(r"(?m)^\s*紧急处置\s*$", "", out)
    out = re.sub(r"(?m)^\s*#+\s*紧急处置措施\s*$", "", out)
    out = re.sub(r"(?m)^\s*紧急处置措施\s*$", "", out)
    out = re.sub(r"(?m)^\s*#+\s*运行优化调整\s*$", "", out)
    out = re.sub(r"(?m)^\s*运行优化调整\s*$", "", out)
    out = re.sub(r"(?m)^\s*#+\s*后续检修预防措施\s*$", "", out)
    out = re.sub(r"(?m)^\s*后续检修预防措施\s*$", "", out)
    out = re.sub(r"(?m)^\s*#+\s*计划长效防控方案\s*$", "", out)
    out = re.sub(r"(?m)^\s*计划长效防控方案\s*$", "", out)
    out = re.sub(r"(?m)^\s*【内部分析参考·禁止写入报告】.*$", "", out)
    # 多余空行
    out = re.sub(r"\n{3,}", "\n\n", out)
    return out.strip()


def _overheat_v2_slots() -> list[SynthesisV2Slot]:
    """与 analysis_plan_overheat_guidance v2（20260602 四段式，q1～q7）及 docx 模板章节一一映射。"""
    return [
        SynthesisV2Slot(
            id="s00_title",
            kind="static_markdown",
            title="",
            static_body="# 锅炉管壁超温智能分析报告\n\n",
        ),
        SynthesisV2Slot(
            id="s01_ch1_intro",
            kind="static_markdown",
            title="",
            static_body=OVERHEAT_CH1_INTRO,
        ),
        # 去掉 “--按日超温分析--“静态槽位
        # SynthesisV2Slot(
        #     id="s02_daily_marker",
        #     kind="static_markdown",
        #     title="",
        #     static_body="--按日超温分析--\n\n",
        # ),
        SynthesisV2Slot(
            id="s03_daily_section",
            kind="template_deterministic",
            title="",
            source_item_ids=("q1",),
            template_id="overheat_daily_section",
        ),
        # 去掉 “--按周超温分析--“静态槽位
        # SynthesisV2Slot(
        #     id="s02_weekly_marker",
        #     kind="static_markdown",
        #     title="",
        #     static_body="--按周超温分析--\n\n",
        # ),
        SynthesisV2Slot(
            id="s04_weekly_section",
            kind="template_deterministic",
            title="",
            source_item_ids=("q1", "q2", "q3"),
            template_id="overheat_weekly_section",
        ),
        SynthesisV2Slot(
            id="s05_ch2_hdr",
            kind="static_markdown",
            title="",
            static_body="\n\n## 二、超温原因剖析\n\n",
        ),
        SynthesisV2Slot(
            id="s06_cause",
            kind="llm_narrative",
            title="",
            source_item_ids=("q1", "q4", "q5", "q6", "q7"),
            narrative_instruction="（运行时按机组展开为多槽；见 overheat_cause_narrative_instruction）",
            stream_live=True,
        ),
        SynthesisV2Slot(
            id="s07_ch3_hdr",
            kind="static_markdown",
            title="",
            static_body="\n\n## 三、超温风险评估\n\n",
        ),
        SynthesisV2Slot(
            id="s08_risk",
            kind="llm_narrative",
            title="",
            source_item_ids=("q1", "q2"),
            narrative_instruction=(
                "撰写本章正文（禁止输出「超温风险评估」及任何 # / ## / ### 标题行；章节标题已由系统输出）。"
                "禁止输出 docx 红色说明句「关联本次超温出现的问题，拉取知识片段并推理评估」。"
                "该章节需包含两个三级(###)小节标题：风险量化、风险评估"
                "风险量化 小节中按区域进行四级(####)小节列举测点/极值/等级/时长等量化数据"
                "风险评估 小节中需结合上述量化数据(不要再次重复输出量化数据，只基于量化数据进行分析)，再衔接管材蠕变、氧化皮、金相劣化、爆管泄漏等风险机理"
                "须结合 q1/q2 极值与 RAG 知识，说明诱因、最大瞬时超温值、连续超温时长是否超标、异常等级（尤其Ⅲ/Ⅳ级）。"
                "列举测点极值/等级/时长时须点明对应区域或测点的规格材质（来自 q1）；"
                "衔接 RAG 时按材质差异化表述风险（如高合金钢氧化皮、低合金钢蠕变等），"
                "须与本次超温差值、连续时长、异常等级同句挂钩；无材质写「规格材质待补充」。"
                "禁止处置建议、运行优化、检修措施、后续建议、额外章节。"
                "无数据写待补充；禁止置信度、依据、q 编号。"
                "禁止「可能、或许、大概、疑似、或与…有关、一般、通常」等无数据支撑的泛化表述；"
                "每条风险陈述须引用具体测点展示名（测点名称（测点编号））、壁温、差值、时长或等级，"
                "禁止正文单独以测点编号作主称谓；数据不足写「待补充」。"
            ),
        ),
        SynthesisV2Slot(
            id="s09_ch4_hdr",
            kind="static_markdown",
            title="",
            static_body="\n\n## 四、总结和推荐措施\n\n",
        ),
        SynthesisV2Slot(
            id="s10a_emergency_hdr",
            kind="static_markdown",
            title="",
            static_body="### 紧急处置措施\n\n",
        ),
        SynthesisV2Slot(
            id="s10a_emergency",
            kind="llm_narrative",
            title="",
            source_item_ids=("q1", "q2", "q4", "q5"),
            narrative_instruction=(
                "撰写「紧急处置措施」正文（禁止输出「紧急处置」或 「紧急处置措施」 标题行，标题已由系统输出。直接按条目输出处置措施）。"
                "禁止输出 docx 示例措施全文。须基于本次超温区域与严重测点自行撰写，"
                "可含：整体调控、差异化降温、紧急调整燃烧工况等要点（plain 叙述或短列表）。"
                "对Ⅲ/Ⅳ级且已知规格材质的测点/区域，处置须考虑材质耐温极限（如加强监视、限制升负荷）；无材质不写具体钢号。"
                "禁止置信度、依据、额外章节标题。"
                "措施须对应 q1/q2 中具体区域、测点展示名（测点名称（测点编号））、等级与极值，"
                "禁止正文单独以测点编号作主称谓；禁止「可能/或许/大概/一般/通常」等泛化表述；"
                "无数据支撑的操作写「待补充」。"
            ),
        ),
        SynthesisV2Slot(
            id="s10b_optimize_hdr",
            kind="static_markdown",
            title="",
            static_body="\n\n### 运行优化调整\n\n",
        ),
        SynthesisV2Slot(
            id="s10b_optimize",
            kind="llm_narrative",
            title="",
            source_item_ids=("q1", "q2", "q4", "q5"),
            narrative_instruction=(
                "撰写「运行优化调整」正文（禁止输出「运行优化调整」标题行，标题已由系统输出）。"
                "可含共性优化、专项优化等要点；须结合本次超温区域。"
                "优化建议须对应区域规格材质特性（如高合金氧化皮风险区避免频繁启停变温）。"
                "具体 优化调整建议 条目，按照第四级标题结构输出（只用 ####，禁止 ###）"
                "禁止置信度、依据、额外章节。"
                "须点名区域/测点展示名（测点名称（测点编号））/参数依据，"
                "禁止正文单独以测点编号或测点编码作主称谓；"
                "禁止「可能/或许/大概/一般/通常」等泛化表述；无依据写「待补充」。"
            ),
        ),
        SynthesisV2Slot(
            id="s10c_maintenance_hdr",
            kind="static_markdown",
            title="",
            static_body="\n\n### 后续检修预防措施\n\n",
        ),
        SynthesisV2Slot(
            id="s10c_maintenance",
            kind="llm_narrative",
            title="",
            source_item_ids=("q1", "q2", "q4", "q5"),
            narrative_instruction=(
                "撰写「后续检修预防措施」正文（禁止输出「后续检修预防措施」标题行，标题已由系统输出）。"
                "可含重点检修、全面排查、测点校验等要点。"
                "须按区域规格材质写针对性检修（氧化皮检测、蠕变评估、换管材质升级等），"
                "须引用 q1 区域名称、规格材质与等级；无材质写「规格材质待补充」。"
                "具体的 预防措施 条目，按照第四级标题结构输出（只用 ####，禁止 ###）"
                "禁止置信度、依据、额外章节。"
                "须对应严重超温区域/测点展示名（测点名称（测点编号））/等级，"
                "禁止正文单独以测点编号作主称谓；"
                "禁止「可能/或许/大概/一般/通常」等泛化表述；无依据写「待补充」。"
            ),
        ),
        SynthesisV2Slot(
            id="s10d_longterm_hdr",
            kind="static_markdown",
            title="",
            static_body="\n\n### 计划长效防控方案\n\n",
        ),
        SynthesisV2Slot(
            id="s10d_longterm",
            kind="llm_narrative",
            title="",
            source_item_ids=("q1", "q2", "q4", "q5"),
            narrative_instruction=(
                "撰写「计划长效防控方案」正文（禁止输出「计划长效防控方案」标题行，标题已由系统输出）。"
                "可含优化措施、分区管控、制度完善等要点。"
                "具体的 计划长效防控方案 条目，按照第四级标题结构输出（只用 ####，禁止 ###）"
                "禁止置信度、依据、额外总结章节。"
                "须结合本次超温分布与重复发生区域（引用区域名称与测点展示名），"
                "禁止正文单独以测点编号作主称谓；"
                "禁止「可能/或许/大概/一般/通常」等泛化表述；无依据写「待补充」。"
            ),
        ),
    ]


SYNTHESIS_V2_SLOT_REGISTRIES: dict[str, list[SynthesisV2Slot]] = {
    "overheat_guidance": _overheat_v2_slots(),
}


def synthesis_v2_registry_available(analysis_type: str) -> bool:
    return analysis_type in SYNTHESIS_V2_SLOT_REGISTRIES


def get_synthesis_v2_slots(analysis_type: str) -> list[SynthesisV2Slot]:
    return list(SYNTHESIS_V2_SLOT_REGISTRIES.get(analysis_type, ()))


def get_effective_synthesis_v2_slots(
    analysis_type: str,
    *,
    report_context: dict[str, Any] | None = None,
    gathered_data: dict[str, list[dict]] | None = None,
) -> list[SynthesisV2Slot]:
    slots = get_synthesis_v2_slots(analysis_type)
    if analysis_type == "overheat_guidance":
        return filter_overheat_synthesis_slots(slots, report_context, gathered_data)
    return slots


def _resolve_live_slot_index(slots: list[SynthesisV2Slot]) -> int | None:
    """首槽 LLM 流式索引：显式 stream_live 优先，否则首个 llm_narrative；无则 None。"""
    for i, slot in enumerate(slots):
        if slot.stream_live and slot.kind == "llm_narrative":
            return i
    for i, slot in enumerate(slots):
        if slot.kind == "llm_narrative":
            return i
    return None


def _wrap_template_markdown(title: str, body: str) -> str:
    cleaned = (body or "").strip()
    if not title:
        return cleaned + ("\n" if cleaned else "")
    if not cleaned or cleaned == "（待补充）":
        return f"### {title}\n\n（待补充）\n\n"
    return f"### {title}\n\n{cleaned}\n\n"


def _join_slot_markdown(parts: list[str]) -> str:
    """拼接各槽位 Markdown，保证槽位之间有空行（避免流式拼接后标题/表格粘连）。"""
    blocks: list[str] = []
    for piece in parts:
        text = (piece or "").strip()
        if text:
            blocks.append(text)
    if not blocks:
        return ""
    return "\n\n".join(blocks) + "\n"


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


def _table_heading_markdown(title: str, *, subsection: bool = False) -> str:
    t = (title or "").strip()
    if not t:
        return ""
    prefix = "####" if subsection or re.match(r"^\d+\.\d+\s", t) else "###"
    return f"{prefix} {t}"


def _table_empty_message(
    table_id: str,
    source_item_ids: tuple[str, ...],
    *,
    task_status: dict[str, str] | None,
    gathered_data: dict[str, list[dict]] | None,
) -> str:
    status = task_status or {}
    data = gathered_data or {}
    for iid in source_item_ids:
        st = status.get(iid, "")
        if st in ("mandatory_failed", "optional_failed"):
            return f"（{_data_source_label(iid)} 查询失败，非无数据）"
    if table_id == "overheat_q5_defects" and source_item_ids == ("q5a",):
        if status.get("q5a") == "success" and not (data.get("q5a") or []):
            return "（近一年无遗留问题/泄漏/换管记录；查询条件：机组与时间窗见正文）"
    if table_id == "overheat_q6_history" and source_item_ids == ("q6c",):
        if status.get("q6c") == "success" and not (data.get("q6c") or []):
            return "（无历史同类对标记录）"
    return "（无数据）"


def render_markdown_table(
    rows: list[dict],
    *,
    max_rows: int,
    title: str,
    empty_message: str | None = None,
    subsection: bool = False,
) -> tuple[str, dict[str, Any]]:
    heading = _table_heading_markdown(title, subsection=subsection)
    if not rows:
        msg = empty_message or "（无数据）"
        body = f"{heading}\n\n{msg}\n" if heading else f"{msg}\n"
        return body, {
            "id": "",
            "title": title,
            "format": "markdown",
            "content": msg,
            "columns": [],
            "rows": [],
            "row_count": 0,
        }
    cols = _pick_columns(rows)
    if not cols:
        body = f"{heading}\n\n（无法解析列）\n" if heading else "（无法解析列）\n"
        return body, {"title": title, "format": "markdown", "content": body, "columns": [], "rows": [], "row_count": 0}
    trimmed = rows[:max_rows]
    header = "| " + " | ".join(cols) + " |"
    sep = "| " + " | ".join(["---"] * len(cols)) + " |"
    lines = [heading, "", header, sep] if heading else [header, sep]
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


def _filter_rows_by_boiler(rows: list[dict], boiler_name: str) -> list[dict]:
    if not boiler_name:
        return rows
    out: list[dict] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        if str(row.get("机组名称") or "").strip() == boiler_name:
            out.append(row)
    return out


def _resolve_data_subset(
    gathered_data: dict[str, list[dict]],
    item_ids: tuple[str, ...],
    *,
    strict: bool,
    boiler_name: str = "",
) -> dict[str, list[dict]]:
    """按槽位绑定查询项切片；strict 时不再回退为全量 gathered_data。"""
    if not item_ids:
        subset = dict(gathered_data) if gathered_data else {}
    else:
        subset = {}
        for iid in item_ids:
            chunk = gathered_data.get(iid)
            if isinstance(chunk, list):
                subset[iid] = [r for r in chunk if isinstance(r, dict)]
            else:
                subset[iid] = []
        if not subset and not strict:
            subset = dict(gathered_data)
    if boiler_name:
        for iid in list(subset.keys()):
            if iid == "q5":
                continue
            subset[iid] = _filter_rows_by_boiler(subset.get(iid) or [], boiler_name)
    return subset


_CHAPTER_PREFIX_RE = re.compile(r"^[一二三四五六七八九十百]+、")

# 常见列 → 单位提示，降低 MPa/MW 等误读
_FIELD_UNIT_HINTS: dict[str, str] = {
    "highest_temp": "一般为 ℃，勿写作 MPa/MW",
    "limit_temp": "一般为 ℃",
    "temperature": "一般为 ℃",
    "temp": "一般为 ℃",
    "steam_pressure_value": "按数据原值书写，勿猜测单位",
    "main_steam_pressure": "按数据原值书写，勿猜测单位",
    "load_mw": "一般为 MW，勿与 MPa 混淆",
    "power_mw": "一般为 MW",
}


def _normalize_heading_text(text: str) -> str:
    s = re.sub(r"[、：:（）()\s]", "", (text or "").strip())
    return s.casefold()


def strip_leading_duplicate_heading(md: str, slot_title: str) -> str:
    """去掉模型正文开头与 slot 标题重复的 Markdown 标题行。"""
    if not (md or "").strip():
        return ""
    lines = md.split("\n")
    idx = 0
    skipped = 0
    target = _normalize_heading_text(slot_title) if slot_title else ""
    chapter_prefix = _CHAPTER_PREFIX_RE.match(slot_title).group(0) if slot_title and _CHAPTER_PREFIX_RE.match(slot_title) else ""
    while idx < len(lines) and skipped < 6:
        line = lines[idx].strip()
        if not line:
            idx += 1
            skipped += 1
            continue
        m = re.match(r"^\s{0,3}(#{1,6})\s+(.+)$", line)
        if m:
            heading = _normalize_heading_text(m.group(2))
            if target and (target in heading or heading in target):
                idx += 1
                skipped += 1
                continue
            if chapter_prefix and chapter_prefix in m.group(2):
                idx += 1
                skipped += 1
                continue
            if not slot_title:
                idx += 1
                skipped += 1
                continue
        break
    return "\n".join(lines[idx:]).strip()


def _normalize_overheat_level(raw: Any) -> str:
    s = str(raw or "").strip()
    if not s or s == "正常":
        return ""
    if "严重" in s:
        return "严重超温"
    if "中度" in s:
        return "中度超温"
    if "轻微" in s:
        return "轻微超温"
    return s


def _aggregate_q2_severity_table_rows(rows: list[dict]) -> list[dict]:
    """将 q2 测点明细聚合为 docx 第二章第 3 项：按超温等级的测点及位置列表与数量。"""
    order = {"严重超温": 1, "中度超温": 2, "轻微超温": 3}
    buckets: dict[str, list[str]] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        level = _normalize_overheat_level(row.get("超温等级") or row.get("overheat_severity"))
        if not level:
            continue
        loc = str(row.get("测点及位置") or row.get("测点编号") or "").strip()
        if not loc:
            continue
        buckets.setdefault(level, []).append(loc)
    out: list[dict] = []
    for level in sorted(buckets.keys(), key=lambda x: order.get(x, 99)):
        pts = buckets[level]
        out.append(
            {
                "超温等级": level,
                "测点及位置列表": "、".join(pts),
                "测点数量": len(pts),
            }
        )
    return out


_Q2_EVENT_SUMMARY_KEYS: tuple[str, ...] = (
    "全事件平均负荷_MW",
    "全事件负荷_percent",
    "全事件主汽压力_MPa",
    "全事件实测最高壁温",
    "全事件最高壁温测点",
    "全事件最大超温差值_监测",
    "全事件最大超温差值_设计",
    "全事件平均超温差值_监测",
    "分区域设计壁温",
)


def _extract_q2_event_summary(rows: list[dict]) -> dict[str, Any]:
    """从 q2 任一行提取 JOIN 的全事件级字段（各行相同）。"""
    for row in rows:
        if not isinstance(row, dict):
            continue
        summary = {k: row[k] for k in _Q2_EVENT_SUMMARY_KEYS if row.get(k) not in (None, "")}
        if summary:
            return summary
    return {}


def _first_data_row(rows: list[dict]) -> dict[str, Any]:
    for row in rows:
        if isinstance(row, dict):
            return row
    return {}


def _is_wall_temp_sis_row(row: dict[str, Any]) -> bool:
    code = str(row.get("测点编码") or row.get("测点编号") or row.get("pi_code") or "").strip()
    name = str(row.get("测点名称") or row.get("point_name") or "")
    if _WALL_TEMP_POINT_CODE_RE.match(code):
        return True
    if "CT" in code.upper() and "HAD" in code.upper():
        return True
    if "壁温" in name and "减温水" not in name:
        return True
    return False


def _filter_q4b_sis_rows(rows: list[dict]) -> list[dict]:
    return [r for r in rows if isinstance(r, dict) and not _is_wall_temp_sis_row(r)]


def _rows_for_table_slot(table_id: str, rows: list[dict]) -> list[dict]:
    if table_id == "overheat_q2_severity":
        return _aggregate_q2_severity_table_rows(rows)
    if table_id == "overheat_q4_sis":
        return _filter_q4b_sis_rows(rows)
    return rows


def _fmt_template_val(val: Any, fallback: str = "待补充") -> str:
    if val is None:
        return fallback
    s = str(val).strip()
    return s if s else fallback


_OVERHEAT_CRITICAL_DELTA_C = 40.0
_WALL_TEMP_POINT_CODE_RE = re.compile(r"^\d+[A-Z]{2,}\d*CT\d+", re.IGNORECASE)


def _normalize_steam_pressure_mpa(val: Any) -> tuple[float | None, bool]:
    """SIS 原值 >25 时按 kPa→MPa（/1000）换算。"""
    num = _q2_numeric(val)
    if num is None:
        return None, False
    if num > 25:
        return round(num / 1000.0, 3), True
    return num, False


def _format_steam_pressure_display(val: Any) -> str:
    mpa, converted = _normalize_steam_pressure_mpa(val)
    if mpa is None:
        return "待补充"
    suffix = "（已由kPa换算）" if converted else ""
    return f"{mpa:.2f}{suffix}"


def _split_point_list_entries(text: str) -> list[str]:
    s = (text or "").strip()
    if not s or s == "无":
        return []
    return [p.strip() for p in s.split("、") if p.strip()]


def _truncate_point_list(text: str, *, max_entries: int = 5) -> str:
    """按完整测点条目计数截断，避免 UTF-8 字符中间切断。"""
    parts = _split_point_list_entries(text)
    if not parts:
        return "无"
    if len(parts) <= max_entries:
        return f"{'、'.join(parts)}，共{len(parts)}个"
    shown = "、".join(parts[:max_entries])
    return f"前{max_entries}个：{shown}…等，共{len(parts)}个"


def _q2_numeric(val: Any) -> float | None:
    if val is None or str(val).strip() == "":
        return None
    try:
        return float(val)
    except (TypeError, ValueError):
        return None


def _parse_q2_time(val: Any) -> datetime | None:
    if val is None:
        return None
    if isinstance(val, datetime):
        return val
    s = str(val).strip()
    if not s:
        return None
    for fmt in (
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d %H:%M",
        "%Y/%m/%d %H:%M:%S",
        "%Y/%m/%d %H:%M",
    ):
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            continue
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None


def _format_duration_seconds(seconds: int) -> str:
    if seconds <= 0:
        return "0秒"
    hours, rem = divmod(seconds, 3600)
    minutes, secs = divmod(rem, 60)
    parts: list[str] = []
    if hours:
        parts.append(f"{hours}小时")
    if minutes:
        parts.append(f"{minutes}分")
    if secs or not parts:
        parts.append(f"{secs}秒")
    return "".join(parts)


def _q2_global_time_window(rows: list[dict]) -> tuple[str, str, str]:
    """返回 (起始, 结束, 持续时长描述)。"""
    starts: list[datetime] = []
    ends: list[datetime] = []
    total_limit_sec = 0
    for row in rows:
        if not isinstance(row, dict):
            continue
        st = _parse_q2_time(row.get("最早超温起始"))
        et = _parse_q2_time(row.get("最晚超温结束"))
        if st:
            starts.append(st)
        if et:
            ends.append(et)
        dur = _q2_numeric(row.get("超温总时长_秒"))
        if dur is not None:
            total_limit_sec += int(dur)
    if not starts or not ends:
        return "待补充", "待补充", "待补充"
    start_dt = min(starts)
    end_dt = max(ends)
    span_sec = int((end_dt - start_dt).total_seconds())
    if span_sec > 0:
        duration = _format_duration_seconds(span_sec)
    elif total_limit_sec > 0:
        duration = f"累计超温{_format_duration_seconds(total_limit_sec)}"
    else:
        duration = "待补充"
    return (
        start_dt.strftime("%Y-%m-%d %H:%M:%S"),
        end_dt.strftime("%Y-%m-%d %H:%M:%S"),
        duration,
    )


def _q2_core_point_annotations(rows: list[dict], *, limit: int = 3) -> str:
    scored: list[tuple[float, str]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        dur = _q2_numeric(row.get("超温总时长_秒")) or 0.0
        code = _fmt_template_val(row.get("测点编号"), "")
        period = _fmt_template_val(row.get("时段说明"), "")
        if not code or code == "待补充":
            continue
        label = f"{code}：{period}" if period and period != "待补充" else code
        scored.append((dur, label))
    if not scored:
        return ""
    scored.sort(key=lambda x: x[0], reverse=True)
    labels = [label for _, label in scored[:limit]]
    return "；".join(labels)


def _overheat_anomaly_level_from_q1(
    row: dict[str, Any],
    *,
    q2d_row: dict[str, Any] | None = None,
) -> str:
    """模板口径：Ⅰ轻微、Ⅱ中度、Ⅲ严重、Ⅳ临界爆管（非「严重=Ⅳ」）。"""
    severe = int(_q2_numeric(row.get("严重超温数量")) or 0)
    moderate = int(_q2_numeric(row.get("中度超温数量")) or 0)
    mild = int(_q2_numeric(row.get("轻微超温数量")) or 0)
    max_delta = _q2_numeric(row.get("全事件最大超温差值_监测"))
    if q2d_row:
        max_delta = max_delta if max_delta is not None else _q2_numeric(q2d_row.get("全事件最大超温差值_监测"))
    critical = (
        severe > 0
        and max_delta is not None
        and max_delta >= _OVERHEAT_CRITICAL_DELTA_C
    )
    if critical:
        return "Ⅳ级（临界爆管风险）"
    if severe > 0:
        return "Ⅲ级（严重超温）"
    if moderate > 0:
        return "Ⅱ级（中度超温）"
    if mild > 0:
        return "Ⅰ级（轻微超温）"
    return "Ⅰ级（正常）"


def _render_overheat_ch1_basic_info(
    rows: list[dict],
    *,
    gathered_data: dict[str, list[dict]] | None = None,
    **_kwargs: Any,
) -> str:
    row = _first_data_row(rows)
    q2d_row = _first_data_row((gathered_data or {}).get("q2d") or [])
    if not row:
        return "（待补充）\n"
    now = datetime.now()
    report_no = f"GL-CW-{now.strftime('%Y%m%d')}-001"
    gen_time = now.strftime("%Y年%m月%d日 %H:%M")
    boiler = _fmt_template_val(row.get("机组名称"))
    model = _fmt_template_val(row.get("锅炉型号"))
    load_mw = _fmt_template_val(row.get("额定负荷_MW"))
    monitor = _fmt_template_val(row.get("监测部位"))
    total = int(_q2_numeric(row.get("超温测点总数")) or 0)
    mild = int(_q2_numeric(row.get("轻微超温数量")) or 0)
    moderate = int(_q2_numeric(row.get("中度超温数量")) or 0)
    severe = int(_q2_numeric(row.get("严重超温数量")) or 0)
    anomaly = _overheat_anomaly_level_from_q1(row, q2d_row=q2d_row or None)
    lines = [
        f"1.报告编号：{report_no}",
        f"2.生成时间：{gen_time}",
        f"3.机组信息：机组编号{boiler}、锅炉型号{model}、额定负荷{load_mw}MW",
        f"4.监测部位：{monitor}",
        "5.数据来源：SIS/DCS 实时监测与历史台账",
        f"6.分析主体：{boiler}",
        f"7.异常等级：{anomaly}",
        (
            f"8.超温测点数量：共{total}个"
            f"（轻微{mild}个、中度{moderate}个、严重{severe}个）"
        ),
    ]
    return "\n".join(lines) + "\n"


def _q4a_global_time_window(rows: list[dict]) -> tuple[str, str, str]:
    """q2a 失败时由 q4a 采集时间推导全局起止。"""
    times: list[datetime] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        t = _parse_q2_time(row.get("采集时间") or row.get("start_time"))
        if t:
            times.append(t)
    if not times:
        return "待补充", "待补充", "待补充"
    start_dt, end_dt = min(times), max(times)
    span_sec = int((end_dt - start_dt).total_seconds())
    duration = _format_duration_seconds(span_sec) if span_sec > 0 else "待补充"
    return (
        start_dt.strftime("%Y-%m-%d %H:%M:%S"),
        end_dt.strftime("%Y-%m-%d %H:%M:%S"),
        duration,
    )


def _render_overheat_ch2_item1(
    rows: list[dict],
    *,
    gathered_data: dict[str, list[dict]] | None = None,
    **_kwargs: Any,
) -> str:
    if not rows:
        q4a = (gathered_data or {}).get("q4a") or []
        if q4a:
            start, end, duration = _q4a_global_time_window(q4a)
            note = "（测点时段明细不可用，由壁温时序推导）"
            return (
                f"1. 超温起止时段：起始{start} 结束{end} 持续{duration}{note}\n"
            )
        return "1. 超温起止时段：起始待补充 结束待补充 持续待补充\n"
    start, end, duration = _q2_global_time_window(rows)
    line = f"1. 超温起止时段：起始{start} 结束{end} 持续{duration}"
    core = _q2_core_point_annotations(rows)
    if core:
        line += f"（核心测点时段：{core}）"
    return line + "\n"


def _render_overheat_ch2_item2(rows: list[dict], **_kwargs: Any) -> str:
    row = _first_data_row(rows)
    load_pct = _fmt_template_val(row.get("全事件负荷_percent") or row.get("负荷_percent"))
    pressure_raw = row.get("全事件主汽压力_MPa") or row.get("主汽压力_MPa")
    pressure = _format_steam_pressure_display(pressure_raw)
    avg_mw = _fmt_template_val(row.get("全事件平均负荷_MW") or row.get("平均负荷_MW"))
    return (
        "2. 运行工况："
        f"负荷{load_pct}%"
        f"、主汽压力{pressure}MPa"
        f"（平均负荷{avg_mw}MW）\n"
    )


_CH2_SEVERITY_LABELS: tuple[tuple[str, str], ...] = (
    ("严重超温", "严重超温（≥20℃）"),
    ("中度超温", "中度超温（10～20℃）"),
    ("轻微超温", "轻微超温（5～10℃）"),
)


def _severity_buckets_from_rows(rows: list[dict]) -> dict[str, dict]:
    """优先使用 q2c 已聚合行；否则从测点明细行聚合。"""
    buckets: dict[str, dict] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        level = _normalize_overheat_level(row.get("超温等级") or row.get("overheat_severity"))
        if not level:
            continue
        if row.get("测点及位置列表") is not None or row.get("测点数量") is not None:
            buckets[level] = row
    if buckets:
        return buckets
    return {row["超温等级"]: row for row in _aggregate_q2_severity_table_rows(rows)}


def _rows_for_template_slot(
    gathered_data: dict[str, list[dict]],
    template_id: str,
    source_item_ids: tuple[str, ...],
) -> list[dict]:
    if template_id in ("overheat_daily_section", "overheat_weekly_section"):
        return gathered_data.get("q1") or []
    if template_id == "overheat_ch2_item1":
        return gathered_data.get("q2a") or []
    if template_id == "overheat_ch2_item5":
        return gathered_data.get("q2a") or []
    return _gather_item_rows(gathered_data, source_item_ids)


def _render_overheat_ch2_item3(rows: list[dict], **_kwargs: Any) -> str:
    agg = _severity_buckets_from_rows(rows)
    lines = ["3. 超温测点汇总："]
    for level_key, label in _CH2_SEVERITY_LABELS:
        bucket = agg.get(level_key)
        if bucket:
            pts = _truncate_point_list(str(bucket.get("测点及位置列表") or "无"))
            count = bucket.get("测点数量") or 0
            lines.append(f"{label}：{pts}，共{count}个")
        else:
            lines.append(f"{label}：无，共0个")
    return "\n".join(lines) + "\n"


def _render_overheat_ch2_item4(rows: list[dict], **_kwargs: Any) -> str:
    row = _first_data_row(rows)
    design_temps = _fmt_template_val(row.get("分区域设计壁温"))
    max_temp = _fmt_template_val(row.get("全事件实测最高壁温"))
    max_point = _fmt_template_val(row.get("全事件最高壁温测点"))
    delta_design = _fmt_template_val(row.get("全事件最大超温差值_设计"))
    delta_monitor = _fmt_template_val(row.get("全事件最大超温差值_监测"))
    avg_delta = _fmt_template_val(row.get("全事件平均超温差值_监测"))
    return (
        "4. 设计允许壁温&实际温度："
        f"分区域设计壁温{design_temps}；"
        f"实测最高{max_temp}℃（测点{max_point}），"
        f"最大超温差值{delta_design}℃（设计口径）/ {delta_monitor}℃（监测口径）；"
        f"各超温测点平均超温差值{avg_delta}℃\n"
    )


def _infer_overheat_distribution_from_q3a(rows: list[dict]) -> tuple[str, str]:
    """q2a 失败时用 q3a 累计超温时长判断集中/分散/混合。"""
    if not rows:
        return "待判定", "q3a 无区域汇总行"
    durations: list[tuple[str, float]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        name = str(row.get("超温区域") or row.get("受热面名称") or "").strip()
        dur = _q2_numeric(row.get("累计超温时长_秒") or row.get("累计超温时长"))
        if name and dur is not None and dur > 0:
            durations.append((name, dur))
    if not durations:
        return "待判定", "q3a 无有效累计超温时长"
    durations.sort(key=lambda x: x[1], reverse=True)
    total = sum(d for _, d in durations)
    top_name, top_dur = durations[0]
    if len(durations) == 1:
        return "集中式", f"超温集中于{top_name}（累计{int(top_dur)}秒）"
    if top_dur / total >= 0.6:
        return "集中式", f"{top_name}累计时长占比最高（约{top_dur / total:.0%}）"
    if len(durations) >= 4 and top_dur / total < 0.35:
        names = "、".join(n for n, _ in durations[:4])
        return "分散式", f"超温分散在{len(durations)}个区域（{names}等）"
    return "混合型", f"共{len(durations)}个区域有累计超温，{top_name}时长最长"


def _infer_overheat_distribution(rows: list[dict]) -> tuple[str, str]:
    from collections import Counter

    device_counts: Counter[str] = Counter()
    for row in rows:
        if not isinstance(row, dict):
            continue
        name = str(row.get("受热面名称") or row.get("device_name") or "").strip()
        if not name:
            continue
        device_counts[name] += 1
    if not device_counts:
        return "待判定", "q2a 无有效受热面名称字段"
    multi = [name for name, cnt in device_counts.items() if cnt >= 2]
    singles = [name for name, cnt in device_counts.items() if cnt == 1]
    total_devices = len(device_counts)
    total_points = sum(device_counts.values())
    if total_devices == 1 and total_points >= 2:
        only = next(iter(device_counts))
        return "集中式", f"全部{total_points}个超温测点均位于{only}"
    if total_devices >= 3 and not multi:
        names = "、".join(sorted(device_counts.keys())[:5])
        suffix = "等" if total_devices > 5 else ""
        return "分散式", f"超温测点分散在{total_devices}个受热面（{names}{suffix}），各面均为单点超温"
    if multi and not singles:
        names = "、".join(multi[:3])
        suffix = "等" if len(multi) > 3 else ""
        return "集中式", f"超温测点集中在{names}{suffix}等{total_devices}个受热面，存在同面多测点超温"
    names = "、".join(multi[:2]) if multi else "、".join(sorted(device_counts.keys())[:2])
    return "混合型", f"共{total_points}个测点分布于{total_devices}个受热面，{names}等存在多测点或跨面分布"


def _render_overheat_ch2_item5(
    rows: list[dict],
    *,
    gathered_data: dict[str, list[dict]] | None = None,
    **_kwargs: Any,
) -> str:
    if not rows:
        q3a = (gathered_data or {}).get("q3a") or []
        if q3a:
            pattern, reason = _infer_overheat_distribution_from_q3a(q3a)
            return f"5. 超温分布特征：{pattern}（{reason}；由区域汇总推导）\n"
        return "5. 超温分布特征：待判定（测点时段与区域汇总均无数据）\n"
    pattern, reason = _infer_overheat_distribution(rows)
    return f"5. 超温分布特征：{pattern}（{reason}）\n"


_OVERHEAT_CH2_TEMPLATE_RENDERERS: dict[str, Callable[[list[dict]], str]] = {
    "overheat_ch1_basic": _render_overheat_ch1_basic_info,
    "overheat_ch2_item1": _render_overheat_ch2_item1,
    "overheat_ch2_item2": _render_overheat_ch2_item2,
    "overheat_ch2_item3": _render_overheat_ch2_item3,
    "overheat_ch2_item4": _render_overheat_ch2_item4,
    "overheat_ch2_item5": _render_overheat_ch2_item5,
}


def _render_template_slot(
    template_id: str,
    rows: list[dict],
    *,
    gathered_data: dict[str, list[dict]] | None = None,
    task_status: dict[str, str] | None = None,
    report_context: dict[str, Any] | None = None,
) -> str:
    if template_id == "overheat_daily_section":
        rows = gathered_data.get("q1") or [] if gathered_data else rows
        return render_overheat_daily_section(
            rows,
            report_context=report_context,
            render_table=render_markdown_table,
            max_rows=50,
            empty_message="（无测点超温明细）",
        )
    if template_id == "overheat_weekly_section":
        gd = gathered_data or {}
        return render_overheat_weekly_section(
            gd.get("q1") or [],
            gd.get("q2") or [],
            gd.get("q3") or [],
            report_context=report_context,
            render_table=render_markdown_table,
            max_rows=50,
            empty_message="（无数据）",
        )
    renderer = _OVERHEAT_CH2_TEMPLATE_RENDERERS.get(template_id)
    if not renderer:
        raise ValueError(f"unknown_template_id:{template_id}")
    return renderer(rows, gathered_data=gathered_data, task_status=task_status)


def _audit_preview_row_limit(item_id: str, row_count: int, *, slot_id: str = "") -> int:
    if item_id == "q2a" and slot_id.startswith("s02"):
        return min(max(3, row_count), 12)
    if item_id in ("q2b", "q2d", "q5b") and slot_id.startswith(("s02", "s10")):
        return min(1, row_count)
    if item_id == "q2c" and slot_id.startswith("s02"):
        return min(3, row_count)
    if item_id == "q6" and slot_id.startswith("s06"):
        return min(max(5, row_count), 24)
    if item_id == "q7" and slot_id.startswith("s06"):
        return min(max(5, row_count), 24)
    if item_id == "q5" and slot_id.startswith(("s06", "s10")):
        return min(max(5, row_count), 24)
    return min(3, row_count)


def _audit_format_field(key: str, val: Any) -> str:
    kl = str(key).lower()
    if kl in ("全事件主汽压力_mpa", "主汽压力_mpa", "steam_pressure_value"):
        mpa, converted = _normalize_steam_pressure_mpa(val)
        if mpa is None:
            return f"{key}=待补充"
        note = "（kPa→MPa）" if converted else ""
        return f"{key}={mpa:.3f}MPa{note}"
    hint = _FIELD_UNIT_HINTS.get(kl, "")
    suffix = f" （{hint}）" if hint else ""
    return f"{key}={val}{suffix}"


def _append_q1_top_points(lines: list[str], rows: list[dict]) -> None:
    scored: list[tuple[float, str]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        delta = _q2_numeric(row.get("最大监测超温差值_℃")) or 0.0
        label = format_overheat_entity_label(
            str(row.get("测点名称") or "").strip() or None,
            str(row.get("测点编号") or "").strip() or None,
        )
        if label != "未知测点":
            scored.append((delta, label))
    if not scored:
        return
    scored.sort(key=lambda x: x[0], reverse=True)
    top = "、".join(c for _, c in scored[:5])
    lines.append(f"- 代表超温测点(温差排序): {top}")


def _append_q2a_top_points(lines: list[str], rows: list[dict]) -> None:
    scored: list[tuple[float, str]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        dur = _q2_numeric(row.get("超温总时长_秒")) or 0.0
        label = format_overheat_entity_label(
            str(row.get("测点名称") or "").strip() or None,
            str(row.get("测点编号") or "").strip() or None,
        )
        if label != "未知测点":
            scored.append((dur, label))
    if not scored:
        return
    scored.sort(key=lambda x: x[0], reverse=True)
    top = "、".join(c for _, c in scored[:5])
    lines.append(f"- 尖峰测点(时长排序): {top}")


def _append_q3b_top_points(lines: list[str], rows: list[dict]) -> None:
    scored: list[tuple[float, str]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        cnt = _q2_numeric(row.get("瞬时尖峰超温次数")) or 0.0
        label = format_overheat_entity_label(
            str(row.get("测点名称") or "").strip() or None,
            str(row.get("测点编号") or "").strip() or None,
        )
        if label != "未知测点":
            scored.append((cnt, label))
    if not scored:
        return
    scored.sort(key=lambda x: x[0], reverse=True)
    top = "、".join(c for _, c in scored[:5])
    lines.append(f"- 尖峰频次测点: {top}")


def _append_q6_soot_agg_hint(lines: list[str], rows: list[dict]) -> None:
    """q6 区域吹灰汇总：突出吹灰次数偏少/偏多的受热面，供运行操作归因。"""
    scored: list[tuple[int, str, Any]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        boiler = str(row.get("机组名称") or "").strip()
        dev = str(row.get("受热面名称") or "未知受热面").strip()
        try:
            n = int(row.get("吹灰次数") or 0)
        except (TypeError, ValueError):
            continue
        label = f"{boiler}/{dev}" if boiler else dev
        days = row.get("吹灰天数")
        scored.append((n, label, days))
    if not scored:
        return
    scored.sort(key=lambda x: x[0])
    low_parts = []
    for n, label, days in scored[:5]:
        seg = f"{label}{n}次"
        if days is not None and str(days).strip() != "":
            seg += f"({days}天)"
        low_parts.append(seg)
    lines.append(f"- 吹灰频次偏低区域（升序）: {'、'.join(low_parts)}")
    if len(scored) > 5:
        high = sorted(scored, key=lambda x: x[0], reverse=True)[:3]
        hi_parts = []
        for n, label, days in high:
            seg = f"{label}{n}次"
            if days is not None and str(days).strip() != "":
                seg += f"({days}天)"
            hi_parts.append(seg)
        lines.append(f"- 吹灰频次偏高区域: {'、'.join(hi_parts)}")


def _append_q5_sis_agg_hint(lines: list[str], rows: list[dict]) -> None:
    """q5 SIS 关联参数汇总：按六类参数类型归纳，并提示缺失类型。"""
    expected_types = ("减温水", "烟温", "排烟", "总风量", "氧量", "炉膛负压")
    by_type: dict[str, list[str]] = {t: [] for t in expected_types}
    by_swing: list[tuple[float, str]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        ptype = str(row.get("参数类型") or "").strip()
        tag = str(row.get("测点编码") or "").strip()
        pname = str(row.get("测点名称") or "").strip()
        try:
            samples = int(row.get("采样点数") or 0)
        except (TypeError, ValueError):
            samples = 0
        tag_label = format_overheat_entity_label(pname or None, tag or None, fallback="")
        seg = f"{tag_label}{samples}点" if tag_label else f"{samples}点"
        if ptype in by_type:
            by_type[ptype].append(seg)
        vmin = _q2_numeric(row.get("最小值"))
        vmax = _q2_numeric(row.get("最大值"))
        if vmin is not None and vmax is not None and (tag or pname):
            swing_label = format_overheat_entity_label(pname or None, tag or None)
            by_swing.append((vmax - vmin, f"{ptype}/{swing_label} {vmin:.2g}~{vmax:.2g}"))
    present_parts: list[str] = []
    for ptype in expected_types:
        items = by_type.get(ptype) or []
        if items:
            present_parts.append(f"{ptype}:{'、'.join(items[:3])}")
    if present_parts:
        lines.append(f"- SIS关联参数汇总: {'；'.join(present_parts)}")
    missing = [t for t in expected_types if not by_type.get(t)]
    if missing:
        lines.append(f"- 未接入参数类型: {'、'.join(missing)} → 报告须写待补充")
    if by_swing:
        by_swing.sort(key=lambda x: x[0], reverse=True)
        parts = [desc for _, desc in by_swing[:3]]
        lines.append(f"- 参数波动较大测点: {'、'.join(parts)}")


def _append_q7_mill_agg_hint(lines: list[str], rows: list[dict]) -> None:
    """q7 磨煤机运行汇总：突出采样偏少或给煤/负荷波动较大的磨煤机。"""
    low_activity: list[tuple[int, str]] = []
    high_coal: list[tuple[float, str]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        boiler = str(row.get("机组名称") or "").strip()
        mill = str(row.get("磨煤机名称") or "未知磨煤机").strip()
        label = f"{boiler}/{mill}" if boiler else mill
        try:
            samples = int(row.get("采样记录数") or 0)
        except (TypeError, ValueError):
            samples = 0
        low_activity.append((samples, label))
        max_coal = _q2_numeric(row.get("最大给煤量_t_h"))
        avg_coal = _q2_numeric(row.get("平均给煤量_t_h"))
        if max_coal is not None and avg_coal is not None and avg_coal > 0:
            high_coal.append((max_coal / avg_coal, f"{label}最大/平均给煤{max_coal:.1f}/{avg_coal:.1f}t/h"))
        elif max_coal is not None:
            high_coal.append((max_coal, f"{label}最大给煤{max_coal:.1f}t/h"))
    if low_activity:
        low_activity.sort(key=lambda x: x[0])
        parts = [f"{label}{n}条" for n, label in low_activity[:5]]
        lines.append(f"- 磨煤机采样偏少（升序）: {'、'.join(parts)}")
    if high_coal:
        high_coal.sort(key=lambda x: x[0], reverse=True)
        parts = [desc for _, desc in high_coal[:3]]
        lines.append(f"- 给煤波动较大磨煤机: {'、'.join(parts)}")


def _build_audit_facts(
    subset: dict[str, list[dict]],
    query: str,
    *,
    slot_id: str = "",
    task_status: dict[str, str] | None = None,
) -> str:
    lines: list[str] = ["【可引用事实（正文数值仅可来自下列键值；勿在报告中写数据源编号）】"]
    status = task_status or {}
    for iid, rows in subset.items():
        label = _data_source_label(iid)
        st = status.get(iid, "")
        if st in ("mandatory_failed", "optional_failed"):
            lines.append(f"- [{label}] 查询失败 → 须写明查询失败（非无数据）")
            continue
        if not rows:
            if iid == "q5" and st == "success":
                lines.append(
                    f"- [{label}] 无非壁温 SIS 测点 → 减温水/烟温/氧量/炉膛负压等须写「待补充」"
                )
            else:
                lines.append(f"- [{label}] 无数据行 → 不得编造该部分明细")
            continue
        preview_n = _audit_preview_row_limit(iid, len(rows), slot_id=slot_id)
        lines.append(f"- [{label}] {len(rows)} 行")
        for row in rows[:preview_n]:
            if not isinstance(row, dict):
                continue
            shown = 0
            for key, val in row.items():
                if val is None or str(val).strip() == "":
                    continue
                lines.append(f"  {_audit_format_field(str(key), val)}")
                shown += 1
                if shown >= 18:
                    break
        if len(rows) > preview_n:
            lines.append(f"  ……共 {len(rows)} 行，以上仅预览前 {preview_n} 行")
        if iid == "q1" and not slot_id.startswith("s06_cause"):
            _append_q1_top_points(lines, rows)
        if iid == "q2a":
            _append_q2a_top_points(lines, rows)
        if iid == "q3b":
            _append_q3b_top_points(lines, rows)
        if iid == "q5":
            _append_q5_sis_agg_hint(lines, rows)
        if iid == "q6" and not slot_id.startswith("s06_cause"):
            _append_q6_soot_agg_hint(lines, rows)
        if iid == "q7":
            _append_q7_mill_agg_hint(lines, rows)
    q = (query or "").strip()
    if q:
        lines.append(f"- 用户问题约束: {q[:500]}")
    lines.append("- 主汽压力已换算为 MPa 的以事实清单为准；禁止自造 MPa/MW/℃")
    return "\n".join(lines)


def _build_data_coverage_note(
    subset: dict[str, list[dict]],
    *,
    task_status: dict[str, str] | None = None,
) -> str:
    status = task_status or {}
    parts: list[str] = []
    for iid, rows in subset.items():
        label = _data_source_label(iid)
        st = status.get(iid, "")
        if st in ("mandatory_failed", "optional_failed"):
            parts.append(f"{label}=查询失败")
        else:
            parts.append(f"{label}={'有' if rows else '无'}数据")
    if not parts:
        return ""
    return "【本槽数据覆盖】" + "；".join(parts)


def _rag_snippets_for_slot(
    context_snippets: list[str],
    gathered_data: dict[str, list[dict]],
    item_ids: tuple[str, ...],
) -> list[str]:
    """绑定查询项全无行时收紧 RAG，避免用片段中的示例数值补全正文。"""
    if not context_snippets:
        return []
    if not item_ids:
        return context_snippets[:8]
    if any(_gather_item_rows(gathered_data, item_ids)):
        return context_snippets[:8]
    return context_snippets[:2]


def _wrap_narrative_markdown(title: str, body: str) -> str:
    cleaned = _sanitize_report_narrative(strip_leading_duplicate_heading((body or "").strip(), title))
    if not cleaned:
        return f"### {title}\n\n（待补充）\n\n" if title else "（待补充）\n\n"
    if title:
        return f"### {title}\n\n{cleaned}\n\n"
    return f"{cleaned}\n\n"


def _build_overheat_charts(records: list[dict], *, chart_mode: str) -> tuple[str, list[dict[str, Any]]]:
    if chart_mode == "off" or not records:
        return "", []
    trend: list[dict[str, Any]] = []
    zone_buckets: dict[str, int] = {}
    for r in records:
        t = r.get("start_time") or r.get("采集时间") or r.get("time") or r.get("timestamp")
        temp = r.get("highest_temp") or r.get("壁温值") or r.get("temperature") or r.get("temp") or r.get("实测最高壁温_℃")
        if t is not None and temp is not None:
            try:
                trend.append({"time": str(t), "temperature": float(temp)})
            except (TypeError, ValueError):
                pass
        zone = (
            str(r.get("device_name") or r.get("设备名称") or r.get("监测部位") or r.get("boiler_name") or "unknown")[:64]
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


def _build_dcs_linkage_charts(records: list[dict], *, chart_mode: str) -> tuple[str, list[dict[str, Any]]]:
    """九、附件 DCS 参数联动趋势图（q6d：长表 参数类型/参数值 或 docx 宽表字段）。"""
    if chart_mode == "off" or not records:
        return "", []
    by_param: dict[str, list[dict[str, Any]]] = {}
    wide_field_map = (
        ("壁温_℃", "壁温"),
        ("highest_temp", "壁温"),
        ("机组负荷_MW", "机组负荷"),
        ("mw_value", "机组负荷"),
        ("主汽压力_MPa", "主汽压力"),
        ("steam_pressure_value", "主汽压力"),
        ("超温差值_℃", "超温差值"),
    )

    for r in records:
        if not isinstance(r, dict):
            continue
        t = (
            r.get("采集时间")
            or r.get("start_time")
            or r.get("数据时间")
            or r.get("time")
            or r.get("timestamp")
        )
        if t is None:
            continue
        param_type = r.get("参数类型")
        if param_type is not None:
            val = r.get("参数值")
            if val is None:
                continue
            try:
                point = {"time": str(t), "value": float(val)}
            except (TypeError, ValueError):
                continue
            key = str(param_type).strip() or "参数"
            by_param.setdefault(key, []).append(point)
            continue
        for field, label in wide_field_map:
            if field not in r or r.get(field) is None:
                continue
            try:
                point = {"time": str(t), "value": float(r.get(field))}
            except (TypeError, ValueError):
                continue
            by_param.setdefault(label, []).append(point)

    charts: list[dict[str, Any]] = []
    md_parts: list[str] = []
    for param, points in sorted(by_param.items(), key=lambda x: (-len(x[1]), x[0]))[:8]:
        if not points:
            continue
        points.sort(key=lambda p: p["time"])
        trimmed = points[:500]
        spec = {
            "id": f"dcs_linkage_{abs(hash(param)) % 10_000_000}",
            "chart_type": "line",
            "title": f"DCS联动-{param}",
            "spec": {
                "x_field": "time",
                "y_field": "value",
                "series_name": param,
                "data": trimmed,
            },
        }
        charts.append(spec)
        md_parts.append(f"- DCS联动图：`{spec['title']}`（{len(trimmed)} 点）")
    md = "\n".join(md_parts) + "\n\n" if md_parts else ""
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
        stream_chunk_chars: int = 16,
        stream_chunk_delay_ms: float = 0.0,
        idle_heartbeat_seconds: float = 5.0,
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
        self._stream_chunk_chars = max(1, stream_chunk_chars)
        self._stream_chunk_delay_ms = max(0.0, float(stream_chunk_delay_ms))
        self._idle_heartbeat_seconds = max(0.5, float(idle_heartbeat_seconds))
        self._json_fallback = json_fallback or (lambda o: str(o))

    @staticmethod
    def _chunk_text(text: str, chunk_size: int) -> list[str]:
        if not text:
            return []
        size = max(1, chunk_size)
        return [text[i : i + size] for i in range(0, len(text), size)]

    async def _sleep_stream_chunk_delay(self) -> None:
        if self._stream_chunk_delay_ms > 0:
            await asyncio.sleep(self._stream_chunk_delay_ms / 1000.0)

    def _loading_event(
        self,
        *,
        active: bool,
        slot_id: str = "",
        slot_index: int | None = None,
        phase: str = "waiting_slot",
        elapsed_ms: int | None = None,
        hint: str = "",
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "event": "synthesis_loading",
            "active": active,
            "phase": phase,
        }
        if slot_id:
            payload["slot_id"] = slot_id
        if slot_index is not None:
            payload["slot_index"] = slot_index
        if elapsed_ms is not None:
            payload["elapsed_ms"] = elapsed_ms
        if hint:
            payload["hint"] = hint
        return payload

    def _llm_messages_for_slot(
        self,
        *,
        query: str,
        analysis_type: str,
        data_mode: str,
        gathered_data: dict[str, list[dict]],
        context_snippets: list[str],
        planning_context: str | None,
        slot: SynthesisV2Slot,
        task_status: dict[str, str] | None = None,
        report_context: dict[str, Any] | None = None,
    ) -> list[dict[str, str]]:
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
            task_status=task_status,
            report_context=report_context,
        )
        return [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_content},
        ]

    async def _run_llm_slot_background(
        self,
        *,
        index: int,
        slot: SynthesisV2Slot,
        outputs: list[SynthesisV2SlotOutput | None],
        chunk_queue: asyncio.Queue[Any],
        sem: asyncio.Semaphore,
        query: str,
        analysis_type: str,
        data_mode: str,
        gathered_data: dict[str, list[dict]],
        context_snippets: list[str],
        planning_context: str | None,
        task_status: dict[str, str] | None = None,
        report_context: dict[str, Any] | None = None,
    ) -> None:
        title = slot.title.strip()
        try:
            async with sem:
                messages = self._llm_messages_for_slot(
                    query=query,
                    analysis_type=analysis_type,
                    data_mode=data_mode,
                    gathered_data=gathered_data,
                    context_snippets=context_snippets,
                    planning_context=planning_context,
                    slot=slot,
                    task_status=task_status,
                    report_context=report_context,
                )
                stream_body_parts: list[str] = []
                async for chunk in self._llm.stream_chat(
                    model=None,
                    messages=messages,
                    timeout=float(self._synthesis_timeout),
                    max_tokens=self._segment_max_tokens,
                ):
                    stream_body_parts.append(chunk)
                    await chunk_queue.put(chunk)
                body = strip_leading_duplicate_heading("".join(stream_body_parts), slot.title)
                body = _sanitize_report_narrative(body)
                outputs[index] = SynthesisV2SlotOutput(
                    slot.id,
                    slot.kind,
                    title,
                    _wrap_narrative_markdown(title, body),
                )
        except Exception as exc:  # noqa: BLE001
            logger.exception("synthesis v2 slot failed slot_id=%s", slot.id)
            err_md = f"### {title}\n\n（本章生成失败：{exc}）\n\n" if title else f"（本章生成失败：{exc}）\n\n"
            outputs[index] = SynthesisV2SlotOutput(slot.id, slot.kind, title, err_md, error=str(exc))
        finally:
            await chunk_queue.put(_LLM_STREAM_END)

    def _start_background_slot(
        self,
        *,
        index: int,
        slot: SynthesisV2Slot,
        outputs: list[SynthesisV2SlotOutput | None],
        sem: asyncio.Semaphore,
        query: str,
        analysis_type: str,
        data_mode: str,
        gathered_data: dict[str, list[dict]],
        context_snippets: list[str],
        planning_context: str | None,
        chart_mode: str,
        task_status: dict[str, str] | None = None,
        report_context: dict[str, Any] | None = None,
    ) -> tuple[asyncio.Task[None], asyncio.Queue[Any] | None]:
        if slot.kind == "llm_narrative":
            chunk_queue: asyncio.Queue[Any] = asyncio.Queue()
            task = asyncio.create_task(
                self._run_llm_slot_background(
                    index=index,
                    slot=slot,
                    outputs=outputs,
                    chunk_queue=chunk_queue,
                    sem=sem,
                    query=query,
                    analysis_type=analysis_type,
                    data_mode=data_mode,
                    gathered_data=gathered_data,
                    context_snippets=context_snippets,
                    planning_context=planning_context,
                    task_status=task_status,
                    report_context=report_context,
                )
            )
            return task, chunk_queue

        async def _deterministic_runner() -> None:
            outputs[index] = await self._render_slot(
                query=query,
                analysis_type=analysis_type,
                data_mode=data_mode,
                gathered_data=gathered_data,
                context_snippets=context_snippets,
                planning_context=planning_context,
                slot=slot,
                chart_mode=chart_mode,
                task_status=task_status,
                report_context=report_context,
            )

        return asyncio.create_task(_deterministic_runner()), None

    async def _await_task_with_heartbeat(
        self,
        task: asyncio.Task[None],
        *,
        slot: SynthesisV2Slot,
        slot_index: int,
        last_emit_at: float,
    ) -> AsyncIterator[dict[str, Any]]:
        while not task.done():
            try:
                await asyncio.wait_for(asyncio.shield(task), timeout=self._idle_heartbeat_seconds)
            except asyncio.TimeoutError:
                elapsed_ms = int((time.monotonic() - last_emit_at) * 1000)
                yield self._loading_event(
                    active=True,
                    slot_id=slot.id,
                    slot_index=slot_index,
                    phase="waiting_slot",
                    elapsed_ms=elapsed_ms,
                    hint=f"正在生成：{slot.title or slot.id}",
                )
        await task

    async def _iter_llm_slot_stream_deltas(
        self,
        *,
        slot: SynthesisV2Slot,
        slot_index: int,
        task: asyncio.Task[None],
        chunk_queue: asyncio.Queue[Any],
        last_emit_at: float,
    ) -> AsyncIterator[tuple[dict[str, Any], float]]:
        title = slot.title.strip()
        if title:
            yield ({"event": "summary_delta", "text": f"### {title}\n\n"}, time.monotonic())
            await self._sleep_stream_chunk_delay()
        got_body = False
        while True:
            if task.done() and chunk_queue.empty():
                break
            try:
                item = await asyncio.wait_for(chunk_queue.get(), timeout=self._idle_heartbeat_seconds)
            except asyncio.TimeoutError:
                elapsed_ms = int((time.monotonic() - last_emit_at) * 1000)
                yield (
                    self._loading_event(
                        active=True,
                        slot_id=slot.id,
                        slot_index=slot_index,
                        phase="waiting_slot",
                        elapsed_ms=elapsed_ms,
                        hint=f"正在生成：{title or slot.id}",
                    ),
                    last_emit_at,
                )
                continue
            if item is _LLM_STREAM_END:
                break
            got_body = True
            yield ({"event": "summary_delta", "text": item}, time.monotonic())
            last_emit_at = time.monotonic()
            await self._sleep_stream_chunk_delay()
        if not task.done():
            async for ev in self._await_task_with_heartbeat(
                task, slot=slot, slot_index=slot_index, last_emit_at=last_emit_at
            ):
                yield (ev, last_emit_at)
            await task
        if title and got_body:
            yield ({"event": "summary_delta", "text": "\n\n"}, time.monotonic())
            await self._sleep_stream_chunk_delay()

    async def _emit_markdown_chunks(
        self, text: str
    ) -> AsyncIterator[dict[str, Any]]:
        for piece in self._chunk_text(text, self._stream_chunk_chars):
            yield {"event": "summary_delta", "text": piece}
            await self._sleep_stream_chunk_delay()

    def _structured_events_for_output(self, out: SynthesisV2SlotOutput) -> list[dict[str, Any]]:
        events: list[dict[str, Any]] = []
        if self._emit_structured_sse and out.table:
            events.append(
                {"event": "table_payload", "slot_id": out.slot_id, "table": out.table}
            )
        if self._emit_structured_sse:
            for ch in out.charts or ([out.chart] if out.chart else []):
                events.append(
                    {"event": "chart_payload", "slot_id": out.slot_id, "chart": ch}
                )
        return events

    async def _emit_slot_in_order(
        self,
        *,
        index: int,
        slot: SynthesisV2Slot,
        outputs: list[SynthesisV2SlotOutput | None],
        task: asyncio.Task[None],
        chunk_queue: asyncio.Queue[Any] | None,
        last_emit_at: float,
    ) -> AsyncIterator[tuple[dict[str, Any], float]]:
        if slot.kind == "llm_narrative" and chunk_queue is not None:
            got_any_delta = False
            async for ev, ts in self._iter_llm_slot_stream_deltas(
                slot=slot,
                slot_index=index,
                task=task,
                chunk_queue=chunk_queue,
                last_emit_at=last_emit_at,
            ):
                if ev.get("event") == "summary_delta":
                    got_any_delta = True
                    last_emit_at = ts
                yield (ev, last_emit_at)
            out = outputs[index]
            if out is None:
                await task
                out = outputs[index]
            if out is not None and not got_any_delta:
                async for ev in self._emit_markdown_chunks(out.markdown):
                    yield (ev, time.monotonic())
                    last_emit_at = time.monotonic()
            if out is not None:
                for se in self._structured_events_for_output(out):
                    yield (se, time.monotonic())
            yield (
                self._loading_event(active=False, slot_id=slot.id, slot_index=index),
                time.monotonic(),
            )
            return

        async for hb in self._await_task_with_heartbeat(
            task, slot=slot, slot_index=index, last_emit_at=last_emit_at
        ):
            yield (hb, last_emit_at)
        out = outputs[index]
        if out is None:
            return
        async for ev in self._emit_markdown_chunks(out.markdown):
            yield (ev, time.monotonic())
            last_emit_at = time.monotonic()
        for se in self._structured_events_for_output(out):
            yield (se, time.monotonic())
        yield (
            self._loading_event(active=False, slot_id=slot.id, slot_index=index),
            time.monotonic(),
        )

    def _narrative_system_prompt(self, analysis_type: str) -> str:
        for scene in (
            f"analysis_synthesis_{analysis_type}_narrative",
            "analysis_synthesis_overheat_narrative",
            "analysis_synthesis",
        ):
            tpl = self._prompts.get_template(scene=scene, version="v1")
            if tpl and tpl.content.strip():
                base = tpl.content.strip()
                if analysis_type == "overheat_guidance":
                    return f"{base}\n\n{OVERHEAT_DOCX_AUTHORING_RULES}"
                return base
        fallback = (
            "你是《锅炉管壁超温智能分析报告》撰写专家。严格按【本章写作任务】输出指定章节正文；"
            "禁止编造数值；禁止 Markdown 标题行；禁止出现置信度、依据、q 编号；"
            "禁止增删模板章节或额外「结论摘要/综合评估/建议措施」结构。"
        )
        if analysis_type == "overheat_guidance":
            return f"{fallback}\n\n{OVERHEAT_DOCX_AUTHORING_RULES}"
        return fallback

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
        task_status: dict[str, str] | None = None,
        report_context: dict[str, Any] | None = None,
    ) -> str:
        subset = _resolve_data_subset(
            gathered_data,
            item_ids,
            strict=slot.kind == "llm_narrative",
            boiler_name=slot.boiler_name,
        )
        audit_facts = _build_audit_facts(subset, query, slot_id=slot.id, task_status=task_status)
        coverage = _build_data_coverage_note(subset, task_status=task_status)
        data_preview = json.dumps(subset, ensure_ascii=False, default=self._json_fallback)[
            : self._gathered_json_max_chars
        ]
        rag_list = _rag_snippets_for_slot(context_snippets, gathered_data, item_ids)
        rag_text = "\n".join(f"- {s}" for s in rag_list) if rag_list else "（无，且不得用常识补数值）"
        pc = (planning_context or "").strip()
        planning_block = f"\n分阶段规划意图(结构化要点):\n{pc[:2000]}\n" if pc else ""
        coverage_block = f"\n{coverage}\n" if coverage else ""
        dist_block = ""
        region_block = ""
        if slot.id == "s06_cause" or slot.id.startswith("s06_cause__"):
            q1_rows = subset.get("q1") or []
            q6_rows = subset.get("q6") or []
            dist_block = (
                "【内部分析参考·禁止写入报告】"
                + build_overheat_distribution_note(q1_rows, _infer_overheat_distribution)
                + "\n"
            )
            region_block = build_overheat_region_fact_packages(q1_rows, q6_rows) + "\n"
        boiler_block = ""
        if slot.boiler_name:
            boiler_block = f"本章目标机组: {slot.boiler_name}\n"
        mode_block = ""
        if report_context:
            mode = report_context.get("analysis_mode", "weekly")
            mode_block = f"报告分析模式: {'按日' if mode == 'daily' else '按周'}\n"
        return (
            f"分析类型: {analysis_type}\n"
            f"数据来源模式: {data_mode}\n"
            f"{mode_block}"
            f"{boiler_block}"
            f"用户问题: {query}\n"
            f"{planning_block}"
            f"{coverage_block}"
            f"{dist_block}"
            f"{region_block}"
            f"{audit_facts}\n"
            f"数据摘要(JSON截断): {data_preview}\n"
            f"RAG参考片段（仅规范/方法，不可作数值来源）:\n{rag_text}\n\n"
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
        task_status: dict[str, str] | None = None,
        report_context: dict[str, Any] | None = None,
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
            task_status=task_status,
            report_context=report_context,
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
        task_status: dict[str, str] | None = None,
        report_context: dict[str, Any] | None = None,
    ) -> SynthesisV2SlotOutput:
        title = slot.title.strip()
        try:
            if slot.kind == "static_markdown":
                md = slot.static_body
                return SynthesisV2SlotOutput(slot.id, slot.kind, title, md)

            if slot.kind == "template_deterministic":
                rows = _rows_for_template_slot(
                    gathered_data, slot.template_id, slot.source_item_ids
                )
                empty_msg = _table_empty_message(
                    slot.template_id,
                    slot.source_item_ids,
                    task_status=task_status,
                    gathered_data=gathered_data,
                )
                if slot.template_id == "overheat_daily_section":
                    body = render_overheat_daily_section(
                        gathered_data.get("q1") or [],
                        report_context=report_context,
                        render_table=render_markdown_table,
                        max_rows=self._table_max_rows,
                        empty_message=empty_msg,
                    )
                elif slot.template_id == "overheat_weekly_section":
                    body = render_overheat_weekly_section(
                        gathered_data.get("q1") or [],
                        gathered_data.get("q2") or [],
                        gathered_data.get("q3") or [],
                        report_context=report_context,
                        render_table=render_markdown_table,
                        max_rows=self._table_max_rows,
                        empty_message=empty_msg,
                    )
                else:
                    body = _render_template_slot(
                        slot.template_id,
                        rows,
                        gathered_data=gathered_data,
                        task_status=task_status,
                        report_context=report_context,
                    )
                md = _wrap_template_markdown(title, body)
                return SynthesisV2SlotOutput(slot.id, slot.kind, title, md)

            if slot.kind == "table_deterministic":
                empty_msg = _table_empty_message(
                    slot.table_id,
                    slot.source_item_ids,
                    task_status=task_status,
                    gathered_data=gathered_data,
                )
                rows = _gather_item_rows(gathered_data, slot.source_item_ids)
                rows = _rows_for_table_slot(slot.table_id, rows)
                md, tbl = render_markdown_table(
                    rows,
                    max_rows=self._table_max_rows,
                    title=title or slot.table_id,
                    empty_message=empty_msg,
                    subsection=True,
                )
                if tbl:
                    tbl["id"] = slot.table_id or tbl.get("id", slot.id)
                    tbl["source_item_ids"] = list(slot.source_item_ids)
                return SynthesisV2SlotOutput(slot.id, slot.kind, title, md, table=tbl)

            if slot.kind == "chart_structured":
                rows = _gather_item_rows(gathered_data, slot.source_item_ids)
                if slot.table_id == "overheat_q6_dcs_linkage":
                    md, charts = _build_dcs_linkage_charts(rows, chart_mode=chart_mode)
                else:
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
                body = await self._render_llm_slot(
                    query=query,
                    analysis_type=analysis_type,
                    data_mode=data_mode,
                    gathered_data=gathered_data,
                    context_snippets=context_snippets,
                    planning_context=planning_context,
                    slot=slot,
                    task_status=task_status,
                    report_context=report_context,
                )
                md = _wrap_narrative_markdown(title, body)
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
        task_status: dict[str, str] | None = None,
        report_context: dict[str, Any] | None = None,
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
                        task_status=task_status,
                        report_context=report_context,
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
                task_status=task_status,
                report_context=report_context,
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
        summary = _join_slot_markdown(parts)
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
        task_status: dict[str, str] | None = None,
        report_context: dict[str, Any] | None = None,
    ) -> SynthesisV2RunResult:
        slots = get_effective_synthesis_v2_slots(
            analysis_type, report_context=report_context, gathered_data=gathered_data
        )
        outputs = await self._fill_all_slots_parallel(
            slots=slots,
            query=query,
            analysis_type=analysis_type,
            data_mode=data_mode,
            gathered_data=gathered_data,
            context_snippets=context_snippets,
            planning_context=planning_context,
            chart_mode=chart_mode,
            task_status=task_status,
            report_context=report_context,
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
        task_status: dict[str, str] | None = None,
        report_context: dict[str, Any] | None = None,
    ) -> AsyncIterator[tuple[dict[str, Any], SynthesisV2RunResult | None]]:
        """
        后台并行生成各槽位，按槽位顺序就绪即推送（小块 summary_delta + 空闲心跳）；
        表/图可额外推送 table_payload / chart_payload。
        Yields (event_dict, None)；最后一次 yield (_, result)。
        """
        slots = get_effective_synthesis_v2_slots(
            analysis_type, report_context=report_context, gathered_data=gathered_data
        )
        if not slots:
            result = SynthesisV2RunResult(summary="", synthesis_version="v2:empty")
            yield ({}, result)
            return
        outputs: list[SynthesisV2SlotOutput | None] = [None] * len(slots)
        sem = asyncio.Semaphore(self._max_parallel_llm)
        bg_tasks: list[asyncio.Task[None]] = []
        bg_queues: list[asyncio.Queue[Any] | None] = []
        for i, slot in enumerate(slots):
            task, queue = self._start_background_slot(
                index=i,
                slot=slot,
                outputs=outputs,
                sem=sem,
                query=query,
                analysis_type=analysis_type,
                data_mode=data_mode,
                gathered_data=gathered_data,
                context_snippets=context_snippets,
                planning_context=planning_context,
                chart_mode=chart_mode,
                task_status=task_status,
                report_context=report_context,
            )
            bg_tasks.append(task)
            bg_queues.append(queue)

        last_emit_at = time.monotonic()
        for i, slot in enumerate(slots):
            async for ev, last_emit_at in self._emit_slot_in_order(
                index=i,
                slot=slot,
                outputs=outputs,
                task=bg_tasks[i],
                chunk_queue=bg_queues[i],
                last_emit_at=last_emit_at,
            ):
                yield (ev, None)

        filled = [o for o in outputs if o is not None]
        yield ({}, self._assemble_result(filled, analysis_type=analysis_type))

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
        task_status: dict[str, str] | None = None,
        report_context: dict[str, Any] | None = None,
    ) -> AsyncIterator[tuple[dict[str, Any], SynthesisV2RunResult | None]]:
        """
        首槽 LLM 真流式；其余槽后台并行、按注册表顺序就绪即推送（token/小块 + 空闲心跳）。
        Yields (event_dict, None) ；最后一次 yield (_, result)。
        """
        slots = get_effective_synthesis_v2_slots(
            analysis_type, report_context=report_context, gathered_data=gathered_data
        )
        if not slots:
            result = SynthesisV2RunResult(summary="", synthesis_version="v2:empty")
            yield ({"event": "summary_delta", "text": ""}, result)
            return

        live_idx = _resolve_live_slot_index(slots)
        if live_idx is None or slots[live_idx].kind != "llm_narrative":
            outputs_fb: list[SynthesisV2SlotOutput | None] = [None] * len(slots)
            sem_fb = asyncio.Semaphore(self._max_parallel_llm)
            bg_tasks_fb: list[asyncio.Task[None]] = []
            bg_queues_fb: list[asyncio.Queue[Any] | None] = []
            for i, slot in enumerate(slots):
                task, queue = self._start_background_slot(
                    index=i,
                    slot=slot,
                    outputs=outputs_fb,
                    sem=sem_fb,
                    query=query,
                    analysis_type=analysis_type,
                    data_mode=data_mode,
                    gathered_data=gathered_data,
                    context_snippets=context_snippets,
                    planning_context=planning_context,
                    chart_mode=chart_mode,
                    task_status=task_status,
                    report_context=report_context,
                )
                bg_tasks_fb.append(task)
                bg_queues_fb.append(queue)
            last_emit_at = time.monotonic()
            for i, slot in enumerate(slots):
                async for ev, last_emit_at in self._emit_slot_in_order(
                    index=i,
                    slot=slot,
                    outputs=outputs_fb,
                    task=bg_tasks_fb[i],
                    chunk_queue=bg_queues_fb[i],
                    last_emit_at=last_emit_at,
                ):
                    yield (ev, None)
            filled_fb = [o for o in outputs_fb if o is not None]
            yield ({}, self._assemble_result(filled_fb, analysis_type=analysis_type))
            return

        live_slot = slots[live_idx]
        outputs: list[SynthesisV2SlotOutput | None] = [None] * len(slots)
        sem = asyncio.Semaphore(self._max_parallel_llm)

        bg_tasks: list[asyncio.Task[None] | None] = [None] * len(slots)
        bg_queues: list[asyncio.Queue[Any] | None] = [None] * len(slots)
        for i, slot in enumerate(slots):
            if i == live_idx:
                continue
            task, queue = self._start_background_slot(
                index=i,
                slot=slot,
                outputs=outputs,
                sem=sem,
                query=query,
                analysis_type=analysis_type,
                data_mode=data_mode,
                gathered_data=gathered_data,
                context_snippets=context_snippets,
                planning_context=planning_context,
                chart_mode=chart_mode,
                task_status=task_status,
                report_context=report_context,
            )
            bg_tasks[i] = task
            bg_queues[i] = queue

        messages = self._llm_messages_for_slot(
            query=query,
            analysis_type=analysis_type,
            data_mode=data_mode,
            gathered_data=gathered_data,
            context_snippets=context_snippets,
            planning_context=planning_context,
            slot=live_slot,
            task_status=task_status,
            report_context=report_context,
        )
        header = f"### {live_slot.title}\n\n" if live_slot.title else ""
        stream_body_parts: list[str] = []
        last_emit_at = time.monotonic()
        if header:
            yield ({"event": "summary_delta", "text": header}, None)
            last_emit_at = time.monotonic()

        stream_iter = self._llm.stream_chat(
            model=None,
            messages=messages,
            timeout=float(self._synthesis_timeout),
            max_tokens=self._segment_max_tokens,
        )
        aiter = stream_iter.__aiter__()
        while True:
            try:
                chunk = await asyncio.wait_for(
                    aiter.__anext__(),
                    timeout=self._idle_heartbeat_seconds,
                )
            except StopAsyncIteration:
                break
            except asyncio.TimeoutError:
                elapsed_ms = int((time.monotonic() - last_emit_at) * 1000)
                yield (
                    self._loading_event(
                        active=True,
                        slot_id=live_slot.id,
                        slot_index=live_idx,
                        phase="waiting_token",
                        elapsed_ms=elapsed_ms,
                        hint=f"正在生成：{live_slot.title or live_slot.id}",
                    ),
                    None,
                )
                continue
            stream_body_parts.append(chunk)
            yield ({"event": "summary_delta", "text": chunk}, None)
            last_emit_at = time.monotonic()

        body = strip_leading_duplicate_heading("".join(stream_body_parts), live_slot.title)
        live_md = _wrap_narrative_markdown(live_slot.title, body)
        outputs[live_idx] = SynthesisV2SlotOutput(
            live_slot.id,
            live_slot.kind,
            live_slot.title,
            live_md,
        )
        yield ({"event": "summary_delta", "text": "\n\n"}, None)
        yield (
            self._loading_event(active=False, slot_id=live_slot.id, slot_index=live_idx),
            None,
        )
        last_emit_at = time.monotonic()

        for i, slot in enumerate(slots):
            if i == live_idx:
                continue
            task = bg_tasks[i]
            if task is None:
                continue
            async for ev, last_emit_at in self._emit_slot_in_order(
                index=i,
                slot=slot,
                outputs=outputs,
                task=task,
                chunk_queue=bg_queues[i],
                last_emit_at=last_emit_at,
            ):
                yield (ev, None)

        filled = [o for o in outputs if o is not None]
        result = self._assemble_result(filled, analysis_type=analysis_type)
        yield ({}, result)
