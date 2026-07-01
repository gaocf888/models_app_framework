"""看图诊断：视觉拒识 scope 确认门禁。"""

from __future__ import annotations

from app.llm.graphs.img_diag_scope_display import build_scope_hitl_confirm_reply_example
from app.llm.graphs.img_diag_vision_display import (
    VISION_HITL_REUPLOAD_PROMPT,
    VISION_REJECT_INTERRUPT_REASON,
    apply_vision_rejection_scope_gate,
    is_scope_confirm_blocked_by_vision,
)


def test_is_scope_confirm_blocked_when_vision_rejects() -> None:
    blocked = is_scope_confirm_blocked_by_vision(
        {"is_boiler_pressure_part_image": False},
        img_diag_request={"image_urls": ["http://a.jpg"], "img_diag_subtype": "defect_ident"},
        img_diag_subtype="defect_ident",
    )
    assert blocked is True


def test_is_scope_confirm_not_blocked_without_images() -> None:
    blocked = is_scope_confirm_blocked_by_vision(
        {"is_boiler_pressure_part_image": False},
        img_diag_request={"image_urls": [], "img_diag_subtype": "leakage_burst"},
        img_diag_subtype="leakage_burst",
    )
    assert blocked is False


def test_apply_vision_rejection_scope_gate_clears_confirm() -> None:
    state = {
        "img_diag_subtype": "defect_ident",
        "img_diag_request": {"image_urls": ["http://a.jpg"], "img_diag_subtype": "defect_ident"},
        "vision_prefetch_data": {"is_boiler_pressure_part_image": False},
        "confirmed_scope_intent": {"scope": {"boiler": "1号炉"}},
        "scope_intent_text": "scope text",
        "pending_matched_confirm": True,
    }
    assert apply_vision_rejection_scope_gate(state) is True
    assert "confirmed_scope_intent" not in state
    assert state["interrupt_reason"] == VISION_REJECT_INTERRUPT_REASON
    assert state["human_prompt"] == VISION_HITL_REUPLOAD_PROMPT


def test_confirm_reply_example_for_vision_reject() -> None:
    example = build_scope_hitl_confirm_reply_example(
        {"interrupt_reason": VISION_REJECT_INTERRUPT_REASON}
    )
    assert "重新上传" in example
