from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from app.analysis_agent.slots.builder import slots_from_spec_dict
from app.analysis_agent.slots.kinds import AnalysisAgentSlot
from app.analysis_agent.slots.specs import normalize_template_version

_REPORTS_DIR = Path(__file__).resolve().parents[2] / "configs" / "analysis_agent_reports"


def _load_report_json(*, analysis_type: str, version: str) -> dict | None:
    """从 configs/analysis_agent_reports/{type}.{version}.json 加载报告规格。"""
    ver = normalize_template_version(version)
    path = _REPORTS_DIR / f"{analysis_type}.{ver}.json"
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return None


class ReportSpecPlanItemModel(BaseModel):
    item_id: str
    question: str = ""
    purpose: str = ""
    mandatory: bool = False
    dependency_ids: list[str] = Field(default_factory=list)


class ReportSpecFileModel(BaseModel):
    """报告规格 JSON 结构校验（加载时可选）。"""

    schema_version: int = 1
    title: str = ""
    description: str = ""
    plan: dict[str, Any] | None = None
    plan_items: list[dict[str, Any]] | None = None
    chapters: list[dict[str, Any]] | None = None
    slots: list[dict[str, Any]] | None = None


@dataclass(frozen=True)
class ReportSpec:
    analysis_type: str
    version: str
    title: str
    plan_items: tuple[dict[str, Any], ...]
    chapters: tuple[AnalysisAgentSlot, ...]
    description: str = ""

    @property
    def plan_tasks(self) -> list[dict[str, Any]]:
        return [dict(x) for x in self.plan_items]


def load_report_spec(
    analysis_type: str,
    *,
    version: str | None = None,
) -> ReportSpec | None:
    ver = normalize_template_version(version)
    raw = _load_report_json(analysis_type=analysis_type, version=ver)
    if raw is None:
        return None
    try:
        ReportSpecFileModel.model_validate(raw)
    except Exception:  # noqa: BLE001
        pass
    plan_items: list[dict[str, Any]] = []
    plan_raw = raw.get("plan")
    if isinstance(plan_raw, dict):
        items = plan_raw.get("items")
        if isinstance(items, list):
            for item in items:
                if isinstance(item, dict) and item.get("item_id"):
                    plan_items.append(dict(item))
    elif isinstance(raw.get("plan_items"), list):
        for item in raw["plan_items"]:
            if isinstance(item, dict) and item.get("item_id"):
                plan_items.append(dict(item))
    chapters_raw = raw.get("chapters") or raw.get("slots")
    if not isinstance(chapters_raw, list) or not chapters_raw:
        return None
    spec_dict = {"slots": chapters_raw}
    chapters = tuple(slots_from_spec_dict(spec_dict))
    return ReportSpec(
        analysis_type=analysis_type,
        version=ver,
        title=str(raw.get("title") or ""),
        plan_items=tuple(plan_items),
        chapters=chapters,
        description=str(raw.get("description") or ""),
    )


def report_spec_available(analysis_type: str, *, version: str | None = None) -> bool:
    return load_report_spec(analysis_type, version=version) is not None
