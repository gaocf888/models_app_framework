"""看图诊断 HITL resume 换图与视觉重跑。"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.llm.graphs.img_diag_hitl_images import (
    merge_hitl_image_urls_into_request,
    normalize_image_url_list,
    validate_hitl_image_urls_for_subtype,
)
from app.llm.graphs.img_diag_scope_graph import (
    ImgDiagScopeGraphState,
    _apply_hitl_image_urls_to_state,
    _build_interrupt_payload,
    _enrich_interrupt_payload_from_state,
    _finalize_state_after_hitl_resume,
)


def test_merge_hitl_image_urls_replaces_list() -> None:
    req = {"image_urls": ["http://old/a.jpg"], "img_diag_subtype": "defect_ident"}
    updated, changed = merge_hitl_image_urls_into_request(
        req,
        {"image_urls": ["http://new/b.jpg"]},
    )
    assert changed is True
    assert updated["image_urls"] == ["http://new/b.jpg"]


def test_merge_skips_when_unchanged() -> None:
    req = {"image_urls": ["http://same/a.jpg"]}
    updated, changed = merge_hitl_image_urls_into_request(
        req,
        {"image_urls": ["http://same/a.jpg"]},
    )
    assert changed is False
    assert updated is req


def test_validate_defect_ident_requires_image() -> None:
    assert validate_hitl_image_urls_for_subtype(
        img_diag_subtype="defect_ident",
        image_urls=[],
    )


def test_apply_hitl_image_urls_before_scope_fields() -> None:
    state: ImgDiagScopeGraphState = {
        "img_diag_request": {"image_urls": ["http://old/a.jpg"], "img_diag_subtype": "defect_ident"},
        "img_diag_subtype": "defect_ident",
        "scope_cumulative_text": "query",
    }
    _apply_hitl_image_urls_to_state(
        state,
        {
            "image_urls": ["http://new/b.jpg"],
            "user_supplement": "1号锅炉低温过热器",
        },
    )
    assert state["vision_images_replaced"] is True
    assert state["img_diag_request"]["image_urls"] == ["http://new/b.jpg"]


def test_include_vision_preview_when_images_replaced_on_round_two() -> None:
    payload: dict = {}
    state: ImgDiagScopeGraphState = {
        "orchestrator_path": "vision_first",
        "hitl_rounds": 2,
        "vision_images_replaced": True,
        "vision_prefetch_data": {"defect_type": "裂纹", "morphology_summary": "线状"},
        "img_diag_subtype": "defect_ident",
    }
    _enrich_interrupt_payload_from_state(state, payload)
    assert payload["include_vision_preview"] is True


@pytest.mark.asyncio
async def test_finalize_state_refreshes_vision_on_interrupt() -> None:
    session = MagicMock()
    session.img_diag_request = {"image_urls": ["http://old/a.jpg"], "img_diag_subtype": "defect_ident"}
    session.orchestrator_path = "vision_first"
    session.vision_prefetch = {"defect_type": "旧"}
    session.vision_prefetch_ms = 1
    session.vision_prefetch_status = "success"

    state = {
        "img_diag_request": {"image_urls": ["http://new/b.jpg"], "img_diag_subtype": "defect_ident"},
        "vision_images_replaced": True,
        "orchestrator_path": "vision_first",
    }

    async def _refresh(req: dict):
        assert req["image_urls"] == ["http://new/b.jpg"]
        return {"defect_type": "新"}, 99, "success"

    updated = await _finalize_state_after_hitl_resume(
        state,
        session=session,
        vision_refresh=_refresh,
        for_interrupt=True,
    )
    assert updated["image_urls"] == ["http://new/b.jpg"]
    assert state["vision_prefetch_data"]["defect_type"] == "新"
    assert state["vision_prefetch_ms"] == 99


@pytest.mark.asyncio
async def test_finalize_state_path2_confirm_skips_vision_refresh() -> None:
    session = MagicMock()
    session.img_diag_request = {"image_urls": ["http://old/a.jpg"], "img_diag_subtype": "defect_ident"}
    session.orchestrator_path = "scope_first"
    session.vision_prefetch = None
    session.vision_prefetch_ms = 0
    session.vision_prefetch_status = ""

    state = {
        "img_diag_request": {"image_urls": ["http://new/b.jpg"], "img_diag_subtype": "defect_ident"},
        "vision_images_replaced": True,
        "orchestrator_path": "scope_first",
    }
    refresh = AsyncMock(return_value=({"defect_type": "不应调用"}, 1, "success"))

    await _finalize_state_after_hitl_resume(
        state,
        session=session,
        vision_refresh=refresh,
        for_interrupt=False,
    )
    refresh.assert_not_called()


def test_interrupt_payload_round_two_no_vision_without_replace() -> None:
    state: ImgDiagScopeGraphState = {
        "orchestrator_path": "vision_first",
        "hitl_rounds": 2,
        "vision_images_replaced": False,
        "vision_prefetch_data": {"defect_type": "裂纹"},
        "img_diag_subtype": "defect_ident",
        "scope_draft": {},
        "missing_fields": [],
    }
    payload = _build_interrupt_payload(state)
    assert payload["include_vision_preview"] is False
