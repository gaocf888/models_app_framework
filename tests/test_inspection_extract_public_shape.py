from __future__ import annotations

from app.models.inspection_extract import (
    DetectionType,
    InspectionExtractResponse,
    InspectionExtractTrace,
    InspectionRecord,
    InspectionSummary,
    ReplaceFlag,
    normalize_inspect_prompt_version,
    shape_public_record_dict,
)


def test_normalize_inspect_prompt_version() -> None:
    assert normalize_inspect_prompt_version("v2") == "v2"
    assert normalize_inspect_prompt_version("inspection_extract:v2") == "v2"
    assert normalize_inspect_prompt_version("inspection_extract:v1") == "v1"
    assert normalize_inspect_prompt_version(None) == "v1"


def test_shape_public_record_dict_v2_drops_location_alias() -> None:
    raw = {
        "受热面": "水冷壁A3吹灰器下",
        "检测位置": "水冷壁A3吹灰器下",
        "行号": "1",
        "管号": "-2",
        "壁厚": 5.8,
    }
    out = shape_public_record_dict(raw, prompt_version="v2")
    assert "检测位置" not in out
    assert "location" not in out
    assert out["受热面"] == "水冷壁A3吹灰器下"
    assert out["管号"] == "-2"


def test_shape_public_record_dict_v1_keeps_location() -> None:
    raw = {"检测位置": "右墙", "行号": "1", "管号": "2", "壁厚": 7.0}
    out = shape_public_record_dict(raw, prompt_version="v1")
    assert out["检测位置"] == "右墙"
    assert "受热面" not in out


def test_inspection_extract_response_serializer_v2() -> None:
    resp = InspectionExtractResponse(
        ok=True,
        records=[
            InspectionRecord(
                检测位置="水冷壁A3吹灰器下",
                行号="1",
                管号="-2",
                壁厚=5.8,
                检测类型=DetectionType.MEASUREMENT,
                是否换管=ReplaceFlag.NO,
            )
        ],
        summary=InspectionSummary(total=1),
        trace=InspectionExtractTrace(
            parse_route="docx_v2",
            llm_model="m",
            prompt_version="inspection_extract:v2",
            parse_latency_ms=1,
            llm_latency_ms=2,
        ),
    )
    data = resp.model_dump(by_alias=True)
    assert data["records"][0].get("受热面") == "水冷壁A3吹灰器下"
    assert "检测位置" not in data["records"][0]


def test_inspection_extract_response_serializer_v1() -> None:
    resp = InspectionExtractResponse(
        ok=True,
        records=[
            InspectionRecord(
                检测位置="右墙",
                行号="1",
                管号="2",
                壁厚=7.0,
                检测类型=DetectionType.MEASUREMENT,
                是否换管=ReplaceFlag.NO,
            )
        ],
        summary=InspectionSummary(total=1),
        trace=InspectionExtractTrace(
            parse_route="docx_v2",
            llm_model="m",
            prompt_version="inspection_extract:v1",
            parse_latency_ms=1,
            llm_latency_ms=2,
        ),
    )
    data = resp.model_dump(by_alias=True)
    assert data["records"][0].get("检测位置") == "右墙"
    assert "受热面" not in data["records"][0]


def test_reload_v2_final_response_accepts_surface_field() -> None:
    payload = {
        "ok": True,
        "records": [
            {
                "受热面": "水冷壁A3吹灰器下",
                "行号": "1",
                "管号": "-2",
                "壁厚": 5.8,
                "检测类型": "测厚",
                "缺陷类型": None,
                "是否换管": "否",
            }
        ],
        "summary": {"total": 1, "defect_count": 0, "replace_count": 0, "warnings": []},
        "trace": {
            "parse_route": "docx_v2",
            "llm_model": "m",
            "prompt_version": "inspection_extract:v2",
            "parse_latency_ms": 1,
            "llm_latency_ms": 2,
        },
    }
    resp = InspectionExtractResponse.model_validate(payload)
    assert resp.records[0].location == "水冷壁A3吹灰器下"
    dumped = resp.model_dump(by_alias=True)
    assert dumped["records"][0]["受热面"] == "水冷壁A3吹灰器下"
    assert "检测位置" not in dumped["records"][0]
