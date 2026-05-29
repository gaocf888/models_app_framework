from __future__ import annotations

from typing import Any, AsyncIterator

from app.analysis_agent.agents.section_agent import section_result_to_slot_output, synthesize_section
from app.analysis_agent.slots.kinds import AnalysisAgentSlot
from app.core.logging import get_logger
from app.llm.client import VLLMHttpClient
from app.llm.prompt_registry import PromptTemplateRegistry

logger = get_logger(__name__)


async def stream_narrative_slot(
    *,
    llm_client: Any,
    prompts: PromptTemplateRegistry,
    slot: AnalysisAgentSlot,
    query: str,
    gathered_data: dict[str, list[dict]],
    context_snippets: list[str],
    task_status: dict[str, str] | None,
    max_tokens: int,
    gathered_json_max_chars: int,
    use_react_agent: bool = True,
    react_max_iterations: int = 8,
    hybrid_rag: Any | None = None,
    analysis_type: str = "overheat_guidance",
) -> AsyncIterator[str]:
    """
    叙述/章节槽：ReAct（可含 emit 工具）或 stream_chat；按块 yield markdown。
    """
    _ = max_tokens, gathered_json_max_chars, react_max_iterations
    is_section = slot.kind in ("llm_section", "llm_narrative")
    if not is_section:
        is_section = slot.kind == "llm_narrative"

    try:
        result = await synthesize_section(
            prompts=prompts,
            slot=slot,
            query=query,
            gathered_data=gathered_data,
            context_snippets=context_snippets,
            task_status=task_status,
            hybrid_rag=hybrid_rag,
            analysis_type=analysis_type,
            llm_client=llm_client if isinstance(llm_client, VLLMHttpClient) else None,
            use_react_agent=use_react_agent and hybrid_rag is not None,
        )
        _out, chunks = section_result_to_slot_output(slot, result)
        for piece in chunks:
            yield piece
        return
    except Exception as exc:  # noqa: BLE001
        logger.warning("stream_narrative_slot failed: %s", exc)
        yield "（待补充）"
