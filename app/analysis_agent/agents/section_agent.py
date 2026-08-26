from __future__ import annotations

from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Any

from app.analysis_agent.agents.narrative_react import run_narrative_react
from app.analysis_agent.agents.section_prompt import build_section_user_prompt
from app.analysis_agent.agents.section_result import SectionSynthesisResult
from app.analysis_agent.plans.loader import get_synthesis_template
from app.analysis_agent.renderers import section_data as core
from app.analysis_agent.slots.kinds import AnalysisAgentSlot
from app.core.config import get_app_config
from app.core.logging import get_logger
from app.llm.client import VLLMHttpClient
from app.llm.prompt_registry import PromptTemplateRegistry

logger = get_logger(__name__)

CancelChecker = Callable[[], Awaitable[bool]]
DeltaCallback = Callable[[str], Awaitable[None] | None]


def _system_prompt(analysis_type: str, prompts: PromptTemplateRegistry) -> str:
    system_tpl, _scene = get_synthesis_template(analysis_type, prompts=prompts)
    default_system = "你是电站锅炉综合分析报告撰写专家。"
    if analysis_type == "overheat_guidance":
        default_system = "你是电站锅炉超温分析报告撰写专家。"
    elif (analysis_type or "").startswith("subsidence_"):
        default_system = "你是北京市地面沉降监测报告撰写专家。"
    return (system_tpl.content if system_tpl else "").strip() or default_system


def _should_use_react(*, use_react_agent: bool, slot: AnalysisAgentSlot) -> bool:
    """T2：仅 use_emit_tools 章允许 ReAct；纯叙述走 stream_chat。"""
    if not use_react_agent:
        return False
    return bool(slot.use_emit_tools)


def _build_user_prompt(
    *,
    slot: AnalysisAgentSlot,
    query: str,
    gathered_data: dict[str, list[dict]],
    context_snippets: list[str],
    task_status: dict[str, str] | None,
    intent_context: list[str] | None,
    prepared_viz_note: str = "",
) -> str:
    subset = core.resolve_data_subset(gathered_data, slot.source_item_ids, strict=True)
    coverage = core.build_data_coverage_note(subset, task_status=task_status)
    facts = core.build_audit_facts(subset, query, slot_id=slot.id, task_status=task_status)
    rag_lines = core.rag_snippets_for_slot(context_snippets, gathered_data, slot.source_item_ids)
    rag_block = ""
    if rag_lines:
        rag_block = "【RAG参考片段】\n" + "\n".join(f"- {s[:800]}" for s in rag_lines)
    return build_section_user_prompt(
        slot=slot,
        query=query,
        coverage=coverage,
        facts=facts,
        rag_block=rag_block,
        intent_context=intent_context,
        prepared_viz_note=prepared_viz_note,
    )


async def _emit_delta(on_delta: DeltaCallback | None, text: str) -> None:
    if not on_delta or not text:
        return
    maybe = on_delta(text)
    if maybe is not None and hasattr(maybe, "__await__"):
        await maybe  # type: ignore[misc]


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
    use_react_agent: bool = False,
    intent_context: list[str] | None = None,
    on_delta: DeltaCallback | None = None,
    cancel_checker: CancelChecker | None = None,
    prepared_viz_note: str = "",
) -> SectionSynthesisResult:
    """
    llm_section 合成。

    - 默认 / 非 emit 章：stream_chat，经 on_delta 真流式推送
    - use_react_agent 且 use_emit_tools：ReAct（整章完成后一次 on_delta 正文，避免假切片主路径）
    """
    user_prompt = _build_user_prompt(
        slot=slot,
        query=query,
        gathered_data=gathered_data,
        context_snippets=context_snippets,
        task_status=task_status,
        intent_context=intent_context,
        prepared_viz_note=prepared_viz_note,
    )

    if _should_use_react(use_react_agent=use_react_agent, slot=slot) and hybrid_rag is not None:
        try:
            result = await run_narrative_react(
                prompts=prompts,
                slot=slot,
                query=query,
                gathered_data=gathered_data,
                context_snippets=context_snippets,
                task_status=task_status,
                hybrid_rag=hybrid_rag,
                analysis_type=analysis_type,
                user_prompt_override=user_prompt,
                include_emit_tools=True,
            )
            if result.markdown:
                await _emit_delta(on_delta, result.markdown)
            return result
        except Exception as exc:  # noqa: BLE001
            logger.warning("section react failed, fallback stream_chat: %s", exc)

    client = llm_client or VLLMHttpClient()
    cfg = get_app_config().analysis_agent
    messages = [
        {"role": "system", "content": _system_prompt(analysis_type, prompts)},
        {"role": "user", "content": user_prompt},
    ]
    buf = ""
    n = 0
    async for chunk in client.stream_chat(messages=messages, max_tokens=cfg.narrative_max_tokens):
        if cancel_checker is not None and n % 8 == 0:
            try:
                if await cancel_checker():
                    break
            except Exception:  # noqa: BLE001
                pass
        if chunk:
            buf += chunk
            n += 1
            await _emit_delta(on_delta, chunk)
    body = buf.strip() or "（待补充）"
    return SectionSynthesisResult(markdown=body)


async def iter_synthesize_deltas(
    **kwargs: Any,
) -> AsyncIterator[str]:
    """测试/联调用：仅产出文本增量。"""
    chunks: list[str] = []

    async def _cap(text: str) -> None:
        chunks.append(text)

    await synthesize_section(**kwargs, on_delta=_cap)
    for c in chunks:
        yield c


def section_result_to_slot_output(
    slot: AnalysisAgentSlot,
    result: SectionSynthesisResult,
    *,
    already_streamed: bool = False,
) -> tuple[Any, list[str]]:
    """
    将 SectionSynthesisResult 转为 SlotOutput。

    already_streamed=True：正文已通过 on_delta 推送，不再返回假切片 chunks（避免重复 SSE）。
    """
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
    if already_streamed:
        return out, []
    chunk_size = max(1, int(get_app_config().analysis_agent.stream_chunk_chars))
    chunks = [wrapped[i : i + chunk_size] for i in range(0, len(wrapped), chunk_size)] or [wrapped]
    return out, chunks
