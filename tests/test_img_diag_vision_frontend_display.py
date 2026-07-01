"""Tests for frontend-only vision narrative display sanitization."""

from __future__ import annotations

from app.llm.graphs.img_diag_vision_display import (
    build_vision_findings_display,
    build_vision_morphology_bullets,
    sanitize_vision_narrative_for_frontend,
)


def test_sanitize_removes_markdown_header_and_json_section() -> None:
    raw = (
        "### Markdown 外观可见分析\n"
        "- **检验标记**：白色弧形标记线\n"
        "- **线状损伤**：标记圈内可见沿管轴细裂纹\n"
        "---\n"
        "### `\n"
        "主缺陷类型：锈蚀\n"
        "---JSON---\n"
        '{"defect_type":"裂纹"}'
    )
    cleaned = sanitize_vision_narrative_for_frontend(raw)
    assert "Markdown" not in cleaned
    assert "###" not in cleaned
    assert "主缺陷类型" not in cleaned
    assert "检验标记" in cleaned
    assert "线状损伤" in cleaned
    assert "细裂纹" in cleaned


def test_build_vision_findings_display_narrative_only() -> None:
    display = build_vision_findings_display(
        {
            "vision_narrative": (
                "### Markdown 外观可见分析\n"
                "- **检验标记**：白圈\n"
                "---\n"
                "ignored structured echo"
            ),
            "defect_type": "周向表面裂纹",
            "defect_types": ["锈蚀", "周向表面裂纹"],
            "defect_signals": ["白圈内横向细线"],
        },
        img_diag_subtype="defect_ident",
    )
    assert "外观可见分析" in display
    assert "检验标记" in display["外观可见分析"]
    assert "主缺陷类型" not in display
    assert "缺陷类型" not in display
    assert "可见形貌要点" not in display


def test_build_vision_morphology_bullets_no_structured_fields() -> None:
    bullets = build_vision_morphology_bullets(
        {
            "vision_narrative": "- **主体形貌**：锈蚀\n- **线状损伤**：周向裂纹",
            "defect_type": "裂纹",
            "defect_types": ["裂纹"],
        },
        img_diag_subtype="defect_ident",
    )
    assert any("主体形貌" in b for b in bullets)
    assert any("裂纹" in b for b in bullets)
    assert not any("主缺陷类型" in b for b in bullets)
