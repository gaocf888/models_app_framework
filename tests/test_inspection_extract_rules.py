from __future__ import annotations

from app.models.inspection_extract import DefectType, DetectionType, ReplaceFlag
from app.services.inspection_extract_service import InspectionExtractService


def test_canonicalize_maps_synonym_to_allowed_defect_type() -> None:
    svc = InspectionExtractService()
    row = svc._canonicalize_record(  # noqa: SLF001
        {
            "检测位置": "右墙B02",
            "行号": "1",
            "管号": "12",
            "壁厚": "4.7",
            "检测类型": "缺陷",
            "缺陷类型": "吹蚀",
            "是否换管": "是",
        },
        threshold_rules=[],
    )
    assert row["detection_type"] == DetectionType.DEFECT
    assert row["defect_type"] == DefectType.SURFACE_EROSION
    assert row["replaced"] == ReplaceFlag.YES


def test_canonicalize_defaults_measurement_without_defect() -> None:
    svc = InspectionExtractService()
    row = svc._canonicalize_record(  # noqa: SLF001
        {
            "检测位置": "右墙B02",
            "行号": "1",
            "管号": "5",
            "壁厚": "6.6",
            "检测类型": "测厚",
            "是否换管": "否",
        },
        threshold_rules=[],
    )
    assert row["detection_type"] == DetectionType.MEASUREMENT
    assert row["defect_type"] is None
    assert row["replaced"] == ReplaceFlag.NO


def test_threshold_binding_marks_defect_when_below_threshold() -> None:
    svc = InspectionExtractService()
    text = """
    右墙B02吹灰器下部管道测厚
    低于3.15mm超标
    """
    rules = svc._extract_threshold_rules(text)  # noqa: SLF001
    row = svc._canonicalize_record(  # noqa: SLF001
        {
            "检测位置": "右墙B02吹灰器",
            "行号": "2",
            "管号": "11",
            "壁厚": "3.03",
            "是否换管": "否",
        },
        threshold_rules=rules,
    )
    assert row["detection_type"] == DetectionType.DEFECT


def test_threshold_binding_uses_best_location_match() -> None:
    svc = InspectionExtractService()
    text = """
    右墙B02吹灰器下部管道测厚
    低于3.15mm超标
    左墙1-2测厚位置
    低于4.50mm超标
    """
    rules = svc._extract_threshold_rules(text)  # noqa: SLF001
    row = svc._canonicalize_record(  # noqa: SLF001
        {
            "检测位置": "左墙1-2",
            "行号": "1",
            "管号": "3",
            "壁厚": "4.4",
            "是否换管": "否",
        },
        threshold_rules=rules,
    )
    assert row["detection_type"] == DetectionType.DEFECT


def test_threshold_binding_no_global_fallback_with_multiple_rules() -> None:
    svc = InspectionExtractService()
    rules = [
        {"threshold": 3.15, "location_hint": "右墙B02", "tokens": ["右墙", "B02"], "line_idx": 10},
        {"threshold": 4.50, "location_hint": "左墙1-2", "tokens": ["左墙", "1", "2"], "line_idx": 20},
    ]
    picked = svc._select_threshold_for_location(  # noqa: SLF001
        "前墙未知区域",
        threshold_rules=rules,
        row_no="1",
        line_index={},
    )
    assert picked[0] is None
    assert picked[1] == "未命中"


def test_threshold_binding_prefers_nearby_paragraph_rule() -> None:
    svc = InspectionExtractService()
    parsed_text = """
    右墙B02吹灰器
    描述段落A
    低于3.15mm超标
    左墙1-2
    描述段落B
    低于4.50mm超标
    """
    rules = svc._extract_threshold_rules(parsed_text)  # noqa: SLF001
    line_index = svc._build_line_index(parsed_text)  # noqa: SLF001
    picked = svc._select_threshold_for_location(  # noqa: SLF001
        "右墙B02吹灰器",
        threshold_rules=rules,
        row_no="12",
        line_index=line_index,
    )
    assert picked[0] == 3.15
    assert picked[1] == "段落近邻"


def test_threshold_source_written_into_warnings() -> None:
    svc = InspectionExtractService()
    rules = [{"threshold": 3.15, "location_hint": "右墙B02", "tokens": ["右墙", "B02"], "line_idx": 1}]
    row = svc._canonicalize_record(  # noqa: SLF001
        {
            "检测位置": "右墙B02",
            "行号": "1",
            "管号": "12",
            "壁厚": "3.0",
            "是否换管": "否",
        },
        threshold_rules=rules,
        line_index={"右墙B02": 1},
    )
    assert any(str(x).startswith("阈值命中来源:") for x in row["warnings"])

