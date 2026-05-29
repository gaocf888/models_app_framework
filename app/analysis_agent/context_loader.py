from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.analysis_agent.plans.loader import load_plan_tasks
from app.analysis_agent.report_spec import ReportSpec, load_report_spec
from app.analysis_agent.slots.kinds import AnalysisAgentSlot
from app.analysis_agent.slots.specs import SUPPORTED_ANALYSIS_TYPES, normalize_template_version
from app.llm.prompt_registry import PromptTemplateRegistry


@dataclass(frozen=True)
class AnalysisRunContext:
    analysis_type: str
    plan_template_version: str
    plan_tasks: list[dict[str, Any]]
    slots: list[AnalysisAgentSlot]
    report_title: str = ""
    from_report_spec: bool = False


def load_analysis_run_context(
    analysis_type: str,
    *,
    version: str | None = None,
    prompts: PromptTemplateRegistry | None = None,
    validate_plan_refs: bool = True,
) -> AnalysisRunContext:
    """
    加载报告运行上下文：章节来自 configs/analysis_agent_reports/{type}.v1.json；
    数据计划优先 report 内 plan.items，否则回退 analysis_agent_plan_{type}（prompts.yaml）。
    """
    ver = normalize_template_version(version)
    if analysis_type not in SUPPORTED_ANALYSIS_TYPES:
        raise ValueError(f"unsupported_analysis_type:{analysis_type}")

    spec: ReportSpec | None = load_report_spec(analysis_type, version=ver)
    if spec is None:
        raise ValueError(f"missing_report_spec:{analysis_type}:{ver}")

    plan_tasks = list(spec.plan_tasks)
    if not plan_tasks:
        plan_tasks = load_plan_tasks(analysis_type, version=ver, prompts=prompts)

    slots = list(spec.chapters)
    if validate_plan_refs:
        _validate_slots_plan_refs(analysis_type, ver, slots, plan_tasks)

    return AnalysisRunContext(
        analysis_type=analysis_type,
        plan_template_version=spec.version,
        plan_tasks=plan_tasks,
        slots=slots,
        report_title=spec.title,
        from_report_spec=True,
    )


def _validate_slots_plan_refs(
    analysis_type: str,
    version: str,
    slots: list[AnalysisAgentSlot],
    plan_tasks: list[dict[str, Any]],
) -> None:
    plan_ids = {str(t["item_id"]) for t in plan_tasks if t.get("item_id")}
    for slot in slots:
        for iid in slot.source_item_ids:
            if iid and iid not in plan_ids:
                raise ValueError(
                    f"report_plan_mismatch:{analysis_type}:{version}:{slot.id}:unknown_item_id:{iid}"
                )
