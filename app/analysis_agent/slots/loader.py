from __future__ import annotations

import json
from pathlib import Path

from app.analysis_agent.plans.loader import load_plan_tasks
from app.analysis_agent.slots.builder import slots_from_spec_dict
from app.analysis_agent.slots.kinds import AnalysisAgentSlot
from app.analysis_agent.slots.specs import default_plan_version, normalize_template_version
from app.llm.prompt_registry import PromptTemplateRegistry

_SLOTS_CONFIG_DIR = Path(__file__).resolve().parents[3] / "configs" / "analysis_agent_slots"


def _slots_scene(analysis_type: str) -> str:
    return f"analysis_agent_slots_{analysis_type}"


def _load_spec_json(*, analysis_type: str, version: str) -> dict | None:
    reg = PromptTemplateRegistry()
    scene = _slots_scene(analysis_type)
    ver = normalize_template_version(version)
    tpl = reg.get_template(scene=scene, version=ver)
    if tpl and (tpl.content or "").strip():
        raw = json.loads(tpl.content)
        if isinstance(raw, dict):
            return raw
    path = _SLOTS_CONFIG_DIR / f"{analysis_type}.{ver}.json"
    legacy_v2 = _SLOTS_CONFIG_DIR / f"{analysis_type}.v2.json"
    if not path.exists() and legacy_v2.exists() and ver == "v1":
        path = legacy_v2
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return None


def load_agent_slots(
    analysis_type: str,
    *,
    version: str | None = None,
    prompts: PromptTemplateRegistry | None = None,
    validate_plan_refs: bool = True,
) -> list[AnalysisAgentSlot]:
    """已统一为 report_spec.chapters；保留本函数供脚本/测试，内部委托 context_loader。"""
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
