"""V2：五类地降报告 schema 3 + compose。"""

from __future__ import annotations

import json
from pathlib import Path

_REPORTS = Path(__file__).resolve().parents[1] / "configs" / "analysis_agent_reports"

_TYPES = (
    "subsidence_daily",
    "subsidence_weekly",
    "subsidence_monthly",
    "subsidence_quarterly",
    "subsidence_yearly",
)


def test_subsidence_schema_version_3_compose() -> None:
    for name in _TYPES:
        path = _REPORTS / f"{name}.analysis_agent.json"
        raw = json.loads(path.read_text(encoding="utf-8"))
        assert raw.get("schema_version") == 3
        assert isinstance(raw.get("compose"), list) and raw["compose"]


def test_boiler_json_has_no_compose() -> None:
    path = _REPORTS / "overheat_guidance.analysis_agent.json"
    raw = json.loads(path.read_text(encoding="utf-8"))
    assert not raw.get("compose")
    assert int(raw.get("schema_version") or 0) < 3 or raw.get("schema_version") == 2
