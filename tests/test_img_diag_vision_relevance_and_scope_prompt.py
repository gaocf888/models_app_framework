"""看图诊断：视觉锅炉相关性展示 + 台账未解析 HITL 文案。"""

from __future__ import annotations

import pytest

from app.llm.graphs.img_diag_scope_display import (
    SCOPE_HITL_NOT_PARSED_PROMPT,
    build_scope_hitl_confirm_reply_example,
)
from app.llm.graphs.img_diag_vision_display import (
    VISION_BOILER_REJECTION_DEFAULT,
    build_vision_findings_display,
    build_vision_morphology_bullets,
    is_vision_boiler_relevance_rejected,
    vision_boiler_rejection_message,
)
from app.models.analysis import AnalysisImgDiagRequest, AnalysisOptions


def test_vision_rejection_display_message() -> None:
    data = {
        "is_boiler_pressure_part_image": False,
        "user_message": "当前图片非锅炉相关图片，请重新上传",
    }
    assert is_vision_boiler_relevance_rejected(data) is True
    assert vision_boiler_rejection_message(data) == "当前图片非锅炉相关图片，请重新上传"
    bullets = build_vision_morphology_bullets(data, img_diag_subtype="defect_ident")
    assert bullets == ["当前图片非锅炉相关图片，请重新上传"]
    display = build_vision_findings_display(data, img_diag_subtype="defect_ident")
    assert display["说明"] == "当前图片非锅炉相关图片，请重新上传"


def test_vision_rejection_default_message_when_flag_false_only() -> None:
    data = {"is_boiler_pressure_part_image": False}
    assert vision_boiler_rejection_message(data) == VISION_BOILER_REJECTION_DEFAULT


def test_vision_non_boiler_long_narrative_still_rejected() -> None:
    data = {
        "is_boiler_pressure_part_image": False,
        "vision_narrative": (
            "- 主体形貌：可见工业电机外壳\n"
            "- 表面状态：无明显管壁特征\n"
            "- 其他：与锅炉受压管系无关"
        ),
    }
    assert is_vision_boiler_relevance_rejected(data) is True
    msg = vision_boiler_rejection_message(data)
    assert msg is not None
    assert "重新上传" in msg
    assert "工业电机" not in msg


def test_vision_rejection_ignores_non_boiler_narrative_without_reupload_hint() -> None:
    data = {
        "is_boiler_pressure_part_image": False,
        "vision_narrative": "照片内容为办公文档截图，与锅炉检验无关",
        "preliminary_visual_conclusion": "非锅炉设备",
    }
    assert vision_boiler_rejection_message(data) == VISION_BOILER_REJECTION_DEFAULT


def test_vision_relevant_still_builds_morphology() -> None:
    data = {
        "is_boiler_pressure_part_image": True,
        "vision_narrative": "- **主体形貌**：可见沟槽沿管轴延伸\n- **表面状态**：氧化皮",
    }
    assert is_vision_boiler_relevance_rejected(data) is False
    bullets = build_vision_morphology_bullets(data, img_diag_subtype="defect_ident")
    assert any("主体形貌" in b for b in bullets)


def test_confirm_reply_example_not_parsed_prompt() -> None:
    example = build_scope_hitl_confirm_reply_example(
        {
            "prompt": SCOPE_HITL_NOT_PARSED_PROMPT,
            "interrupt_reason": "missing:boiler,device_name",
            "missing_fields": ["机组", "受热面"],
        }
    )
    assert "机组应为" in example


def test_defect_ident_allows_empty_query_with_images() -> None:
    req = AnalysisImgDiagRequest(
        user_id="u1",
        session_id="s1",
        img_diag_subtype="defect_ident",
        query="",
        image_urls=["http://example.com/a.jpg"],
        options=AnalysisOptions(),
    )
    assert req.query == ""
    assert req.image_urls == ["http://example.com/a.jpg"]


def test_leakage_burst_still_requires_query() -> None:
    with pytest.raises(ValueError, match="query must not be empty"):
        AnalysisImgDiagRequest(
            user_id="u1",
            session_id="s1",
            img_diag_subtype="leakage_burst",
            query="",
            image_urls=["http://example.com/a.jpg"],
            options=AnalysisOptions(),
        )
