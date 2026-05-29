"""各 analysis_type 的 plan 版本、叙述 scene 与槽位规格。"""

from __future__ import annotations

from typing import Any

# 综合分析智能体统一模板版本（多槽位流水线，不再区分 v1/v2）
DEFAULT_AGENT_TEMPLATE_VERSION = "v1"

PLAN_VERSION_BY_TYPE: dict[str, str] = {
    "overheat_guidance": DEFAULT_AGENT_TEMPLATE_VERSION,
    "maintenance_strategy": DEFAULT_AGENT_TEMPLATE_VERSION,
    "four_tube_health_interpretation": DEFAULT_AGENT_TEMPLATE_VERSION,
    "leakage_burst_analysis": DEFAULT_AGENT_TEMPLATE_VERSION,
}

NARRATIVE_SCENE_BY_TYPE: dict[str, str] = {
    "overheat_guidance": "analysis_agent_synthesis_narrative",
    "maintenance_strategy": "analysis_agent_synthesis_maintenance_strategy",
    "four_tube_health_interpretation": "analysis_agent_synthesis_four_tube_health_interpretation",
    "leakage_burst_analysis": "analysis_agent_synthesis_leakage_burst_analysis",
}

SUPPORTED_ANALYSIS_TYPES: tuple[str, ...] = tuple(PLAN_VERSION_BY_TYPE.keys())


def default_plan_version(analysis_type: str) -> str:
    return PLAN_VERSION_BY_TYPE.get(analysis_type, DEFAULT_AGENT_TEMPLATE_VERSION)


def default_slot_version(analysis_type: str) -> str:
    return default_plan_version(analysis_type)


def narrative_scene_for_type(analysis_type: str) -> str:
    return NARRATIVE_SCENE_BY_TYPE.get(analysis_type, "analysis_agent_synthesis_narrative")


def normalize_template_version(version: str | None) -> str:
    """将历史 v2 或空值规范为统一多槽位版本 v1。"""
    v = (version or "").strip().lower()
    if not v or v == "v2":
        return DEFAULT_AGENT_TEMPLATE_VERSION
    return v


def _slot(
    id: str,
    kind: str,
    *,
    title: str = "",
    source_item_ids: tuple[str, ...] = (),
    narrative_instruction: str = "",
    table_id: str = "",
    static_body: str = "",
    table_kind: str | None = None,
    allow_human_confirm: bool = False,
    stream_live: bool = False,
) -> dict[str, Any]:
    return {
        "id": id,
        "kind": kind,
        "title": title,
        "source_item_ids": source_item_ids,
        "narrative_instruction": narrative_instruction,
        "table_id": table_id or id,
        "template_id": "",
        "static_body": static_body,
        "table_kind": table_kind,
        "chart_when_table": table_kind in ("classification", "proportion"),
        "mandatory_data": allow_human_confirm,
        "allow_human_confirm": allow_human_confirm,
        "stream_live": stream_live,
    }


# 检修策略：q0 主表 + 分章叙述 + 佐证表
_MAINTENANCE_SLOTS: list[dict[str, Any]] = [
    _slot("m_hdr", "static_markdown", static_body="# 锅炉检修策略分析报告\n\n"),
    _slot(
        "m_q0",
        "table_deterministic",
        title="统一检修优先级汇总",
        source_item_ids=("q0",),
        table_id="maint_q0_priority",
        table_kind="classification",
        allow_human_confirm=True,
    ),
    _slot(
        "m_risk",
        "llm_narrative",
        title="一、风险与寿命评估",
        source_item_ids=("q0", "q1", "q2", "q3", "q4"),
        narrative_instruction=(
            "撰写「一、风险与寿命评估」：优先引用统一检修优先级汇总；"
            "用测厚、缺陷、超温、泄爆等佐证表补充；禁止出现 q0～q5 编号。"
        ),
    ),
    _slot(
        "m_q1",
        "table_deterministic",
        title="测厚与换管数据汇总",
        source_item_ids=("q1",),
        table_id="maint_q1_thickness",
    ),
    _slot(
        "m_plan",
        "llm_narrative",
        title="二、检修策略",
        source_item_ids=("q0", "q1", "q2", "q3", "q4", "q5"),
        narrative_instruction=(
            "撰写「二、检修策略」：含 2.1 检修优先级清单（1/2/3 级）与 2.2 方案影响分析；"
            "以统一检修优先级汇总为主依据。"
        ),
        stream_live=True,
    ),
    _slot(
        "m_q2",
        "table_deterministic",
        title="历史遗留问题汇总",
        source_item_ids=("q2",),
        table_id="maint_q2_defects",
    ),
    _slot(
        "m_q3",
        "table_deterministic",
        title="超温与运行参数汇总",
        source_item_ids=("q3",),
        table_id="maint_q3_overheat",
    ),
    _slot(
        "m_q4",
        "table_deterministic",
        title="泄爆数据汇总",
        source_item_ids=("q4",),
        table_id="maint_q4_leakage",
    ),
    _slot(
        "m_q5",
        "table_deterministic",
        title="综合时间轴事件汇总",
        source_item_ids=("q5",),
        table_id="maint_q5_timeline",
    ),
]

