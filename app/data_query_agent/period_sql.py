"""周期窗列表：确定性 PostgreSQL SQL（窗末 − 窗初），不走 LLM / QUALIFY。"""

from __future__ import annotations

import re
from typing import Any

from app.analysis_agent.deterministics.coverage import canonical_project, normalize_site_key
from app.data_query_agent.catalog import LibraryDef
from app.data_query_agent.scope_intent import ScopeIntentResult
from app.nl2sql.executor import SQLExecutor

_IDENT_RE = re.compile(r"^[a-z][a-z0-9_]*$")
_WASH_TABLE_RE = re.compile(r"^t_data_wash_[a-z0-9_]+$")


def _ident(name: str, *, kind: str = "identifier") -> str:
    s = (name or "").strip().lower()
    if not _IDENT_RE.match(s):
        raise ValueError(f"非法{kind}: {name}")
    return s


def _wash_table(library: LibraryDef) -> str:
    table = _ident(library.table, kind="表名")
    if not _WASH_TABLE_RE.match(table):
        raise ValueError(f"库表不在白名单: {library.table}")
    return table


def _period_bounds(scope: ScopeIntentResult) -> tuple[str, str]:
    win = scope.annual_window or {}
    start = str(win.get("start") or "").strip()
    end = str(win.get("end") or "").strip()
    if not start or not end:
        raise ValueError("period_window_missing")
    return start, end


def _station_projects(library: LibraryDef, scope: ScopeIntentResult) -> list[str]:
    cs = scope.confirmed_scope or {}
    raw = str(cs.get("station_name") or cs.get("station_id") or "").strip()
    if not raw:
        return []
    device = (library.device_type or library.id or "").strip().lower()
    canon = canonical_project(raw, device) or raw
    out: list[str] = []
    for name in (canon, raw):
        if name and name not in out:
            out.append(name)
    return out


def _rep_marks(library: LibraryDef, projects: list[str]) -> list[str]:
    from app.data_query_agent.browse import representative_mark_pairs

    pairs = representative_mark_pairs(library)
    if not pairs:
        return []
    if not projects:
        return [mark for _project, mark in pairs]
    wanted = {normalize_site_key(p) for p in projects}
    codes = {p.split("(", 1)[0].upper() for p in projects if p}
    marks: list[str] = []
    for project, mark in pairs:
        if normalize_site_key(project) in wanted:
            marks.append(mark)
            continue
        code = project.split("(", 1)[0].upper()
        if code in codes:
            marks.append(mark)
    return marks


def build_period_list_sql(
    library: LibraryDef,
    scope: ScopeIntentResult,
    *,
    max_rows: int,
) -> tuple[str, dict[str, Any]]:
    """窗内最早/最晚各一行，周期差 = 窗末 − 窗初。PostgreSQL ROW_NUMBER，不用 QUALIFY。"""
    table = _wash_table(library)
    col = _ident(library.core_metric or "total_settle", kind="列名")
    core_alias = _ident(
        (library.core_metrics[0].id if library.core_metrics else library.core_metric) or col,
        kind="列名",
    )
    delta_alias = _ident(library.annual_key or "period_settle_mm", kind="列名")
    t_start, t_end = _period_bounds(scope)
    projects = _station_projects(library, scope)
    marks = _rep_marks(library, projects)
    district = str((scope.confirmed_scope or {}).get("district") or "").strip()
    limit = max(1, int(max_rows))

    inner: list[str] = [
        f"f.{col} IS NOT NULL",
        "f.data_time >= CAST(:t_start AS timestamp)",
        "f.data_time <= CAST(:t_end AS timestamp)",
    ]
    params: dict[str, Any] = {"t_start": t_start, "t_end": t_end, "limit": limit}
    if projects:
        inner.append("f.project_name = ANY(:projects)")
        params["projects"] = projects
    if marks:
        inner.append("f.station_name = ANY(:rep_marks)")
        params["rep_marks"] = marks
    area_pred = ""
    if district:
        params["area"] = district
        area_pred = " AND s.area = CAST(:area AS text)"

    sql = (
        f"SELECT w.project_name AS station_id, w.project_name AS station_name, s.area AS area, "
        f"MAX(CASE WHEN w.rn_desc = 1 THEN w.v END) AS {core_alias}, "
        f"(MAX(CASE WHEN w.rn_desc = 1 THEN w.v END) - MAX(CASE WHEN w.rn_asc = 1 THEN w.v END)) "
        f"AS {delta_alias}, "
        f"MAX(CASE WHEN w.rn_desc = 1 THEN w.data_time END) AS data_time "
        f"FROM ("
        f"SELECT f.project_name, f.data_time, f.{col} AS v, "
        f"ROW_NUMBER() OVER (PARTITION BY f.project_name ORDER BY f.data_time ASC) AS rn_asc, "
        f"ROW_NUMBER() OVER (PARTITION BY f.project_name ORDER BY f.data_time DESC) AS rn_desc "
        f"FROM {table} f "
        f"WHERE {' AND '.join(inner)}"
        f") w "
        f"LEFT JOIN t_station s ON s.name = w.project_name "
        f"WHERE (w.rn_asc = 1 OR w.rn_desc = 1){area_pred} "
        f"GROUP BY w.project_name, s.area "
        f"ORDER BY ABS("
        f"MAX(CASE WHEN w.rn_desc = 1 THEN w.v END) - MAX(CASE WHEN w.rn_asc = 1 THEN w.v END)"
        f") DESC NULLS LAST, w.project_name "
        f"LIMIT :limit"
    )
    return sql, params


async def fetch_period_list(
    library: LibraryDef,
    scope: ScopeIntentResult,
    *,
    max_rows: int,
    executor: SQLExecutor | None = None,
) -> tuple[str, list[dict[str, Any]]]:
    sql, params = build_period_list_sql(library, scope, max_rows=max_rows)
    exe = executor or SQLExecutor()
    rows = await exe.execute(sql, params)
    return sql, [r for r in (rows or []) if isinstance(r, dict)]
