from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


@dataclass(frozen=True)
class QuestionScopeIntent:
    boiler: str | None = None
    device_name: str | None = None
    check_location_name: str | None = None
    piperow_name: str | None = None
    row_no: int | None = None
    tube_no: int | None = None
    # 地面沉降域
    station_id: str | None = None
    station_name: str | None = None
    district: str | None = None
    device_type: str | None = None


@dataclass(frozen=True)
class QuestionIntent:
    raw_question: str
    scope_question: str
    time_window: tuple[str, str, str] | None
    scope: QuestionScopeIntent
    time_anchor: tuple[str, str] | None = None
    parse_mode: Literal["rule", "llm", "llm_fallback_rule", "human_confirmed"] = "rule"

    @property
    def time_window_tag(self) -> str | None:
        if self.time_window is None:
            return None
        return self.time_window[2]

    @property
    def time_anchor_tag(self) -> str | None:
        if self.time_anchor is None:
            return None
        return self.time_anchor[1]