# 四管健康解读
_FOUR_TUBE_SLOTS: list[dict[str, Any]] = [
    _slot("f_hdr", "static_markdown", static_body="# 四管健康报告智能解读\n\n"),
    _slot(
        "f_q1",
        "table_deterministic",
        title="健康/风险/寿命评估结果",
        source_item_ids=("q1",),
        table_id="four_tube_q1_health",
        table_kind="classification",
        allow_human_confirm=True,
    ),
    _slot(
        "f_brief",
        "llm_narrative",
        title="一、运行简报",
        source_item_ids=("q1", "q2", "q3"),
        narrative_instruction="撰写「运行简报」：整体健康与风险、3～6 条要点、本周运行关注。",
    ),
    _slot(
        "f_q2",
        "table_deterministic",
        title="测厚与缺陷明细",
        source_item_ids=("q2",),
        table_id="four_tube_q2_thickness",
    ),
    _slot(
        "f_plan",
        "llm_narrative",
        title="二、检修建议书",
        source_item_ids=("q1", "q2", "q3", "q4", "q5", "q6"),
        narrative_instruction="撰写「检修建议书」：分条列出测厚复核、更换/消缺、可延期项；无数据写待补。",
        stream_live=True,
    ),
    _slot(
        "f_q3",
        "table_deterministic",
        title="超温佐证",
        source_item_ids=("q3",),
        table_id="four_tube_q3_overheat",
    ),
    _slot(
        "f_q4",
        "table_deterministic",
        title="泄爆履历",
        source_item_ids=("q4",),
        table_id="four_tube_q4_leakage",
    ),
    _slot(
        "f_limit",
        "llm_narrative",
        title="三、风险与限制",
        source_item_ids=("q1", "q2", "q3", "q4", "q5", "q6"),
        narrative_instruction="撰写数据缺口、样本不足、模型口径未知等限制说明。",
    ),
]

# 泄爆分析
_LEAKAGE_SLOTS: list[dict[str, Any]] = [
    _slot("l_hdr", "static_markdown", static_body="# 锅炉受热面泄爆分析报告\n\n"),
    _slot(
        "l_summary",
        "llm_narrative",
        title="一、结论摘要",
        source_item_ids=("q1", "q2", "q3"),
        narrative_instruction="撰写结论摘要 2～4 条：泄爆/泄漏模式、集中区域、紧迫性。",
        stream_live=True,
    ),
    _slot(
        "l_q1",
        "table_deterministic",
        title="泄爆/泄漏履历",
        source_item_ids=("q1",),
        table_id="leak_q1_events",
        table_kind="classification",
        allow_human_confirm=True,
    ),
    _slot(
        "l_events",
        "llm_narrative",
        title="二、泄爆/泄漏事件与履历",
        source_item_ids=("q1", "q2"),
        narrative_instruction="按时间或区域归纳关键事件；无记录须明确说明。",
    ),
    _slot(
        "l_q2",
        "table_deterministic",
        title="测厚与缺陷明细",
        source_item_ids=("q2",),
        table_id="leak_q2_defects",
    ),
    _slot(
        "l_causes",
        "llm_narrative",
        title="三、关键依据与可能原因",
        source_item_ids=("q1", "q2", "q3", "q4"),
        narrative_instruction="逐条引用事实；给出至少 2 类原因并标注置信度。",
    ),
    _slot(
        "l_q3",
        "table_deterministic",
        title="超温佐证",
        source_item_ids=("q3",),
        table_id="leak_q3_overheat",
    ),
    _slot(
        "l_advice",
        "llm_narrative",
        title="四、处置与防控建议",
        source_item_ids=("q1", "q2", "q3", "q4", "q5", "q6"),
        narrative_instruction="按立即/短期/持续跟踪给出可执行建议。",
    ),
    _slot(
        "l_q4",
        "table_deterministic",
        title="壁厚减薄速率",
        source_item_ids=("q4",),
        table_id="leak_q4_thinning",
    ),
]

SLOT_SPECS_BY_TYPE: dict[str, list[dict[str, Any]]] = {
    "maintenance_strategy": _MAINTENANCE_SLOTS,
    "four_tube_health_interpretation": _FOUR_TUBE_SLOTS,
    "leakage_burst_analysis": _LEAKAGE_SLOTS,
}
