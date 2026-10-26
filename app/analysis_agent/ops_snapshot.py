"""运维报告：绑定窗末日 snapshot 批次（跟沉降回退后的 _resolved 走）。

先取半开窗末日所在日历日最后一批 snapshot_time；该日无批次则退全局最新。
五条 om_*.sql 共用这一次探测结果。不复用沉降 period_probe，也不改聊天 NL2SQL 最新批规则。
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Protocol

from app.analysis_agent.deterministics.fcb_endpoints import run_sql_template
from app.analysis_agent.period import _fmt, parse_resolved_dt
from app.analysis_agent.slots.specs import is_subsidence_type
from app.core.logging import get_logger
from app.nl2sql.executor import SQLExecutor

logger = get_logger(__name__)

REASON_OPS_NO_DAY_BATCH = "no_ops_snapshot_on_window_end_day"
REASON_OPS_PROBE_ERROR = "ops_snapshot_probe_error"
REASON_OPS_EMPTY = "ops_snapshot_empty"


def ops_snap_day_bounds(t_end: Any) -> tuple[datetime, datetime]:
    """半开窗 [t_start, t_end)：午夜上界取前一自然日，否则取 t_end 当天。"""
    end = parse_resolved_dt(t_end)
    if end.hour == 0 and end.minute == 0 and end.second == 0 and end.microsecond == 0:
        day = (end - timedelta(days=1)).date()
    else:
        day = end.date()
    start = datetime(day.year, day.month, day.day)
    return start, start + timedelta(days=1)


class OpsSnapshotProbe(Protocol):
    async def pick(
        self,
        day_start: datetime,
        day_end: datetime,
        resolved: dict[str, Any],
    ) -> tuple[datetime | None, bool]: ...


class SqlOpsSnapshotProbe:
    def __init__(self, executor: SQLExecutor) -> None:
        self._executor = executor

    async def pick(
        self,
        day_start: datetime,
        day_end: datetime,
        resolved: dict[str, Any],
    ) -> tuple[datetime | None, bool]:
        rows, _sql = await run_sql_template(
            sql_file="om_snapshot_pick.sql",
            resolved=resolved,
            executor=self._executor,
            extra_params={"snap_day_start": day_start, "snap_day_end": day_end},
            max_rows=1,
        )
        if not rows:
            return None, True
        raw = rows[0].get("snapshot_time")
        if raw is None:
            return None, True
        fallback = bool(rows[0].get("ops_fallback"))
        return parse_resolved_dt(raw), fallback


def _write_ops_bind(
    resolved: dict[str, Any],
    *,
    day_start: datetime,
    snapshot: datetime | None,
    fallback: bool,
    reason: str | None,
) -> dict[str, Any]:
    out = dict(resolved)
    out["ops_snap_day"] = day_start.strftime("%Y-%m-%d")
    out["ops_fallback"] = bool(fallback)
    if snapshot is not None:
        out["ops_snapshot_time"] = _fmt(snapshot)
    else:
        out.pop("ops_snapshot_time", None)
    if fallback and reason:
        out["ops_fallback_reason"] = reason
    else:
        out.pop("ops_fallback_reason", None)
    return out


async def apply_ops_snapshot_bind(
    analysis_type: str,
    options: dict[str, Any] | None,
    *,
    probe: OpsSnapshotProbe | None = None,
    executor: SQLExecutor | None = None,
) -> dict[str, Any]:
    """写 _resolved.ops_snapshot_time；必须在 apply_period_data_fallback 之后调用。"""
    opts = dict(options or {})
    if not is_subsidence_type(analysis_type):
        return opts
    resolved_in = opts.get("_resolved")
    if not isinstance(resolved_in, dict) or not resolved_in.get("t_end"):
        return opts
    if resolved_in.get("ops_snapshot_time"):
        return opts

    resolved = dict(resolved_in)
    day_start, day_end = ops_snap_day_bounds(resolved["t_end"])
    impl: OpsSnapshotProbe = probe or SqlOpsSnapshotProbe(executor or SQLExecutor())
    try:
        snapshot, fallback = await impl.pick(day_start, day_end, resolved)
    except Exception:  # noqa: BLE001
        logger.warning("ops_snapshot probe failed; om sql will miss snapshot bind", exc_info=True)
        opts["_resolved"] = _write_ops_bind(
            resolved,
            day_start=day_start,
            snapshot=None,
            fallback=True,
            reason=REASON_OPS_PROBE_ERROR,
        )
        return opts

    if snapshot is None:
        reason = REASON_OPS_EMPTY
        fallback = True
    elif fallback:
        reason = REASON_OPS_NO_DAY_BATCH
    else:
        reason = None
    bound = _write_ops_bind(
        resolved,
        day_start=day_start,
        snapshot=snapshot,
        fallback=fallback,
        reason=reason,
    )
    opts["_resolved"] = bound
    logger.info(
        "ops_snapshot type=%s snap_day=%s snapshot_time=%s fallback=%s t_end=%s",
        analysis_type,
        bound.get("ops_snap_day"),
        bound.get("ops_snapshot_time"),
        bound.get("ops_fallback"),
        resolved.get("t_end"),
    )
    return opts
