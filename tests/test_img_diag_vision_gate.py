"""看图诊断：视觉拒识 scope 确认门禁。"""

from __future__ import annotations

import sys
from unittest.mock import MagicMock

import pytest

from app.llm.graphs.img_diag_scope_display import (
    SCOPE_HITL_DB_MATCHED_PROMPT,
    SCOPE_HITL_DB_NOT_MATCHED_PROMPT,
    SCOPE_HITL_NOT_PARSED_PROMPT,
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
    assert state["human_prompt"] == SCOPE_HITL_DB_MATCHED_PROMPT
    assert state["pending_matched_confirm"] is True
    assert state["vision_confirm_blocked"] is True
    assert state["scope_interrupt_reason"] == "db_validate_matched"
    assert state["scope_hitl_prompt"] == SCOPE_HITL_DB_MATCHED_PROMPT
    assert "请重新上传后再确认台账" not in state["human_prompt"]


def test_vision_blocked_scope_matched_interrupt_payload() -> None:
    from app.llm.graphs.img_diag_scope_display import (
        format_scope_hitl_assistant_message,
        resolve_scope_hitl_display_prompt,
    )

    payload = {
        "interrupt_reason": VISION_REJECT_INTERRUPT_REASON,
        "scope_interrupt_reason": "db_validate_matched",
        "scope_hitl_prompt": SCOPE_HITL_DB_MATCHED_PROMPT,
        "prompt": "当前图片非锅炉相关图片，请重新上传后再确认台账。",
        "pending_matched_confirm": True,
        "vision_confirm_blocked": True,
        "scope_draft_display": {
            "机组": "1号锅炉",
            "受热面": "水冷壁螺旋段前墙",
            "检测位置": "吹灰孔7",
            "排数": 1,
        },
    }
    assert resolve_scope_hitl_display_prompt(interrupt_payload=payload) == SCOPE_HITL_DB_MATCHED_PROMPT
    example = build_scope_hitl_confirm_reply_example(payload)
    assert example == "确认或继续"
    text = format_scope_hitl_assistant_message(payload)
    assert "请重新上传后再确认台账" not in text
    assert "匹配成功" in text
    assert "确认或继续" in text


def test_confirm_reply_example_vision_reject_uses_scope_context() -> None:
    example = build_scope_hitl_confirm_reply_example(
        {
            "interrupt_reason": VISION_REJECT_INTERRUPT_REASON,
            "scope_interrupt_reason": "db_validate_matched",
            "scope_hitl_prompt": SCOPE_HITL_DB_MATCHED_PROMPT,
            "prompt": "当前图片非锅炉相关图片，请重新上传后再确认台账。",
            "pending_matched_confirm": True,
        }
    )
    assert example == "确认或继续"
    assert "重新上传" not in example


def test_confirm_reply_example_pending_matched() -> None:
    example = build_scope_hitl_confirm_reply_example(
        {
            "interrupt_reason": "db_validate_zero_rows",
            "prompt": SCOPE_HITL_DB_NOT_MATCHED_PROMPT,
            "pending_matched_confirm": True,
        }
    )
    assert example == "确认或继续"


def test_sync_scope_hitl_after_vision_accepted_restores_matched_prompt() -> None:
    from app.llm.graphs.img_diag_scope_display import sync_scope_hitl_after_vision_accepted

    state = {
        "img_diag_subtype": "defect_ident",
        "img_diag_request": {"image_urls": ["http://a.jpg"], "img_diag_subtype": "defect_ident"},
        "vision_prefetch_data": {
            "is_boiler_pressure_part_image": True,
            "defect_type": "裂纹",
            "vision_narrative": "- 检验标记：白圈\n- 线状损伤：裂纹",
        },
        "interrupt_reason": VISION_REJECT_INTERRUPT_REASON,
        "human_prompt": VISION_HITL_REUPLOAD_PROMPT,
        "scope_interrupt_reason": "db_validate_matched",
        "scope_hitl_prompt": SCOPE_HITL_DB_MATCHED_PROMPT,
        "pending_matched_confirm": False,
    }
    sync_scope_hitl_after_vision_accepted(state)
    assert state["interrupt_reason"] == "db_validate_matched"
    assert state["human_prompt"] == SCOPE_HITL_DB_MATCHED_PROMPT
    assert state["pending_matched_confirm"] is True


def test_vision_rejected_false_flag_but_substantive_boiler_narrative_not_blocked() -> None:
    blocked = is_scope_confirm_blocked_by_vision(
        {
            "is_boiler_pressure_part_image": False,
            "vision_narrative": (
                "- 检验标记：白圈标记缺陷区\n"
                "- 主体形貌：管壁锈蚀剥落\n"
                "- 线状损伤：细长线状裂纹沿管轴延伸"
            ),
            "defect_type": "裂纹",
        },
        img_diag_request={"image_urls": ["http://a.jpg"], "img_diag_subtype": "defect_ident"},
        img_diag_subtype="defect_ident",
    )
    assert blocked is False


def test_route_after_human_confirm_confirmed_before_vision_stale_reason(monkeypatch) -> None:
    end = object()
    graph_mod = MagicMock()
    graph_mod.END = end
    monkeypatch.setitem(sys.modules, "langgraph.graph", graph_mod)

    from app.llm.graphs.img_diag_scope_graph import _route_after_human_confirm

    state = {
        "interrupt_reason": VISION_REJECT_INTERRUPT_REASON,
        "confirmed_scope_intent": {"boiler": "1号锅炉"},
        "scope_intent_text": "scope text",
    }
    assert _route_after_human_confirm(state) is end


def test_apply_vision_gate_clears_stale_interrupt_when_vision_passes() -> None:
    state = {
        "img_diag_subtype": "defect_ident",
        "img_diag_request": {"image_urls": ["http://a.jpg"], "img_diag_subtype": "defect_ident"},
        "vision_prefetch_data": {
            "is_boiler_pressure_part_image": True,
            "defect_type": "裂纹",
            "vision_narrative": "- 管壁裂纹",
        },
        "interrupt_reason": VISION_REJECT_INTERRUPT_REASON,
        "vision_confirm_blocked": True,
        "scope_interrupt_reason": "db_validate_matched",
        "pending_matched_confirm": True,
        "confirmed_scope_intent": {"boiler": "1号锅炉"},
        "scope_intent_text": "scope text",
    }
    assert apply_vision_rejection_scope_gate(state) is False
    assert state.get("interrupt_reason") == "db_validate_matched"
    assert "vision_confirm_blocked" not in state
    assert state.get("confirmed_scope_intent")


def test_matched_confirm_after_vision_recovery_finalizes() -> None:
    from app.llm.graphs.img_diag_scope_graph import _apply_human_scope_response

    state = {
        "pending_matched_confirm": True,
        "interrupt_reason": VISION_REJECT_INTERRUPT_REASON,
        "vision_confirm_blocked": True,
        "scope_interrupt_reason": "db_validate_matched",
        "scope_hitl_prompt": SCOPE_HITL_DB_MATCHED_PROMPT,
        "img_diag_subtype": "defect_ident",
        "img_diag_request": {"image_urls": ["http://a.jpg"], "img_diag_subtype": "defect_ident"},
        "vision_prefetch_data": {
            "is_boiler_pressure_part_image": True,
            "defect_type": "裂纹",
        },
        "scope_cumulative_text": "1号锅炉水冷壁螺旋段前墙吹灰孔7",
        "scope_draft": {
            "boiler": "1号锅炉",
            "device_name": "水冷壁螺旋段前墙",
            "check_location_name": "吹灰孔7",
            "row_no": 1,
        },
    }
    updated = _apply_human_scope_response(
        state,
        {"action": "confirm_scope", "payload": {"user_supplement": "确认"}},
    )
    assert updated.get("confirmed_scope_intent")
    assert updated.get("interrupt_reason") != VISION_REJECT_INTERRUPT_REASON


def test_apply_vision_gate_restores_pending_after_finalize_cleared() -> None:
    state = {
        "img_diag_subtype": "defect_ident",
        "img_diag_request": {"image_urls": ["http://a.jpg"], "img_diag_subtype": "defect_ident"},
        "vision_prefetch_data": {"is_boiler_pressure_part_image": False},
        "confirmed_scope_intent": {"boiler": "1号锅炉"},
        "scope_intent_text": "scope text",
        "pending_matched_confirm": False,
        "scope_interrupt_reason": "db_validate_matched",
        "scope_hitl_prompt": SCOPE_HITL_DB_MATCHED_PROMPT,
        "interrupt_reason": "db_validate_matched",
        "human_prompt": SCOPE_HITL_DB_MATCHED_PROMPT,
    }
    assert apply_vision_rejection_scope_gate(state) is True
    assert state["pending_matched_confirm"] is True


def test_image_only_resume_does_not_finalize_matched_confirm() -> None:
    from app.llm.graphs.img_diag_scope_graph import _apply_human_scope_response

    state = {
        "pending_matched_confirm": True,
        "interrupt_reason": VISION_REJECT_INTERRUPT_REASON,
        "scope_interrupt_reason": "db_validate_matched",
        "scope_hitl_prompt": SCOPE_HITL_DB_MATCHED_PROMPT,
        "img_diag_subtype": "defect_ident",
        "img_diag_request": {"image_urls": ["http://wrong.jpg"], "img_diag_subtype": "defect_ident"},
        "vision_prefetch_data": {"is_boiler_pressure_part_image": False},
        "scope_cumulative_text": "1号锅炉水冷壁螺旋段前墙吹灰孔7",
        "scope_draft": {
            "boiler": "1号锅炉",
            "device_name": "水冷壁螺旋段前墙",
            "check_location_name": "吹灰孔7",
            "row_no": 1,
        },
    }
    updated = _apply_human_scope_response(
        state,
        {"action": "edit_scope", "payload": {"image_urls": ["http://wrong2.jpg"]}},
    )
    assert updated.get("pending_matched_confirm") is True
    assert not updated.get("confirmed_scope_intent")
    assert updated.get("interrupt_reason") == VISION_REJECT_INTERRUPT_REASON


def test_multi_round_wrong_images_then_confirm_with_good_vision() -> None:
    from app.llm.graphs.img_diag_scope_graph import _apply_human_scope_response

    base = {
        "pending_matched_confirm": True,
        "scope_interrupt_reason": "db_validate_matched",
        "scope_hitl_prompt": SCOPE_HITL_DB_MATCHED_PROMPT,
        "img_diag_subtype": "defect_ident",
        "scope_cumulative_text": "1号锅炉水冷壁螺旋段前墙吹灰孔7",
        "scope_draft": {
            "boiler": "1号锅炉",
            "device_name": "水冷壁螺旋段前墙",
            "check_location_name": "吹灰孔7",
            "row_no": 1,
        },
    }

    round2 = dict(base)
    round2.update(
        {
            "interrupt_reason": VISION_REJECT_INTERRUPT_REASON,
            "img_diag_request": {"image_urls": ["http://wrong1.jpg"], "img_diag_subtype": "defect_ident"},
            "vision_prefetch_data": {"is_boiler_pressure_part_image": False},
        }
    )
    after_wrong2 = _apply_human_scope_response(
        round2,
        {"action": "edit_scope", "payload": {"image_urls": ["http://wrong2.jpg"]}},
    )
    assert after_wrong2.get("pending_matched_confirm") is True
    assert not after_wrong2.get("confirmed_scope_intent")

    round3 = dict(after_wrong2)
    round3.update(
        {
            "img_diag_request": {"image_urls": ["http://good.jpg"], "img_diag_subtype": "defect_ident"},
            "vision_prefetch_data": {
                "is_boiler_pressure_part_image": True,
                "defect_type": "裂纹",
                "vision_narrative": "- 管壁裂纹",
            },
        }
    )
    after_confirm = _apply_human_scope_response(
        round3,
        {
            "action": "confirm_scope",
            "payload": {"user_supplement": "确认", "image_urls": ["http://good.jpg"]},
        },
    )
    assert after_confirm.get("confirmed_scope_intent")
    assert after_confirm.get("scope_intent_text")
    assert not after_confirm.get("pending_matched_confirm")
    assert after_confirm.get("interrupt_reason") != VISION_REJECT_INTERRUPT_REASON


def test_route_after_human_confirm_pending_matched_waits_interrupt(monkeypatch) -> None:
    end = object()
    graph_mod = MagicMock()
    graph_mod.END = end
    monkeypatch.setitem(sys.modules, "langgraph.graph", graph_mod)

    from app.llm.graphs.img_diag_scope_graph import _route_after_human_confirm

    state = {
        "pending_matched_confirm": True,
        "interrupt_reason": "db_validate_matched",
    }
    assert _route_after_human_confirm(state) is end


def test_route_after_human_confirm_vision_reject_with_scope_supplement_reparses(
    monkeypatch,
) -> None:
    end = object()
    graph_mod = MagicMock()
    graph_mod.END = end
    monkeypatch.setitem(sys.modules, "langgraph.graph", graph_mod)

    from app.llm.graphs.img_diag_scope_graph import (
        _apply_human_scope_response,
        _route_after_human_confirm,
    )

    human = {
        "action": "confirm_scope",
        "payload": {"user_supplement": "1号锅炉水冷壁螺旋段前墙吹灰孔7"},
    }
    state = {
        "interrupt_reason": VISION_REJECT_INTERRUPT_REASON,
        "vision_confirm_blocked": True,
        "scope_interrupt_reason": "missing:boiler,device_name",
        "scope_hitl_prompt": SCOPE_HITL_NOT_PARSED_PROMPT,
        "scope_cumulative_text": "",
        "human_interactions": [{"request": {}, "response": human}],
        "img_diag_subtype": "defect_ident",
        "img_diag_request": {"image_urls": ["http://wrong.jpg"], "img_diag_subtype": "defect_ident"},
    }
    updated = _apply_human_scope_response(state, human)
    assert _route_after_human_confirm(updated) == "scope_preflight_llm"
    assert _route_after_human_confirm(updated) is not end


def test_route_after_human_confirm_vision_reject_empty_supplement_still_waits_interrupt(
    monkeypatch,
) -> None:
    end = object()
    graph_mod = MagicMock()
    graph_mod.END = end
    monkeypatch.setitem(sys.modules, "langgraph.graph", graph_mod)

    from app.llm.graphs.img_diag_scope_graph import (
        _apply_human_scope_response,
        _route_after_human_confirm,
    )

    human = {"action": "confirm_scope", "payload": {}}
    state = {
        "interrupt_reason": VISION_REJECT_INTERRUPT_REASON,
        "vision_confirm_blocked": True,
        "scope_hitl_prompt": SCOPE_HITL_NOT_PARSED_PROMPT,
        "human_interactions": [{"request": {}, "response": human}],
    }
    updated = _apply_human_scope_response(state, human)
    assert _route_after_human_confirm(updated) is end


def test_route_after_human_confirm_matched_affirmative_with_vision_reject_still_waits(
    monkeypatch,
) -> None:
    end = object()
    graph_mod = MagicMock()
    graph_mod.END = end
    monkeypatch.setitem(sys.modules, "langgraph.graph", graph_mod)

    from app.llm.graphs.img_diag_scope_graph import (
        _apply_human_scope_response,
        _route_after_human_confirm,
    )

    human = {"action": "confirm_scope", "payload": {"user_supplement": "确认"}}
    state = {
        "pending_matched_confirm": True,
        "interrupt_reason": VISION_REJECT_INTERRUPT_REASON,
        "vision_confirm_blocked": True,
        "scope_interrupt_reason": "db_validate_matched",
        "scope_hitl_prompt": SCOPE_HITL_DB_MATCHED_PROMPT,
        "scope_cumulative_text": "1号锅炉水冷壁螺旋段前墙吹灰孔7",
        "human_interactions": [{"request": {}, "response": human}],
        "img_diag_subtype": "defect_ident",
        "img_diag_request": {"image_urls": ["http://wrong.jpg"], "img_diag_subtype": "defect_ident"},
        "vision_prefetch_data": {"is_boiler_pressure_part_image": False},
    }
    updated = _apply_human_scope_response(state, human)
    assert _route_after_human_confirm(updated) is end


def test_wrong_image_plus_confirm_does_not_finalize() -> None:
    from app.llm.graphs.img_diag_scope_graph import _apply_human_scope_response

    state = {
        "pending_matched_confirm": True,
        "scope_interrupt_reason": "db_validate_matched",
        "scope_hitl_prompt": SCOPE_HITL_DB_MATCHED_PROMPT,
        "img_diag_subtype": "defect_ident",
        "img_diag_request": {"image_urls": ["http://wrong2.jpg"], "img_diag_subtype": "defect_ident"},
        "vision_prefetch_data": {"is_boiler_pressure_part_image": False},
        "scope_cumulative_text": "1号锅炉水冷壁螺旋段前墙吹灰孔7",
        "scope_draft": {
            "boiler": "1号锅炉",
            "device_name": "水冷壁螺旋段前墙",
            "check_location_name": "吹灰孔7",
            "row_no": 1,
        },
    }
    updated = _apply_human_scope_response(
        state,
        {
            "action": "confirm_scope",
            "payload": {"user_supplement": "确认", "image_urls": ["http://wrong2.jpg"]},
        },
    )
    assert not updated.get("confirmed_scope_intent")
    assert updated.get("pending_matched_confirm") is True
    assert updated.get("interrupt_reason") == VISION_REJECT_INTERRUPT_REASON


def test_affirmative_with_prep_vision_refresh_flag_finalizes() -> None:
    from app.llm.graphs.img_diag_scope_graph import _apply_human_scope_response

    state = {
        "pending_matched_confirm": True,
        "interrupt_reason": VISION_REJECT_INTERRUPT_REASON,
        "vision_confirm_blocked": True,
        "scope_interrupt_reason": "db_validate_matched",
        "scope_hitl_prompt": SCOPE_HITL_DB_MATCHED_PROMPT,
        "vision_prefetch_resume_refreshed": True,
        "img_diag_subtype": "defect_ident",
        "img_diag_request": {"image_urls": ["http://good.jpg"], "img_diag_subtype": "defect_ident"},
        "vision_prefetch_data": {
            "is_boiler_pressure_part_image": True,
            "defect_type": "裂纹",
            "vision_narrative": "- 管壁裂纹",
        },
        "scope_cumulative_text": "1号锅炉水冷壁螺旋段前墙吹灰孔7",
        "scope_draft": {
            "boiler": "1号锅炉",
            "device_name": "水冷壁螺旋段前墙",
            "check_location_name": "吹灰孔7",
            "row_no": 1,
        },
    }
    updated = _apply_human_scope_response(
        state,
        {"action": "confirm_scope", "payload": {"user_supplement": "确认"}},
    )
    assert updated.get("confirmed_scope_intent")
    assert not updated.get("pending_matched_confirm")
    assert updated.get("interrupt_reason") != VISION_REJECT_INTERRUPT_REASON
    assert "vision_confirm_blocked" not in updated


def test_vision_rejected_non_boiler_equipment_still_blocked() -> None:
    blocked = is_scope_confirm_blocked_by_vision(
        {
            "is_boiler_pressure_part_image": False,
            "vision_narrative": (
                "- 主体形貌：可见工业电机外壳\n"
                "- 表面状态：与锅炉管壁无关"
            ),
        },
        img_diag_request={"image_urls": ["http://a.jpg"], "img_diag_subtype": "defect_ident"},
        img_diag_subtype="defect_ident",
    )
    assert blocked is True


@pytest.mark.asyncio
async def test_try_resolve_matched_confirm_after_prep_short_circuits_good_vision() -> None:
    """prep 刷新好图后短路确认，不依赖 LangGraph resume 快照。"""
    from unittest.mock import AsyncMock

    from app.llm.graphs.img_diag_scope_graph import _try_resolve_matched_confirm_after_prep

    good_vision = {
        "is_boiler_pressure_part_image": True,
        "defect_type": "裂纹",
        "vision_narrative": "- 管壁裂纹",
    }
    checkpoint_state = {
        "pending_matched_confirm": True,
        "interrupt_reason": VISION_REJECT_INTERRUPT_REASON,
        "scope_interrupt_reason": "db_validate_matched",
        "scope_hitl_prompt": SCOPE_HITL_DB_MATCHED_PROMPT,
        "img_diag_subtype": "defect_ident",
        "img_diag_request": {"image_urls": ["http://good.jpg"], "img_diag_subtype": "defect_ident"},
        "vision_prefetch_data": good_vision,
        "scope_cumulative_text": "1号锅炉水冷壁螺旋段前墙吹灰孔7",
        "scope_draft": {
            "boiler": "1号锅炉",
            "device_name": "水冷壁螺旋段前墙",
            "check_location_name": "吹灰孔7",
            "row_no": 1,
        },
        "orchestrator_path": "vision_first",
    }
    snap = MagicMock()
    snap.values = checkpoint_state
    graph = AsyncMock()
    graph.aget_state = AsyncMock(return_value=snap)
    graph.aupdate_state = AsyncMock()

    session = MagicMock()
    session.thread_id = "t1"
    session.request_id = "req1"
    session.orchestrator_path = "vision_first"
    session.img_diag_request = checkpoint_state["img_diag_request"]

    result = await _try_resolve_matched_confirm_after_prep(
        graph,
        {"configurable": {"thread_id": "t1"}},
        session,
        action="confirm_scope",
        payload={"user_supplement": "确认", "image_urls": ["http://good.jpg"]},
        vision_refresh=None,
    )
    assert result is not None
    assert result["status"] == "confirmed"
    assert result["confirmed_scope_intent"]
    assert result["vision_prefetch"] == good_vision
