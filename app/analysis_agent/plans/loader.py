from __future__ import annotations

import json
from typing import Any

from app.analysis_agent.report_spec import load_report_spec
from app.analysis_agent.slots.specs import (
    default_plan_version,
    get_default_agent_template_version,
    narrative_scene_for_type,
    normalize_template_version,
)
from app.llm.prompt_registry import PromptTemplateRegistry


def _plan_scene(analysis_type: str) -> str:
    return f"analysis_agent_plan_{analysis_type}"


def synthesis_scene_candidates(analysis_type: str) -> list[str]:
    """叙述模板 scene（仅 analysis_agent_synthesis_*）。"""
    return [narrative_scene_for_type(analysis_type)]


def resolve_plan_template(
    analysis_type: str,
    *,
    version: str | None = None,
    prompts: PromptTemplateRegistry | None = None,
) -> tuple[str, str, str]:
    """返回 (scene, version, content)。"""
    reg = prompts or PromptTemplateRegistry()
    ver = normalize_template_version(version or default_plan_version(analysis_type))
    scene = _plan_scene(analysis_type)
    default_ver = get_default_agent_template_version()
    tried: list[str] = []
    for v in (ver, default_ver, "v1"):
        if not v or v in tried:
            continue
        tried.append(v)
        tpl = reg.get_template(scene=scene, version=v)
        if tpl and (tpl.content or "").strip():
            return scene, ver, tpl.content
    raise ValueError(f"missing_plan_template:{scene}:{ver}")


def get_synthesis_template(
    analysis_type: str,
    *,
    version: str | None = None,
    prompts: PromptTemplateRegistry | None = None,
) -> tuple[Any | None, str]:
    """返回 (template, scene_used)。"""
    reg = prompts or PromptTemplateRegistry()
    ver = normalize_template_version(version or default_plan_version(analysis_type))
    default_ver = get_default_agent_template_version()
    tried: list[str] = []
    for v in (ver, default_ver, "v1"):
        if not v or v in tried:
            continue
        tried.append(v)
        for scene in synthesis_scene_candidates(analysis_type):
            tpl = reg.get_template(scene=scene, version=v)
            if tpl and (tpl.content or "").strip():
                return tpl, scene
    return None, narrative_scene_for_type(analysis_type)


def load_plan_tasks(
    analysis_type: str,
    *,
    version: str | None = None,
    prompts: PromptTemplateRegistry | None = None,
) -> list[dict[str, Any]]:
    """加载 NL2SQL 数据计划：优先 report_spec.plan.items，否则 analysis_agent_plan_*。"""
    ver = normalize_template_version(version or default_plan_version(analysis_type))
    spec = load_report_spec(analysis_type, version=ver)
    if spec is not None and spec.plan_tasks:
        return spec.plan_tasks
    _scene, _ver, content = resolve_plan_template(
        analysis_type, version=version, prompts=prompts
    )
    raw = json.loads(content)
    if not isinstance(raw, list):
        raise ValueError(f"invalid_plan_template:{analysis_type}:expected_list")
    tasks: list[dict[str, Any]] = []
    for item in raw:
        if isinstance(item, dict) and item.get("item_id"):
            tasks.append(dict(item))
    return tasks


def plan_tasks_for_slot(
    all_tasks: list[dict[str, Any]],
    source_item_ids: tuple[str, ...],
) -> list[dict[str, Any]]:
    if not source_item_ids:
        return []
    wanted = set(source_item_ids)
    return [t for t in all_tasks if str(t.get("item_id")) in wanted]


def effective_plan_version(analysis_type: str, options: dict[str, Any] | None) -> str:
    opts = options or {}
    explicit = str(opts.get("plan_template_version") or "").strip()
    if explicit:
        return normalize_template_version(explicit)
    return default_plan_version(analysis_type)
