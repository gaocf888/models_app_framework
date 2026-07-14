"""看图诊断 scope 探针与视觉展示单测。"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from app.llm.graphs.img_diag_scope_display import (
    HITL_MODE_VISION_ACK_ONLY,
    build_scope_hitl_confirm_reply_example,
)
from app.llm.graphs.img_diag_scope_graph import _build_interrupt_payload
from app.llm.graphs.img_diag_scope_intent import ImgDiagScopeDraft, parse_img_diag_scope_draft
from app.llm.graphs.img_diag_scope_probe import probe_img_diag_scope_route
from app.llm.graphs.img_diag_vision_display import (
    build_vision_findings_display,
    build_vision_morphology_bullets,
    format_vision_hitl_assistant_block,
)


@pytest.mark.asyncio
async def test_probe_path2_when_scope_complete_and_db_ok() -> None:
    draft = ImgDiagScopeDraft(
        boiler="1号锅炉",
        device_name="低温过热器",
        check_location_name=None,
        row_no=None,
        tube_no=None,
        confidence="high",
        confidence_reasons=(),
        time_meta=parse_img_diag_scope_draft("").time_meta,
    )
    with patch(
        "app.llm.graphs.img_diag_scope_probe.parse_img_diag_scope_draft",
        return_value=draft,
    ), patch(
        "app.llm.graphs.img_diag_scope_probe.validate_scope_in_catalog",
        new_callable=AsyncMock,
        return_value=(2, None),
    ):
        result = await probe_img_diag_scope_route("1号锅炉低温过热器")
    assert result.route == "path2"
    assert result.missing_fields == []
    assert result.db_match_count == 2


@pytest.mark.asyncio
async def test_probe_path1_when_missing_boiler() -> None:
    draft = ImgDiagScopeDraft(
        boiler=None,
        device_name="低温过热器",
        check_location_name=None,
        row_no=None,
        tube_no=None,
        confidence="high",
        confidence_reasons=(),
        time_meta=parse_img_diag_scope_draft("").time_meta,
    )
    with patch(
        "app.llm.graphs.img_diag_scope_probe.parse_img_diag_scope_draft",
        return_value=draft,
    ):
        result = await probe_img_diag_scope_route("低温过热器")
    assert result.route == "path1"
    assert "boiler" in result.missing_fields


@pytest.mark.asyncio
async def test_probe_path1_when_db_validate_fails() -> None:
    draft = ImgDiagScopeDraft(
        boiler="1号锅炉",
        device_name="低温过热器",
        check_location_name="不存在的位置",
        row_no=None,
        tube_no=None,
        confidence="high",
        confidence_reasons=(),
        time_meta=parse_img_diag_scope_draft("").time_meta,
    )
    with patch(
        "app.llm.graphs.img_diag_scope_probe.parse_img_diag_scope_draft",
        return_value=draft,
    ), patch(
        "app.llm.graphs.img_diag_scope_probe.validate_scope_in_catalog",
        new_callable=AsyncMock,
        return_value=(0, "not_found"),
    ):
        result = await probe_img_diag_scope_route("1号锅炉低温过热器不存在的位置")
    assert result.route == "path1"
    assert result.db_match_count == 0


def test_build_vision_morphology_bullets_defect_ident() -> None:
    bullets = build_vision_morphology_bullets(
        {
            "vision_narrative": (
                "- **检验标记**：白圈标记\n"
                "- **线状损伤**：标记圈内周向表面裂纹\n"
                "- **表面状态**：重度锈蚀"
            ),
            "defect_type": "沟槽",
            "defect_types": ["飞灰冲刷磨损沟槽", "周向表面裂纹"],
            "defect_signals": ["线性沟槽", "白圈内横向细线"],
        },
        img_diag_subtype="defect_ident",
    )
    assert any("检验标记" in b for b in bullets)
    assert any("周向表面裂纹" in b for b in bullets)
    assert not any("主缺陷类型" in b for b in bullets)
    assert not any("缺陷类型（多选）" in b for b in bullets)


def test_build_vision_findings_display_shows_narrative_only() -> None:
    display = build_vision_findings_display(
        {
            "vision_narrative": "- **线状损伤**：白圈内裂纹\n- **表面状态**：氧化皮剥落",
            "defect_type": "沟槽",
            "defect_types": ["沟槽", "周向表面裂纹"],
        },
        img_diag_subtype="defect_ident",
    )
    assert "外观可见分析" in display
    assert "白圈内裂纹" in display["外观可见分析"]
    assert "主缺陷类型" not in display


def test_build_vision_morphology_bullets_prefers_narrative_lines() -> None:
    bullets = build_vision_morphology_bullets(
        {
            "vision_narrative": "- 白圈内可见周向裂纹\n- 表面重度锈蚀",
            "defect_type": "周向表面裂纹",
        },
        img_diag_subtype="defect_ident",
    )
    assert any("周向裂纹" in b for b in bullets)
    assert not any("主缺陷类型" in b for b in bullets)


def test_format_vision_hitl_assistant_block_title() -> None:
    text = format_vision_hitl_assistant_block(
        {
            "vision_narrative": "- **线状损伤**：线状裂纹沿管轴延伸",
            "defect_type": "裂纹",
        },
        img_diag_subtype="defect_ident",
    )
    assert "【图像可见分析】" in text
    assert "视觉臂" not in text
    assert "裂纹" in text
    assert "- **线状损伤**：" in text


def test_format_vision_hitl_assistant_block_markdown_categories() -> None:
    text = format_vision_hitl_assistant_block(
        {
            "vision_narrative": (
                "- **检验标记**：白圈标记\n"
                "- **主体形貌**：管壁锈蚀\n"
                "- **表面状态**：重度锈蚀"
            ),
        },
        img_diag_subtype="defect_ident",
    )
    assert "- **检验标记**：" in text
    assert "- **主体形貌**：" in text
    assert "- **表面状态**：" in text
    assert "  · " not in text
    assert "## 宏观外貌分析" not in text


def test_interrupt_payload_scope_fail_includes_vision_with_macro_heading() -> None:
    """视觉已通过 + 台账未通过：首次 HITL 须返回图像可见分析（含宏观外貌标题）。"""
    state = {
        "orchestrator_path": "vision_first",
        "hitl_rounds": 0,
        "img_diag_subtype": "defect_ident",
        "vision_prefetch_data": {
            "is_boiler_pressure_part_image": True,
            "vision_narrative": "- **线状损伤**：裂纹",
        },
        "scope_draft": {"boiler": "1号锅炉"},
        "missing_fields": ["device_name"],
        "interrupt_reason": "missing:device_name",
        "human_prompt": "未识别解析到台账信息，请补充！",
        "img_diag_request": {
            "image_urls": ["http://minio/good.jpg"],
            "img_diag_subtype": "defect_ident",
        },
    }
    payload = _build_interrupt_payload(state)
    assert payload.get("include_vision_preview") is True
    assert payload.get("include_scope_confirm_preview") is True
    msg = payload.get("vision_hitl_assistant_message") or ""
    assert "【图像可见分析】" in msg
    assert "## 宏观外貌分析" in msg
    assert msg.index("【图像可见分析】") < msg.index("## 宏观外貌分析")
    assert "- **线状损伤**：" in msg
    assert not payload.get("hitl_mode")
    assert not payload.get("ui_buttons")


def test_vision_preview_sse_event_includes_macro_appearance_heading() -> None:
    from app.llm.graphs.analysis_img_diag_runner import AnalysisImgDiagGraphRunner

    ev = AnalysisImgDiagGraphRunner._vision_preview_sse_event(
        request_id="anl_test",
        img_diag_subtype="defect_ident",
        vision_data={
            "vision_narrative": "- **线状损伤**：裂纹",
        },
        vision_ms=100,
        vision_status="ok",
    )
    msg = ev.get("vision_hitl_assistant_message") or ""
    assert ev["event"] == "img_diag_vision_preview"
    assert "## 宏观外貌分析" in msg
    assert "- **线状损伤**：" in msg


def test_confirm_reply_example_db_not_matched() -> None:
    example = build_scope_hitl_confirm_reply_example(
        {
            "interrupt_reason": "db_validate_zero_rows",
            "prompt": "业务库中未匹配到下面台账信息，请确认机组、受热面、检测位置、排数、管数是否准确",
        }
    )
    assert "受热面应为" in example


def test_confirm_reply_example_db_matched() -> None:
    example = build_scope_hitl_confirm_reply_example(
        {
            "interrupt_reason": "db_validate_matched",
            "prompt": "以下为解析且业务库匹配成功的台账信息，请确认是否准确",
        }
    )
    assert example == "确认或继续"


def test_interrupt_payload_vision_only_once_after_delivered() -> None:
    state = {
        "orchestrator_path": "vision_first",
        "hitl_rounds": 1,
        "img_diag_subtype": "defect_ident",
        "vision_prefetch_data": {"defect_type": "裂纹", "morphology_summary": "线状"},
        "human_prompt": "请确认",
        "scope_draft": {},
        "missing_fields": [],
        "interrupt_reason": "db_validate_zero_rows",
    }
    payload = _build_interrupt_payload(state)
    assert payload["include_vision_preview"] is True
    assert payload["vision_findings_display"]
    # delivered 仅在最终下发时由 sync 置位（build 可幂等多次）
    from app.llm.graphs.img_diag_scope_graph import _sync_scope_human_confirm_hitl_gate_flags

    _sync_scope_human_confirm_hitl_gate_flags(state, interrupt_payload=payload)
    assert state.get("vision_hitl_preview_delivered") is True

    state["hitl_rounds"] = 2
    payload2 = _build_interrupt_payload(state)
    assert payload2["include_vision_preview"] is False
    assert "vision_findings_display" not in payload2


def test_first_passed_vision_after_failed_rounds_still_includes_preview() -> None:
    """错图多轮拒识后，首次换正确图且视觉通过、台账未通过 → 须返回图像可见分析。"""
    # 模拟：此前错图 HITL 仅展示拒识（不置 delivered）；换图刷新已清 images_replaced
    state = {
        "orchestrator_path": "vision_first",
        "hitl_rounds": 3,
        "vision_hitl_preview_delivered": False,
        "vision_images_replaced": False,
        "img_diag_subtype": "defect_ident",
        "vision_prefetch_data": {
            "is_boiler_pressure_part_image": True,
            "vision_narrative": "- **主体形貌**：管壁沟槽",
        },
        "scope_draft": {"boiler": "1号锅炉"},
        "missing_fields": ["device_name"],
        "interrupt_reason": "missing:device_name",
        "human_prompt": "未识别解析到台账信息，请补充！",
        "img_diag_request": {
            "image_urls": ["http://minio/good.jpg"],
            "img_diag_subtype": "defect_ident",
        },
    }
    payload = _build_interrupt_payload(state)
    assert payload["include_vision_preview"] is True
    assert "【图像可见分析】" in (payload.get("vision_hitl_assistant_message") or "")
    from app.llm.graphs.img_diag_scope_graph import _sync_scope_human_confirm_hitl_gate_flags

    _sync_scope_human_confirm_hitl_gate_flags(state, interrupt_payload=payload)
    assert state.get("vision_hitl_preview_delivered") is True

    # 未再换图的后续台账纠错轮：不再返回图像可见分析
    state["hitl_rounds"] = 4
    state["vision_images_replaced"] = False
    payload2 = _build_interrupt_payload(state)
    assert payload2["include_vision_preview"] is False


def test_sync_gate_flags_does_not_mark_delivered_on_rejection_preview() -> None:
    from app.llm.graphs.img_diag_scope_graph import _sync_scope_human_confirm_hitl_gate_flags
    from app.llm.graphs.img_diag_vision_display import VISION_REJECT_INTERRUPT_REASON

    state = {
        "orchestrator_path": "vision_first",
        "hitl_rounds": 0,
        "interrupt_reason": VISION_REJECT_INTERRUPT_REASON,
        "vision_confirm_blocked": True,
        "vision_prefetch_data": {"is_boiler_pressure_part_image": False},
        "img_diag_request": {"image_urls": ["http://bad.jpg"], "img_diag_subtype": "defect_ident"},
        "img_diag_subtype": "defect_ident",
    }
    _sync_scope_human_confirm_hitl_gate_flags(
        state,
        interrupt_payload={"include_vision_preview": True},
    )
    assert state["hitl_rounds"] == 1
    assert not state.get("vision_hitl_preview_delivered")


def test_interrupt_payload_vision_passed_scope_passed_omits_vision_preview() -> None:
    """双门禁均已通过时不附带图像可见分析（图应直接放行，不依赖 vision_ack HITL）。"""
    state = {
        "orchestrator_path": "vision_first",
        "hitl_rounds": 0,
        "pending_vision_user_ack": True,
        "img_diag_subtype": "defect_ident",
        "vision_prefetch_data": {
            "is_boiler_pressure_part_image": True,
            "vision_narrative": "- 管壁裂纹",
        },
        "confirmed_scope_intent": {"boiler": "1号锅炉"},
        "scope_intent_text": "1号锅炉低温过热器",
        "scope_draft": {
            "boiler": "1号锅炉",
            "device_name": "低温过热器",
        },
        "img_diag_request": {
            "image_urls": ["http://minio/good.jpg"],
            "img_diag_subtype": "defect_ident",
        },
    }
    payload = _build_interrupt_payload(state)
    assert payload["include_vision_preview"] is False
    assert payload["include_scope_confirm_preview"] is False
    assert "vision_hitl_assistant_message" not in payload
    assert not payload.get("hitl_mode")
    assert not payload.get("ui_buttons")


def test_scope_interrupt_sse_event_includes_vision_ack_ui() -> None:
    from app.llm.graphs.analysis_img_diag_runner import AnalysisImgDiagGraphRunner

    ev = AnalysisImgDiagGraphRunner._scope_interrupt_sse_event(
        {
            "request_id": "anl_test",
            "resume_token": "rt_test",
            "interrupt_payload": {
                "hitl_mode": HITL_MODE_VISION_ACK_ONLY,
                "ui_buttons": [{"id": "continue", "label": "继续", "action": "confirm_scope", "payload": {}}],
                "include_vision_preview": True,
                "include_scope_confirm_preview": False,
                "vision_hitl_assistant_message": "【图像可见分析】",
            },
        }
    )
    assert ev["hitl_mode"] == HITL_MODE_VISION_ACK_ONLY
    assert ev["ui_buttons"][0]["label"] == "继续"
    assert ev["resume_token"] == "rt_test"
