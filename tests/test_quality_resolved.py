"""V2-P0：结构化 _resolved 满足 L1 时间/区划锚点。"""

from __future__ import annotations

from app.analysis_agent.quality import check_l1_anchors


def test_resolved_empty_query_not_missing_time() -> None:
    out = check_l1_anchors(
        query="",
        analysis_type="subsidence_quarterly",
        options={
            "_resolved": {
                "t_start": "2026-04-01T00:00:00",
                "t_end": "2026-07-01T00:00:00",
                "area": None,
            }
        },
    )
    assert out["missing"] == []
    assert out["degrade_reasons"] == []


def test_unstructured_subsidence_still_requires_zone() -> None:
    out = check_l1_anchors(
        query="请分析2024年第三季度沉降情况",
        analysis_type="subsidence_quarterly",
    )
    assert "zone" in out["missing"]
