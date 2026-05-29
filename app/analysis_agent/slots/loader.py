from __future__ import annotations

from typing import Any

from app.analysis_agent.slots.kinds import AnalysisAgentSlot
from app.analysis_agent.slots.specs import default_plan_version, normalize_template_version
from app.llm.prompt_registry import PromptTemplateRegistry


def load_agent_slots(
    analysis_type: str,
    *,
    version: str | None = None,
    prompts: PromptTemplateRegistry | None = None,
    validate_plan_refs: bool = True,
) -> list[AnalysisAgentSlot]:
    """加载章节列表；委托 load_analysis_run_context（report JSON 为事实源）。"""
    from app.analysis_agent.context_loader import load_analysis_run_context

    ver = normalize_template_version(version or default_plan_version(analysis_type))
    ctx = load_analysis_run_context(
        analysis_type, version=ver, prompts=prompts, validate_plan_refs=validate_plan_refs
    )
    return list(ctx.slots)


def _validate_against_plan(
    analysis_type: str,
    version: str,
    slots: list[AnalysisAgentSlot],
    prompts: PromptTemplateRegistry | None,
) -> None:
    from app.analysis_agent.plans.loader import load_plan_tasks

    try:
        tasks = load_plan_tasks(analysis_type, version=version, prompts=prompts)
    except ValueError:
        return
    plan_ids = {str(t["item_id"]) for t in tasks if t.get("item_id")}
    for slot in slots:
        for iid in slot.source_item_ids:
            if iid and iid not in plan_ids:
                raise ValueError(
                    f"slots_plan_mismatch:{analysis_type}:{slot.id}:unknown_item_id:{iid}"
                )
