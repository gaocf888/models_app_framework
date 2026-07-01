"""看图诊断 scope 入口探针：判定 Path2（scope 先行）或 Path1（先视觉后 scope）。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from app.llm.graphs.img_diag_scope_intent import (
    missing_required_scope_fields,
    parse_img_diag_scope_draft,
    scope_dict_for_validate,
)
from app.llm.graphs.img_diag_scope_validate import validate_scope_in_catalog

ImgDiagScopeRoute = Literal["path2", "path1"]


@dataclass(frozen=True)
class ImgDiagScopeProbeResult:
    """首次 scope 探针结果（不进入 HITL、不触发匹配成功确认）。"""

    route: ImgDiagScopeRoute
    draft_dict: dict[str, Any]
    scope_cumulative_text: str
    missing_fields: list[str]
    db_match_count: int
    validation_error: str | None


async def probe_img_diag_scope_route(
    scope_question: str,
    *,
    llm_client: Any | None = None,
    prompt_registry: Any | None = None,
) -> ImgDiagScopeProbeResult:
    """
    Path2：机组+受热面齐全且库表校验 count>0。
    Path1：缺必填 scope 或库表校验未通过（有图时由编排层先跑视觉臂）。
    """
    q = (scope_question or "").strip()
    draft = parse_img_diag_scope_draft(
        q,
        llm_client=llm_client,
        prompt_registry=prompt_registry,
    )
    missing = missing_required_scope_fields(draft)
    draft_dict = draft.to_dict()
    if missing:
        return ImgDiagScopeProbeResult(
            route="path1",
            draft_dict=draft_dict,
            scope_cumulative_text=q,
            missing_fields=missing,
            db_match_count=0,
            validation_error=f"missing:{','.join(missing)}",
        )

    scope = scope_dict_for_validate(draft_dict)
    count, err = await validate_scope_in_catalog(scope)
    if count > 0:
        return ImgDiagScopeProbeResult(
            route="path2",
            draft_dict=draft_dict,
            scope_cumulative_text=q,
            missing_fields=[],
            db_match_count=count,
            validation_error=None,
        )
    return ImgDiagScopeProbeResult(
        route="path1",
        draft_dict=draft_dict,
        scope_cumulative_text=q,
        missing_fields=[],
        db_match_count=0,
        validation_error=err or "scope_not_found_in_catalog",
    )
