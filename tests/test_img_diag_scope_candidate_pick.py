"""看图诊断：库未匹配候选选择 HITL（诊断 / 排序 / 触发轮次）。"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.llm.graphs.img_diag_scope_candidate_rank import parse_candidate_rank_suggestions
from app.llm.graphs.img_diag_scope_diagnose import (
    ScopeDiagnoseResult,
    _prefix_scope_up_to,
    diagnose_scope_db_failure,
)
from app.llm.graphs.img_diag_scope_display import (
    HITL_MODE_SCOPE_CANDIDATE_PICK,
    build_scope_candidate_pick_ui_buttons,
    format_scope_hitl_assistant_message,
    is_scope_candidate_pick_hitl,
)
from app.llm.graphs.img_diag_scope_graph import (
    _build_interrupt_payload,
    _clear_scope_candidate_pick_state,
    make_img_diag_scope_nodes,
    scope_candidate_pick_after_mismatch_rounds,
)


def test_prefix_scope_up_to_nulls_finer_fields() -> None:
    scope = {
        "boiler": "1号锅炉",
        "device_name": "水冷壁螺旋段前墙",
        "check_location_name": "吹灰孔71",
        "row_no": 1,
        "tube_no": 3,
    }
    p = _prefix_scope_up_to(scope, "check_location_name")
    assert p["boiler"] == "1号锅炉"
    assert p["device_name"] == "水冷壁螺旋段前墙"
    assert p["check_location_name"] == "吹灰孔71"
    assert p["row_no"] is None
    assert p["tube_no"] is None


@pytest.mark.asyncio
async def test_diagnose_scope_db_failure_locates_check_location() -> None:
    scope = {
        "boiler": "1号锅炉",
        "device_name": "水冷壁螺旋段前墙",
        "check_location_name": "吹灰孔71",
        "row_no": 1,
        "tube_no": None,
    }

    async def fake_prefix_exists(scope_prefix, *, field, executor=None):
        # 机组、受热面命中；检测位置失败
        return field in ("boiler", "device_name")

    with patch(
        "app.llm.graphs.img_diag_scope_diagnose._prefix_exists",
        new=fake_prefix_exists,
    ), patch(
        "app.llm.graphs.img_diag_scope_diagnose._has_explicit_row_no",
        return_value=False,
    ):
        # 默认注入 row_no=1 会被去掉，失败层应为检测位置
        result = await diagnose_scope_db_failure(
            scope,
            cumulative_text="机组：1号锅炉 受热面：水冷壁螺旋段前墙 检测位置：吹灰孔71",
        )
    assert isinstance(result, ScopeDiagnoseResult)
    assert result.failed_field == "check_location_name"
    assert result.matched_prefix.get("boiler") == "1号锅炉"
    assert result.matched_prefix.get("device_name") == "水冷壁螺旋段前墙"
    assert result.user_value == "吹灰孔71"
    assert "row_no" in result.injected_defaults


def test_parse_candidate_rank_suggestions_filters_hallucinations() -> None:
    candidates = [
        {"id": "1", "value": "吹灰孔70", "label": "吹灰孔70"},
        {"id": "2", "value": "吹灰孔72", "label": "吹灰孔72"},
    ]
    raw = '{"suggestions":[{"value":"吹灰孔70","reason":"接近"},{"value":"吹灰孔999","reason":"幻觉"}]}'
    out = parse_candidate_rank_suggestions(raw, candidates=candidates, top_k=5)
    assert len(out) == 1
    assert out[0]["value"] == "吹灰孔70"
    assert out[0]["id"] == "1"


def test_build_scope_candidate_pick_ui_buttons_patch() -> None:
    buttons = build_scope_candidate_pick_ui_buttons(
        [{"id": "1", "value": "吹灰孔70", "label": "吹灰孔70", "rank": 1}],
        failed_field="check_location_name",
    )
    assert len(buttons) == 1
    assert buttons[0]["action"] == "confirm_scope"
    assert buttons[0]["payload"]["scope_patch"]["check_location_name"] == "吹灰孔70"
    assert buttons[0]["payload"]["candidate_pick_id"] == "1"


def test_format_scope_hitl_includes_candidate_section() -> None:
    text = format_scope_hitl_assistant_message(
        {
            "hitl_mode": HITL_MODE_SCOPE_CANDIDATE_PICK,
            "prompt": "请选择",
            "failed_field": "check_location_name",
            "failed_field_label": "检测位置",
            "user_value": "吹灰孔71",
            "llm_suggestions": [
                {"id": "1", "value": "吹灰孔70", "rank": 1, "reason": "数字接近"},
            ],
            "scope_draft_display": {"机组": "1号锅炉"},
            "include_scope_confirm_preview": True,
        }
    )
    assert is_scope_candidate_pick_hitl(
        {"hitl_mode": HITL_MODE_SCOPE_CANDIDATE_PICK}
    )
    assert "【台账信息确认】" in text
    assert "## 台账信息" in text
    assert "待选择（检测位置）" in text
    assert "原解析值：吹灰孔71" in text
    assert "推荐选项：（请点击下方选项，或继续用自然语言补充修正）" in text
    assert "数字接近" not in text
    assert "1. 吹灰孔70" not in text


def test_interrupt_payload_candidate_pick_mode() -> None:
    state = {
        "orchestrator_path": "vision_first",
        "hitl_rounds": 3,
        "pending_scope_candidate_pick": True,
        "scope_candidate_failed_field": "check_location_name",
        "scope_candidate_matched_prefix": {
            "boiler": "1号锅炉",
            "device_name": "水冷壁螺旋段前墙",
        },
        "scope_candidates": [
            {"id": "1", "value": "吹灰孔70", "label": "吹灰孔70"},
            {"id": "2", "value": "吹灰孔72", "label": "吹灰孔72"},
        ],
        "scope_candidate_suggestions": [
            {"id": "1", "value": "吹灰孔70", "label": "吹灰孔70", "rank": 1, "reason": "近"},
        ],
        "scope_diagnose": {
            "failed_field": "check_location_name",
            "matched_prefix": {"boiler": "1号锅炉", "device_name": "水冷壁螺旋段前墙"},
            "user_value": "吹灰孔71",
        },
        "human_prompt": "请选择",
        "interrupt_reason": "db_validate_zero_rows",
        "scope_draft": {
            "boiler": "1号锅炉",
            "device_name": "水冷壁螺旋段前墙",
            "check_location_name": "吹灰孔71",
        },
        "missing_fields": [],
        "img_diag_subtype": "defect_ident",
        "img_diag_request": {"image_urls": ["http://x"], "img_diag_subtype": "defect_ident"},
    }
    with patch(
        "app.llm.graphs.img_diag_scope_graph._apply_vision_gate_or_restore_scope_hitl",
        return_value=None,
    ), patch(
        "app.llm.graphs.img_diag_scope_graph._should_include_vision_preview_in_hitl",
        return_value=False,
    ):
        payload = _build_interrupt_payload(state)
    assert payload["hitl_mode"] == HITL_MODE_SCOPE_CANDIDATE_PICK
    assert payload["failed_field"] == "check_location_name"
    assert payload["ui_buttons"]
    assert payload["ui_buttons"][0]["payload"]["scope_patch"]["check_location_name"] == "吹灰孔70"
    assert "吹灰孔70" in (payload.get("scope_hitl_assistant_message") or "")


@pytest.mark.asyncio
async def test_scope_db_validate_arms_candidate_after_mismatch_rounds() -> None:
    nodes = make_img_diag_scope_nodes()
    state = {
        "scope_draft": {
            "boiler": "1号锅炉",
            "device_name": "水冷壁螺旋段前墙",
            "check_location_name": "吹灰孔71",
            "row_no": None,
            "tube_no": None,
        },
        "scope_cumulative_text": "1号锅炉水冷壁螺旋段前墙吹灰孔71",
        "hitl_rounds": 2,
        "scope_db_mismatch_rounds": scope_candidate_pick_after_mismatch_rounds(),
        "img_diag_subtype": "defect_ident",
    }
    diagnose = ScopeDiagnoseResult(
        failed_field="check_location_name",
        matched_prefix={"boiler": "1号锅炉", "device_name": "水冷壁螺旋段前墙"},
        user_value="吹灰孔71",
    )
    with patch(
        "app.llm.graphs.img_diag_scope_graph.validate_scope_with_relaxation",
        new_callable=AsyncMock,
        return_value=(0, {}, [], "scope_not_found_in_catalog"),
    ), patch(
        "app.llm.graphs.img_diag_scope_graph.scope_candidate_pick_enabled",
        return_value=True,
    ), patch(
        "app.llm.graphs.img_diag_scope_diagnose.diagnose_scope_db_failure",
        new_callable=AsyncMock,
        return_value=diagnose,
    ), patch(
        "app.llm.graphs.img_diag_scope_diagnose.fetch_scope_candidates",
        new_callable=AsyncMock,
        return_value=[{"id": "1", "value": "吹灰孔70", "label": "吹灰孔70"}],
    ), patch(
        "app.llm.graphs.img_diag_scope_candidate_rank.rank_scope_candidates_async",
        new_callable=AsyncMock,
        return_value=[
            {
                "id": "1",
                "value": "吹灰孔70",
                "label": "吹灰孔70",
                "rank": 1,
                "reason": "近",
            }
        ],
    ):
        out = await nodes["scope_db_validate"](state)
    assert out.get("pending_scope_candidate_pick") is True
    assert out.get("scope_candidate_failed_field") == "check_location_name"
    assert out.get("scope_db_mismatch_rounds") == scope_candidate_pick_after_mismatch_rounds() + 1
    assert out.get("interrupt_reason") == "db_validate_zero_rows"


@pytest.mark.asyncio
async def test_scope_db_validate_skips_candidate_before_threshold() -> None:
    nodes = make_img_diag_scope_nodes()
    state = {
        "scope_draft": {
            "boiler": "1号锅炉",
            "device_name": "水冷壁螺旋段前墙",
            "check_location_name": "吹灰孔71",
        },
        "scope_cumulative_text": "1号锅炉水冷壁螺旋段前墙吹灰孔71",
        "hitl_rounds": 0,
        "scope_db_mismatch_rounds": 0,
        "img_diag_subtype": "defect_ident",
    }
    with patch(
        "app.llm.graphs.img_diag_scope_graph.validate_scope_with_relaxation",
        new_callable=AsyncMock,
        return_value=(0, {}, [], "scope_not_found_in_catalog"),
    ), patch(
        "app.llm.graphs.img_diag_scope_graph.scope_candidate_pick_enabled",
        return_value=True,
    ), patch(
        "app.llm.graphs.img_diag_scope_diagnose.diagnose_scope_db_failure",
        new_callable=AsyncMock,
    ) as diagnose:
        out = await nodes["scope_db_validate"](state)
    assert out.get("pending_scope_candidate_pick") is not True
    assert out.get("scope_db_mismatch_rounds") == 1
    assert "未匹配" in str(out.get("human_prompt") or "")
    diagnose.assert_not_awaited()


def test_clear_scope_candidate_pick_state() -> None:
    state = {
        "pending_scope_candidate_pick": True,
        "scope_candidate_failed_field": "x",
        "scope_candidates": [1],
    }
    _clear_scope_candidate_pick_state(state)
    assert "pending_scope_candidate_pick" not in state
    assert "scope_candidates" not in state
