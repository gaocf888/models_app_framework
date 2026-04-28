from __future__ import annotations

import pytest

from app.models.inspection_extract import (
    DefectType,
    DetectionType,
    InspectionRecord,
    ReplaceFlag,
)


def test_inspection_record_measurement_requires_empty_defect() -> None:
    with pytest.raises(Exception):
        InspectionRecord(
            检测位置="右墙B02",
            行号="1",
            管号="12",
            壁厚=5.2,
            检测类型=DetectionType.MEASUREMENT,
            缺陷类型=DefectType.WEAR,
            是否换管=ReplaceFlag.NO,
        )


def test_inspection_record_dump_with_alias() -> None:
    row = InspectionRecord(
        检测位置="右墙B02",
        行号="1",
        管号="12",
        壁厚=4.8,
        检测类型=DetectionType.DEFECT,
        缺陷类型=DefectType.SURFACE_EROSION,
        是否换管=ReplaceFlag.YES,
    )
    payload = row.model_dump(by_alias=True)
    assert payload["检测位置"] == "右墙B02"
    assert payload["检测类型"] == "缺陷"
    assert payload["是否换管"] == "是"

