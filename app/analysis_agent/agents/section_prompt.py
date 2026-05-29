from __future__ import annotations

from app.analysis_agent.slots.kinds import AnalysisAgentSlot


def build_section_user_prompt(
    *,
    slot: AnalysisAgentSlot,
    query: str,
    coverage: str,
    facts: str,
    rag_block: str = "",
    intent_context: list[str] | None = None,
) -> str:
    """由槽位蓝图拼装 user prompt（outline / constraints / field_hints / 意图 RAG）。"""
    parts: list[str] = [f"用户问题：{query}"]
    if intent_context:
        lines = [f"- {s[:800]}" for s in intent_context[:8] if (s or "").strip()]
        if lines:
            parts.append("【意图与业务背景（intent_rag）】\n" + "\n".join(lines))
    if coverage:
        parts.append(coverage)
    if facts:
        parts.append(facts)
    if slot.outline:
        parts.append("【本章结构 outline】\n" + "\n".join(f"- {line}" for line in slot.outline))
    if slot.field_hints:
        hints = "\n".join(f"- {label}：{hint}" for label, hint in slot.field_hints if label)
        if hints:
            parts.append("【字段说明 field_hints】\n" + hints)
    if slot.constraints:
        parts.append("【硬约束 constraints】\n" + "\n".join(f"- {c}" for c in slot.constraints))
    task = slot.narrative_instruction.strip()
    if task:
        parts.append(f"【本章写作任务】\n{task}")
    if slot.use_emit_tools and slot.allowed_outputs:
        allowed = "、".join(slot.allowed_outputs)
        parts.append(
            "【呈现工具】需要表格或图表时，必须调用 emit_markdown_table / emit_chart，"
            f"不要手写宽表 Markdown。允许：{allowed}。"
            "数值仅可来自 get_slot_data；撰写正文前请先调用 get_slot_data。"
        )
    elif slot.kind == "llm_section":
        parts.append(
            "请先调用 get_slot_data 核对数值，再输出本章正文（Markdown 段落/列表，勿输出 # 标题行）。"
        )
    else:
        parts.append("请调用 get_slot_data 核对数值后输出本章正文（勿输出 # 标题行）。")
    if rag_block:
        parts.append(rag_block)
    return "\n\n".join(p for p in parts if p)
