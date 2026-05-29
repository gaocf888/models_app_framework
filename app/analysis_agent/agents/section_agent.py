from __future__ import annotations

from typing import Any

from app.analysis_agent.agents.narrative_react import run_narrative_react
from app.analysis_agent.agents.section_result import SectionSynthesisResult
from app.analysis_agent.plans.loader import get_synthesis_template
from app.analysis_agent.agents.section_prompt import build_section_user_prompt
from app.analysis_agent.renderers import section_data as core
from app.analysis_agent.slots.kinds import AnalysisAgentSlot
from app.analysis_agent.tools.agent_tools import build_narrative_tools
from app.analysis_agent.tools.slot_context import reset_slot_tool_context, set_slot_tool_context
from app.core.config import get_app_config
from app.core.logging import get_logger
from app.llm.client import VLLMHttpClient
from app.llm.prompt_registry import PromptTemplateRegistry

logger = get_logger(__name__)


def _system_prompt(analysis_type: str, prompts: PromptTemplateRegistry) -> str:
    system_tpl, _scene = get_synthesis_template(analysis_type, prompts=prompts)
    default_system = "你是电站锅炉综合分析报告撰写专家。"
    if analysis_type == "overheat_guidance":
        default_system = "你是电站锅炉超温分析报告撰写专家。"
    return (system_tpl.content if system_tpl else "").strip() or default_system


async def synthesize_section(
    *,
    prompts: PromptTemplateRegistry,
    slot: AnalysisAgentSlot,
    query: str,
    gathered_data: dict[str, list[dict]],
    context_snippets: list[str],
    task_status: dict[str, str] | None,
    hybrid_rag: Any | None,
    analysis_type: str,
    llm_client: VLLMHttpClient | None = None,
    use_react_agent: bool = True,
    intent_context: list[str] | None = None,
) -> SectionSynthesisResult:
    """llm_section / 带 emit 工具的章节合成。"""
    subset = core.resolve_data_subset(gathered_data, slot.source_item_ids, strict=True)
    coverage = core.build_data_coverage_note(subset, task_status=task_status)
    facts = core.build_audit_facts(subset, query, slot_id=slot.id, task_status=task_status)
    rag_lines = core.rag_snippets_for_slot(context_snippets, gathered_data, slot.source_item_ids)
    rag_block = ""
    if rag_lines:
        rag_block = "【RAG参考片段】\n" + "\n".join(f"- {s[:800]}" for s in rag_lines)
    user_prompt = build_section_user_prompt(
        slot=slot,
        query=query,
        coverage=coverage,
        facts=facts,
        rag_block=rag_block,
        intent_context=intent_context,
    )
    include_emit = bool(slot.use_emit_tools or slot.kind == "llm_section")

    if use_react_agent and hybrid_rag is not None:
        try:
            return await run_narrative_react(
                prompts=prompts,
                slot=slot,
                query=query,
                gathered_data=gathered_data,
                context_snippets=context_snippets,
                task_status=task_status,
                hybrid_rag=hybrid_rag,
                analysis_type=analysis_type,
                user_prompt_override=user_prompt,
                include_emit_tools=include_emit,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("section react failed, fallback stream_chat: %s", exc)

    client = llm_client or VLLMHttpClient()
    cfg = get_app_config().analysis_agent
    messages = [
        {"role": "system", "content": _system_prompt(analysis_type, prompts)},
        {"role": "user", "content": user_prompt},
    ]
    buf = ""
    async for chunk in client.stream_chat(messages=messages, max_tokens=cfg.narrative_max_tokens):
        if chunk:
            buf += chunk
    body = buf.strip() or "（待补充）"
    return SectionSynthesisResult(markdown=body)


def section_result_to_slot_output(
    slot: AnalysisAgentSlot,
    result: SectionSynthesisResult,
) -> tuple[Any, list[str]]:
    """将 SectionSynthesisResult 转为 SlotOutput 与流式 chunks。"""
    from app.analysis_agent.renderers.section_data import (
        sanitize_report_narrative,
        wrap_narrative_markdown,
    )
    from app.analysis_agent.slots.kinds import SlotOutput

    md_parts = [sanitize_report_narrative(result.markdown)]
    for extra in result.table_markdowns:
        if extra and extra.strip():
            md_parts.append(extra.strip())
    body = "\n\n".join(p for p in md_parts if p)
    title = slot.title.strip()
    wrapped = wrap_narrative_markdown(title, body) if title else body + "\n\n"
    table = result.tables[0] if result.tables else None
    charts = list(result.charts)
    if table and slot.chart_when_table and not charts:
        from app.analysis_agent.renderers.charts_extra import chart_from_table

        extra = chart_from_table(
            table_id=table.get("id") or slot.table_id,
            table_kind=table.get("table_kind") or slot.table_kind,
            table=table,
            title=title or slot.title,
        )
        if extra:
            charts.append(extra)
    out = SlotOutput(
        slot.id,
        slot.kind,
        slot.title,
        wrapped,
        table=table,
        chart=charts[0] if charts else None,
        charts=charts,
    )
    chunk_size = 256
    chunks = [wrapped[i : i + chunk_size] for i in range(0, len(wrapped), chunk_size)] or [wrapped]
    return out, chunks
