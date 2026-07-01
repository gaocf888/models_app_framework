"""看图诊断 scope 探针与视觉展示单测。"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from app.llm.graphs.img_diag_scope_display import build_scope_hitl_confirm_reply_example
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
            "defect_type": "沟槽",
            "defect_orientation": "沿管轴",
            "morphology_summary": "可见沟槽形貌，走向沿管轴",
            "distribution_features": "集中分布",
            "surface_state": "氧化皮剥落",
            "defect_signals": ["线性沟槽", "边缘较清晰"],
            "risk_level": "moderate",
        },
        img_diag_subtype="defect_ident",
    )
    assert any("主体形貌" in b for b in bullets)
    assert any("分布特征" in b for b in bullets)
    assert any("表面状态" in b for b in bullets)
    assert not any("严重度" in b for b in bullets)


def test_build_vision_findings_display_uses_morphology_keys() -> None:
    display = build_vision_findings_display(
        {
            "defect_type": "沟槽",
            "morphology_summary": "可见沟槽形貌",
            "surface_state": "氧化皮剥落",
        },
        img_diag_subtype="defect_ident",
    )
    assert "主体形貌" in display
    assert "表面状态" in display
    assert "缺陷类型" not in display


def test_format_vision_hitl_assistant_block_title() -> None:
    text = format_vision_hitl_assistant_block(
        {"defect_type": "裂纹", "morphology_summary": "线状裂纹沿管轴延伸"},
        img_diag_subtype="defect_ident",
    )
    assert "【图像可见分析】" in text
    assert "视觉臂" not in text
    assert "裂纹" in text


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


def test_interrupt_payload_vision_only_on_first_hitl_round() -> None:
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

    state["hitl_rounds"] = 2
    payload2 = _build_interrupt_payload(state)
    assert payload2["include_vision_preview"] is False
    assert "vision_findings_display" not in payload2
