from __future__ import annotations

"""编排状态：一次 run/resume 贯穿库锁定、范围、取数与终局 payload。"""

from dataclasses import dataclass, field
from typing import Any

from app.data_query_agent.catalog import LibraryDef
from app.data_query_agent.scope_intent import ScopeIntentResult


@dataclass
class DataQueryAgentState:
    request_id: str
    stream_id: str
    user_id: str
    session_id: str
    query: str
    library_id: str | None = None
    include_hud: bool = True
    expose_sql: bool = False
    max_rows: int = 500
    district: str | None = None
    station_id: str | None = None
    warnings: list[str] = field(default_factory=list)
    library: LibraryDef | None = None
    library_source: str | None = None
    scope: ScopeIntentResult | None = None
    hitl: bool = False
    status: str = "running"
    error: str | None = None
    result: dict[str, Any] | None = None
    extras: dict[str, Any] = field(default_factory=dict)
    started_at: float = 0.0
