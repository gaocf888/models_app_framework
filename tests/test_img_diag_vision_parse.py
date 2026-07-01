"""Tests for img_diag vision lane Markdown + JSON parsing (scheme C)."""

from __future__ import annotations

from app.llm.graphs.img_diag_vision_parse import (
    parse_vision_lane_llm_output,
    split_vision_narrative_and_json,
)


def test_split_vision_narrative_and_json_with_marker() -> None:
    raw = (
        "- 白圈内可见周向裂纹\n"
        "- 表面重度锈蚀\n"
        "---JSON---\n"
        '{"defect_type":"周向表面裂纹","is_boiler_pressure_part_image":true}'
    )
    narrative, blob = split_vision_narrative_and_json(raw)
    assert "周向裂纹" in narrative
    assert blob.startswith("{")


def test_parse_vision_lane_llm_output_scheme_c() -> None:
    raw = (
        "- 标记圈内有多条横跨管轴细裂纹\n"
        "---JSON---\n"
        "{"
        '"is_boiler_pressure_part_image":true,'
        '"defect_type":"周向表面裂纹",'
        '"defect_types":["表面腐蚀","周向表面裂纹"],'
        '"defect_signals":["白圈内横向细线2～3条"]'
        "}"
    )
    parsed = parse_vision_lane_llm_output(raw)
    assert parsed.get("defect_type") == "周向表面裂纹"
    assert "周向表面裂纹" in parsed.get("defect_types", [])
    assert "横跨管轴" in parsed.get("vision_narrative", "")


def test_parse_vision_lane_llm_output_pure_json_legacy() -> None:
    raw = '{"defect_type":"裂纹","defect_orientation":"横跨管轴"}'
    parsed = parse_vision_lane_llm_output(raw)
    assert parsed.get("defect_type") == "裂纹"
    assert "vision_narrative" not in parsed or not parsed.get("vision_narrative")


def test_parse_vision_lane_llm_output_markdown_prefix_without_marker() -> None:
    raw = (
        "外观可见分析：\n"
        "- 白圈内周向裂纹\n"
        '{"defect_type":"周向表面裂纹","is_boiler_pressure_part_image":true}'
    )
    parsed = parse_vision_lane_llm_output(raw)
    assert parsed.get("defect_type") == "周向表面裂纹"
    assert "周向裂纹" in parsed.get("vision_narrative", "")
