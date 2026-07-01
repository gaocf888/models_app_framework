"""看图诊断：视觉拒识 scope 确认门禁。"""

from __future__ import annotations

from app.llm.graphs.img_diag_scope_display import (
    SCOPE_HITL_DB_MATCHED_PROMPT,
    build_scope_hitl_confirm_reply_example,
)
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
        "interrupt_reason": "db_validate_matched",
        "human_prompt": SCOPE_HITL_DB_MATCHED_PROMPT,
    }
    assert apply_vision_rejection_scope_gate(state) is True
    assert "confirmed_scope_intent" not in state
    assert state["interrupt_reason"] == VISION_REJECT_INTERRUPT_REASON
    assert state["human_prompt"] == VISION_HITL_REUPLOAD_PROMPT
    assert state["scope_interrupt_reason"] == "db_validate_matched"
    assert state["scope_hitl_prompt"] == SCOPE_HITL_DB_MATCHED_PROMPT


def test_confirm_reply_example_vision_reject_uses_scope_context() -> None:
    example = build_scope_hitl_confirm_reply_example(
        {
            "interrupt_reason": VISION_REJECT_INTERRUPT_REASON,
            "scope_interrupt_reason": "db_validate_matched",
            "scope_hitl_prompt": "以下为解析且业务库匹配成功的台账信息，请确认是否准确",
            "prompt": "当前图片非锅炉相关图片，请重新上传后再确认台账。",
        }
    )
    assert example == "确认或继续"
    assert "重新上传" not in example
