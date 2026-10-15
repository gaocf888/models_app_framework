"""请求窗无配对数据时，回退到最近一个同粒度、有配对行的完整周期。"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Protocol

from app.analysis_agent.deterministics.fcb_endpoints import run_sql_template
from app.analysis_agent.period import (
    FALLBACK_MAX_STEPS,
    complete_window_ending_on_or_before,
    overlay_resolved_window,
    parse_resolved_dt,
    previous_complete_before,
    step_back_complete_window,
)
from app.analysis_agent.slots.specs import is_subsidence_type
from app.core.logging import get_logger
from app.nl2sql.executor import SQLExecutor

logger = get_logger(__name__)

REASON_NO_DATA = "no_paired_data_in_requested_window"
REASON_EXHAUSTED = "period_fallback_exhausted"
REASON_PROBE_ERROR = "period_fallback_probe_error"


def fallback_degrade_reasons(resolved: dict[str, Any] | None) -> list[str]:
    data = resolved if isinstance(resolved, dict) else {}
    out: list[str] = []
    if data.get("fallback") and data.get("fallback_reason") == REASON_NO_DATA:
        out.append("period_fallback")
    if data.get("fallback_reason") == REASON_EXHAUSTED:
        out.append("period_fallback_exhausted")
    if data.get("fallback_reason") == REASON_PROBE_ERROR:
        out.append("period_fallback_probe_error")
    return out


class PeriodDataProbe(Protocol):
    async def has_paired(self, resolved: dict[str, Any]) -> bool: ...

    async def max_data_time_before(self, before: datetime, resolved: dict[str, Any]) -> datetime | None: ...


class SqlPeriodDataProbe:
    def __init__(self, executor: SQLExecutor) -> None:
        self._executor = executor

    async def has_paired(self, resolved: dict[str, Any]) -> bool:
        rows, _sql = await run_sql_template(
            sql_file="period_probe_paired.sql",
            resolved=resolved,
            executor=self._executor,
            max_rows=1,
        )
        return bool(rows)

    async def max_data_time_before(self, before: datetime, resolved: dict[str, Any]) -> datetime | None:
        probe = dict(resolved)
        probe["t_end"] = before.strftime("%Y-%m-%dT%H:%M:%S")
        if not probe.get("t_start"):
            probe["t_start"] = "1990-01-01T00:00:00"
        rows, _sql = await run_sql_template(
            sql_file="period_probe_max_time.sql",
            resolved=probe,
            executor=self._executor,
            max_rows=1,
        )
        if not rows:
            return None
        raw = rows[0].get("max_data_time")
        if raw is None:
            return None
        return parse_resolved_dt(raw)


async def apply_period_data_fallback(
    analysis_type: str,
    options: dict[str, Any] | None,
    *,
    probe: PeriodDataProbe | None = None,
    executor: SQLExecutor | None = None,
) -> dict[str, Any]:
    """有配对则保持请求窗；否则按日/周/月/季/年整步回退。"""
    opts = dict(options or {})
    if not is_subsidence_type(analysis_type):
        return opts
    resolved_in = opts.get("_resolved")
    if not isinstance(resolved_in, dict) or not resolved_in.get("t_start") or not resolved_in.get("t_end"):
        return opts
    impl: PeriodDataProbe = probe or SqlPeriodDataProbe(executor or SQLExecutor())

    resolved = dict(resolved_in)
    resolved.setdefault("requested_t_start", resolved["t_start"])
    resolved.setdefault("requested_t_end", resolved["t_end"])
    requested_start = parse_resolved_dt(resolved["t_start"])
    cap = max(1, int(FALLBACK_MAX_STEPS.get(analysis_type, 12)))

    try:
        if await impl.has_paired(resolved):
            resolved["fallback"] = False
            resolved.pop("fallback_reason", None)
            opts["_resolved"] = resolved
            return opts
        current_start, current_end = await _first_fallback_window(
            analysis_type, requested_start, resolved, impl
        )
        steps = 0
        last_start, last_end = current_start, current_end
        while steps < cap:
            steps += 1
            candidate = overlay_resolved_window(
                resolved, analysis_type=analysis_type, start=current_start, end=current_end
            )
            if await impl.has_paired(candidate):
                candidate["fallback"] = True
                candidate["fallback_reason"] = REASON_NO_DATA
                candidate["fallback_steps"] = steps
                opts["_resolved"] = candidate
                logger.info(
                    "period_fallback type=%s steps=%s %s -> %s",
                    analysis_type,
                    steps,
                    resolved.get("requested_t_start"),
                    candidate.get("t_start"),
                )
                return opts
            last_start, last_end = current_start, current_end
            current_start, current_end = step_back_complete_window(
                analysis_type, current_start, current_end
            )
        exhausted = overlay_resolved_window(
            resolved, analysis_type=analysis_type, start=last_start, end=last_end
        )
        exhausted["fallback"] = True
        exhausted["fallback_reason"] = REASON_EXHAUSTED
        exhausted["fallback_steps"] = cap
        opts["_resolved"] = exhausted
        logger.info(
            "period_fallback exhausted type=%s cap=%s last=%s",
            analysis_type,
            cap,
            exhausted.get("t_start"),
        )
        return opts
    except Exception:  # noqa: BLE001
        logger.warning("period_probe failed; keep requested window", exc_info=True)
        resolved["fallback"] = False
        resolved["fallback_reason"] = REASON_PROBE_ERROR
        opts["_resolved"] = resolved
        return opts


async def _first_fallback_window(
    analysis_type: str,
    requested_start: datetime,
    resolved: dict[str, Any],
    impl: PeriodDataProbe,
) -> tuple[datetime, datetime]:
    max_ts = await impl.max_data_time_before(requested_start, resolved)
    if max_ts is not None:
        return complete_window_ending_on_or_before(
            analysis_type, before=requested_start, instant=max_ts
        )
    return previous_complete_before(analysis_type, requested_start)
