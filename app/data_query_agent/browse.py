"""浏览路径：感知树 + 点树列表。不走 run-stream / NL2SQL，直接参数化 SQL。

树：device_station_map.yaml 出站，t_station.area 出区（与报告/NL2SQL 同一套覆盖），不读洗数表。
列表分两种：点库/区 → 代表标全部观测（data_time 倒序）；点站 → 该场地全部设备 × 时间矩阵。
列表默认 catalog.default_library_id（分层标 fcb）；一请求一库一表，不做 fcb+jyb UNION。
"""

from __future__ import annotations

import re
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from app.analysis_agent.deterministics.coverage import (
    canonical_project,
    load_device_projects_by_type,
    load_layered_marks,
    normalize_site_key,
    site_code,
)
from app.core.config import get_app_config
from app.core.logging import get_logger
from app.data_query_agent.catalog import CatalogError, LibraryDef, get_library_catalog
from app.nl2sql.executor import SQLExecutor

logger = get_logger(__name__)

_IDENT_RE = re.compile(r"^[a-z][a-z0-9_]*$")
_WASH_TABLE_RE = re.compile(r"^t_data_wash_[a-z0-9_]+$")
_MARK_SORT_RE = re.compile(r"^([A-Za-z]+)(\d+)-(\d+)([A-Za-z]*)$")
_VIRTUAL_COL_KEYS = frozenset({"station_id", "station_name", "area", "data_time"})
_UNASSIGNED_DISTRICT = "未分区"
RESULT_GRAIN_STATION = "station"
RESULT_GRAIN_DEVICE_TIME = "device_time"

DEFAULT_BROWSE_PAGE = 1
DEFAULT_BROWSE_PAGE_SIZE = 20
MAX_BROWSE_PAGE_SIZE = 100


class BrowseRequestError(ValueError):
    """GET /tree、GET /browse 入参或库配置不合法。"""

    def __init__(self, status_code: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message


def _cell(value: Any) -> Any:
    if isinstance(value, Decimal):
        try:
            return float(value)
        except Exception:  # noqa: BLE001
            return str(value)
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    return value


def _ident(name: str, *, kind: str = "identifier") -> str:
    s = (name or "").strip().lower()
    if not _IDENT_RE.match(s):
        raise BrowseRequestError(400, "invalid_identifier", f"非法{kind}: {name}")
    return s


def _wash_table(library: LibraryDef) -> str:
    table = _ident(library.table, kind="表名")
    if not _WASH_TABLE_RE.match(table):
        raise BrowseRequestError(400, "invalid_table", f"库表不在浏览白名单: {library.table}")
    return table


def resolve_browse_library(library_id: str | None, *, required: bool) -> LibraryDef:
    try:
        catalog = get_library_catalog()
    except CatalogError as exc:
        raise BrowseRequestError(503, "catalog_unavailable", str(exc)) from exc
    raw = (library_id or "").strip().lower()
    if not raw:
        if required:
            raise BrowseRequestError(422, "library_id_required", "library_id 不能为空")
        raw = catalog.default_library_id
    library = catalog.get(raw)
    if library is None:
        raise BrowseRequestError(404, "library_not_found", f"未知 library_id={raw}")
    return library


def normalize_page(page: int | None, page_size: int | None) -> tuple[int, int, int]:
    """返回 (page, page_size, offset)；page 从 1 起。"""
    cfg = get_app_config().data_query_agent
    default_size = max(1, int(getattr(cfg, "browse_default_page_size", DEFAULT_BROWSE_PAGE_SIZE)))
    max_size = max(default_size, int(getattr(cfg, "browse_max_page_size", MAX_BROWSE_PAGE_SIZE)))
    p = DEFAULT_BROWSE_PAGE if page is None else int(page)
    if p < 1:
        raise BrowseRequestError(422, "invalid_page", "page 须 ≥ 1")
    size = default_size if page_size is None else int(page_size)
    if size < 1 or size > max_size:
        raise BrowseRequestError(422, "invalid_page_size", f"page_size 须在 1～{max_size}")
    return p, size, (p - 1) * size


def _known_physical_cols(library: LibraryDef) -> set[str]:
    names = {library.core_metric, library.series_column, library.annual_source or ""}
    names.update(m.source for m in library.core_metrics)
    names.update(m.id for m in library.series_metrics)
    return {n.strip().lower() for n in names if n and n.strip()}


def _physical_selects(library: LibraryDef) -> list[tuple[str, str]]:
    """wash 物理列 → 列表 alias；跳过虚拟列与年变化（年变化二次计算）。"""
    seen: set[str] = set()
    out: list[tuple[str, str]] = []
    known = _known_physical_cols(library)

    def add(col: str, alias: str) -> None:
        c = (col or "").strip().lower()
        a = (alias or "").strip()
        if not c or c in seen or c in _VIRTUAL_COL_KEYS:
            return
        if library.annual_key and (c == library.annual_key or a == library.annual_key):
            return
        if not _IDENT_RE.match(c) or not _IDENT_RE.match(a.lower()):
            return
        seen.add(c)
        out.append((c, a))

    if library.core_metric:
        alias = library.core_metrics[0].id if library.core_metrics else library.core_metric
        add(library.core_metric, alias)
    for m in library.core_metrics:
        add(m.source, m.id)
    display_ids = {m.id for m in library.core_metrics}
    for col in library.columns:
        if col.key in _VIRTUAL_COL_KEYS:
            continue
        if library.annual_key and col.key == library.annual_key:
            continue
        if col.key in display_ids:
            continue
        picked = None
        for alias in col.aliases or (col.key,):
            low = alias.lower()
            if low in _VIRTUAL_COL_KEYS or (library.annual_key and low == library.annual_key):
                continue
            if low in known or low == col.key.lower():
                picked = low
                break
        if picked:
            add(picked, col.key)
    return out


def _mark_sort_key(name: str) -> tuple[Any, ...]:
    """F9-1 / F9-1T / F9-2 按编号排序，与设备矩阵表头一致。"""
    raw = str(name or "").strip()
    m = _MARK_SORT_RE.match(raw)
    if m:
        return (m.group(1).upper(), int(m.group(2)), int(m.group(3)), m.group(4).upper())
    return (raw,)


def _core_value_column(library: LibraryDef) -> str:
    return _ident(library.core_metric or "total_settle", kind="列名")


def representative_mark_pairs(library: LibraryDef) -> tuple[tuple[str, str], ...]:
    """(场地 project_name, 代表标 station_name)。分层标=层位 0；基岩标=J 标。"""
    device = (library.device_type or library.id or "").strip().lower()
    out: list[tuple[str, str]] = []
    seen: set[str] = set()
    for mark in load_layered_marks():
        if device == "fcb":
            if mark.table != "fcb" or mark.monitor_layer != 0:
                continue
        elif device == "jyb":
            if mark.table != "jyb":
                continue
        else:
            return ()
        name = str(mark.station_name or "").strip()
        project = str(mark.project_name or "").strip()
        if not name or name in seen:
            continue
        seen.add(name)
        out.append((project, name))
    return tuple(out)


def representative_marks(library: LibraryDef) -> tuple[str, ...]:
    """分层标=层位 0 代表标编号；基岩标=层位字典中的 J 标。其它库无代表标名单。"""
    return tuple(name for _project, name in representative_mark_pairs(library))


def _project_match_sql(station_id: str) -> tuple[str, dict[str, Any]]:
    """对齐 yaml / 洗数表场地名：精确、全角规范化、F 码前缀。"""
    sid = (station_id or "").strip()
    params: dict[str, Any] = {
        "station_id": sid,
        "norm_key": normalize_site_key(sid),
    }
    clauses = [
        "w.project_name = :station_id",
        "replace(replace(replace(replace(btrim(w.project_name), '（', '('), '）', ')'), '，', ','), ' ', '') = :norm_key",
    ]
    code = site_code(sid)
    if code:
        params["code_like"] = f"{code}(%"
        params["code_like_fw"] = f"{code}（%"
        clauses.append("w.project_name LIKE :code_like")
        clauses.append("w.project_name LIKE :code_like_fw")
    return "(" + " OR ".join(clauses) + ")", params


def build_latest_sql(
    library: LibraryDef,
    *,
    marks: tuple[str, ...] | list[str] | None = None,
    projects: tuple[str, ...] | list[str] | None = None,
    limit: int | None = None,
    offset: int = 0,
    count_only: bool = False,
) -> tuple[str, dict[str, Any]]:
    """代表标 / 无层位库：范围内全部观测行，按 data_time 倒序。"""
    table = _wash_table(library)
    extras = "".join(f", w.{col} AS {alias}" for col, alias in _physical_selects(library))
    mark_list = [str(x).strip() for x in (marks or ()) if str(x).strip()]
    project_list = [str(x).strip() for x in (projects or ()) if str(x).strip()]
    params: dict[str, Any] = {}
    if mark_list:
        where = "w.station_name = ANY(:rep_marks)"
        params["rep_marks"] = mark_list
    elif project_list:
        where = "w.project_name = ANY(:projects)"
        params["projects"] = project_list
    else:
        where = "w.project_name IS NOT NULL AND btrim(w.project_name) <> ''"
    if count_only:
        sql = f"SELECT COUNT(*) AS total FROM {table} w WHERE {where}"
        return sql, params
    sql = (
        f"SELECT w.project_name AS station_id, "
        f"w.project_name AS station_name, "
        f"w.station_name AS mark_name, "
        f"w.data_time AS data_time"
        f"{extras} "
        f"FROM {table} w "
        f"WHERE {where} "
        f"ORDER BY w.data_time DESC NULLS LAST, w.project_name, w.station_name"
    )
    if limit is not None:
        sql += " LIMIT :limit OFFSET :offset"
        params["limit"] = int(limit)
        params["offset"] = int(offset)
    return sql, params


def build_device_names_sql(library: LibraryDef, station_id: str) -> tuple[str, dict[str, Any]]:
    table = _wash_table(library)
    match_sql, params = _project_match_sql(station_id)
    sql = (
        f"SELECT DISTINCT w.station_name AS station_name "
        f"FROM {table} w "
        f"WHERE {match_sql} "
        f"AND w.station_name IS NOT NULL AND btrim(w.station_name) <> '' "
        f"ORDER BY w.station_name"
    )
    return sql, params


def build_device_time_sql(
    library: LibraryDef,
    station_id: str,
    *,
    limit: int | None = None,
    offset: int = 0,
    count_only: bool = False,
) -> tuple[str, dict[str, Any]]:
    table = _wash_table(library)
    match_sql, params = _project_match_sql(station_id)
    if count_only:
        sql = (
            f"SELECT COUNT(*) AS total FROM ("
            f"SELECT DISTINCT w.data_time FROM {table} w "
            f"WHERE {match_sql} AND w.data_time IS NOT NULL"
            f") t"
        )
        return sql, params
    sql = (
        f"SELECT DISTINCT w.data_time AS data_time FROM {table} w "
        f"WHERE {match_sql} AND w.data_time IS NOT NULL "
        f"ORDER BY w.data_time DESC NULLS LAST"
    )
    if limit is not None:
        sql += " LIMIT :limit OFFSET :offset"
        params = dict(params)
        params["limit"] = int(limit)
        params["offset"] = int(offset)
    return sql, params


def build_device_values_sql(library: LibraryDef, station_id: str) -> tuple[str, dict[str, Any]]:
    table = _wash_table(library)
    col = _core_value_column(library)
    match_sql, params = _project_match_sql(station_id)
    sql = (
        f"SELECT w.data_time AS data_time, w.station_name AS station_name, w.{col} AS v "
        f"FROM {table} w "
        f"WHERE {match_sql} "
        f"AND w.station_name IS NOT NULL AND btrim(w.station_name) <> '' "
        f"AND w.data_time = ANY(:times) "
        f"ORDER BY w.data_time DESC NULLS LAST, w.station_name"
    )
    return sql, params


def build_annual_sql(library: LibraryDef, *, by_mark: bool = False) -> str:
    table = _wash_table(library)
    src = _ident(library.annual_source or library.core_metric, kind="列名")
    if by_mark:
        return (
            f"SELECT w.project_name AS station_id, w.data_time AS t, w.{src} AS v "
            f"FROM {table} w "
            f"WHERE w.station_name = ANY(:marks) "
            f"AND w.data_time >= :start AND w.data_time <= :end "
            f"ORDER BY w.project_name, w.data_time"
        )
    return (
        f"SELECT w.project_name AS station_id, w.data_time AS t, w.{src} AS v "
        f"FROM {table} w "
        f"WHERE w.project_name = ANY(:names) "
        f"AND w.data_time >= :start AND w.data_time <= :end "
        f"ORDER BY w.project_name, w.data_time"
    )


def _browse_rep_column_title(col_key: str, title: str) -> str:
    """浏览点库/区：仅展示实测沉降量，不用「历史累计沉降 / 年沉降量」双列口径。"""
    text = title or ""
    if col_key == "total_settle_mm" or "历史累计沉降" in text:
        return "沉降量(mm)" if "(mm)" in text else "沉降量"
    return text


def _browse_rep_columns(library: LibraryDef) -> list[dict[str, str]]:
    out: list[dict[str, str]] = []
    for col in library.columns:
        if library.annual_key and col.key == library.annual_key:
            continue
        out.append({"key": col.key, "title": _browse_rep_column_title(col.key, col.title)})
    return out


def _row_to_list_item(library: LibraryDef, raw: dict[str, Any]) -> dict[str, Any]:
    norm = {str(k).strip(): _cell(v) for k, v in raw.items() if str(k).strip()}
    item: dict[str, Any] = {}
    for col in library.columns:
        if library.annual_key and col.key == library.annual_key:
            continue
        val = None
        for alias in col.aliases or (col.key,):
            if alias in norm and norm[alias] is not None:
                val = norm[alias]
                break
            low = alias.lower()
            if low in norm and norm[low] is not None:
                val = norm[low]
                break
        item[col.key] = val
    sid = item.get("station_id") or norm.get("station_id")
    if sid is not None:
        item["station_id"] = str(sid)
    if item.get("station_name") is None and item.get("station_id"):
        item["station_name"] = item["station_id"]
    if norm.get("mark_name"):
        item["mark_name"] = str(norm["mark_name"])
    item["hud_entity_type"] = "station"
    item["hud_entity_id"] = str(item.get("station_id") or "")
    item["hud_available"] = bool(library.hud_supported and item.get("station_id"))
    return item


def _time_key(value: Any) -> str:
    cell = _cell(value)
    if cell is None:
        return ""
    return str(cell)


def _in_yaml_coverage(library: LibraryDef, project_name: str) -> str | None:
    device = (library.device_type or library.id or "").strip().lower()
    return canonical_project(project_name, device)


async def _station_area_lookup(executor: SQLExecutor) -> tuple[dict[str, str], dict[str, str]]:
    rows = await executor.execute(build_station_area_sql(), {})
    return _index_station_areas(rows)


def _attach_area_and_filter(
    library: LibraryDef,
    items: list[dict[str, Any]],
    *,
    district: str | None,
    by_norm: dict[str, str],
    by_code: dict[str, str],
    project_by_mark: dict[str, str] | None = None,
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    mark_map = project_by_mark or {}
    for item in items:
        raw_id = str(item.get("station_id") or "").strip()
        mark_name = str(item.get("mark_name") or "").strip()
        canon = mark_map.get(mark_name) or _in_yaml_coverage(library, raw_id)
        if not canon:
            continue
        area = str(item.get("area") or "").strip() or _area_for_name(canon, by_norm, by_code)
        area = area or _area_for_name(raw_id, by_norm, by_code)
        if district and _district_label(area) != district:
            continue
        item["station_id"] = canon
        item["station_name"] = canon
        item["area"] = area or None
        item["hud_entity_id"] = canon
        out.append(item)
    return out


def _pivot_device_rows(
    times: list[Any],
    device_names: list[str],
    value_rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    by_time: dict[str, dict[str, Any]] = {}
    order: list[str] = []
    for t in times:
        key = _time_key(t)
        if not key or key in by_time:
            continue
        row: dict[str, Any] = {"data_time": _cell(t), "hud_available": False}
        for name in device_names:
            row[name] = None
        by_time[key] = row
        order.append(key)
    for raw in value_rows:
        key = _time_key(raw.get("data_time"))
        name = str(raw.get("station_name") or "").strip()
        if not key or not name or key not in by_time:
            continue
        by_time[key][name] = _cell(raw.get("v"))
    return [by_time[k] for k in order]


async def _fetch_rep_station_list(
    *,
    library: LibraryDef,
    district: str | None,
    page_n: int,
    size: int,
    offset: int,
    executor: SQLExecutor,
) -> dict[str, Any]:
    pairs = representative_mark_pairs(library)
    coverage = _coverage_names(library)
    columns = _browse_rep_columns(library)
    empty = {
        "ok": True,
        "library_id": library.id,
        "display_name": library.display_name,
        "district": district,
        "station_id": None,
        "result_grain": RESULT_GRAIN_STATION,
        "page": page_n,
        "page_size": size,
        "total": 0,
        "has_more": False,
        "columns": columns,
        "list": [],
        "hud_enabled": False,
        "warnings": [],
    }
    if not pairs and not coverage:
        return empty
    try:
        by_norm, by_code = await _station_area_lookup(executor)
    except BrowseRequestError:
        raise
    except Exception as exc:  # noqa: BLE001
        logger.warning("data_query_agent browse list failed library=%s", library.id, exc_info=True)
        raise BrowseRequestError(502, "browse_query_failed", str(exc) or "浏览列表查询失败") from exc

    if district:
        pairs = tuple(
            (project, mark)
            for project, mark in pairs
            if _district_label(_area_for_name(project, by_norm, by_code)) == district
        )
        coverage = tuple(
            name
            for name in coverage
            if _district_label(_area_for_name(name, by_norm, by_code)) == district
        )
        if not pairs and not coverage:
            return empty

    marks = tuple(mark for _project, mark in pairs)
    projects = coverage if not marks else ()
    project_by_mark = {mark: project for project, mark in pairs}
    count_sql, count_params = build_latest_sql(
        library, marks=marks, projects=projects, count_only=True
    )
    page_sql, page_params = build_latest_sql(
        library, marks=marks, projects=projects, limit=size, offset=offset
    )
    logger.info(
        "data_query_agent browse list view=rep_station library=%s district=%s marks=%s",
        library.id,
        district or "",
        len(marks),
    )
    try:
        count_rows = await executor.execute(count_sql, count_params)
        raw_rows = await executor.execute(page_sql, page_params)
    except BrowseRequestError:
        raise
    except Exception as exc:  # noqa: BLE001
        logger.warning("data_query_agent browse list failed library=%s", library.id, exc_info=True)
        raise BrowseRequestError(502, "browse_query_failed", str(exc) or "浏览列表查询失败") from exc

    total = int(count_rows[0].get("total") or 0) if count_rows else 0
    page_items = _attach_area_and_filter(
        library,
        [_row_to_list_item(library, r) for r in raw_rows],
        district=None,
        by_norm=by_norm,
        by_code=by_code,
        project_by_mark=project_by_mark,
    )
    warnings: list[str] = []

    return {
        "ok": True,
        "library_id": library.id,
        "display_name": library.display_name,
        "district": district,
        "station_id": None,
        "result_grain": RESULT_GRAIN_STATION,
        "page": page_n,
        "page_size": size,
        "total": total,
        "has_more": offset + len(page_items) < total,
        "columns": columns,
        "list": page_items,
        "hud_enabled": bool(library.hud_supported and page_items),
        "warnings": warnings,
    }


async def _fetch_device_matrix(
    *,
    library: LibraryDef,
    station_id: str,
    page_n: int,
    size: int,
    offset: int,
    executor: SQLExecutor,
) -> dict[str, Any]:
    names_sql, names_params = build_device_names_sql(library, station_id)
    count_sql, count_params = build_device_time_sql(library, station_id, count_only=True)
    times_sql, times_params = build_device_time_sql(
        library, station_id, limit=size, offset=offset
    )
    logger.info(
        "data_query_agent browse list view=device_time library=%s station_id=%s page=%s size=%s",
        library.id,
        station_id,
        page_n,
        size,
    )
    try:
        name_rows = await executor.execute(names_sql, names_params)
        count_rows = await executor.execute(count_sql, count_params)
        time_rows = await executor.execute(times_sql, times_params)
    except BrowseRequestError:
        raise
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "data_query_agent browse device matrix failed library=%s station_id=%s",
            library.id,
            station_id,
            exc_info=True,
        )
        raise BrowseRequestError(502, "browse_query_failed", str(exc) or "浏览列表查询失败") from exc

    device_names = sorted(
        {str(r.get("station_name") or "").strip() for r in name_rows if str(r.get("station_name") or "").strip()},
        key=_mark_sort_key,
    )
    times = [r.get("data_time") for r in time_rows if r.get("data_time") is not None]
    total = 0
    if count_rows:
        total = int(count_rows[0].get("total") or 0)
    value_rows: list[dict[str, Any]] = []
    if times:
        values_sql, values_params = build_device_values_sql(library, station_id)
        values_params = dict(values_params)
        values_params["times"] = times
        try:
            value_rows = await executor.execute(values_sql, values_params)
        except BrowseRequestError:
            raise
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "data_query_agent browse device values failed library=%s station_id=%s",
                library.id,
                station_id,
                exc_info=True,
            )
            raise BrowseRequestError(502, "browse_query_failed", str(exc) or "浏览列表查询失败") from exc

    items = _pivot_device_rows(times, device_names, value_rows)
    columns = [{"key": "data_time", "title": "监测时间"}] + [
        {"key": name, "title": name} for name in device_names
    ]
    return {
        "ok": True,
        "library_id": library.id,
        "display_name": library.display_name,
        "district": None,
        "station_id": station_id,
        "result_grain": RESULT_GRAIN_DEVICE_TIME,
        "page": page_n,
        "page_size": size,
        "total": total,
        "has_more": offset + len(items) < total,
        "columns": columns,
        "list": items,
        "hud_enabled": False,
        "warnings": [],
    }


async def fetch_browse_list(
    *,
    library_id: str | None = None,
    district: str | None = None,
    station_id: str | None = None,
    page: int | None = None,
    page_size: int | None = None,
    executor: SQLExecutor | None = None,
) -> dict[str, Any]:
    """点库/区：代表标全部观测（data_time 倒序）；点站：该场地全部设备 × 时间矩阵。强制分页。"""
    library = resolve_browse_library(library_id, required=False)
    page_n, size, offset = normalize_page(page, page_size)
    d = (district or "").strip() or None
    sid = (station_id or "").strip() or None
    exe = executor or SQLExecutor()
    if sid:
        return await _fetch_device_matrix(
            library=library,
            station_id=sid,
            page_n=page_n,
            size=size,
            offset=offset,
            executor=exe,
        )
    return await _fetch_rep_station_list(
        library=library,
        district=d,
        page_n=page_n,
        size=size,
        offset=offset,
        executor=exe,
    )


def _district_label(area: Any) -> str:
    text = str(area or "").strip()
    return text or _UNASSIGNED_DISTRICT


def build_station_area_sql() -> str:
    """仅读 t_station 出区，不 JOIN 洗数表。"""
    return (
        "SELECT name, area FROM t_station "
        "WHERE name IS NOT NULL AND btrim(name) <> ''"
    )


def _coverage_names(library: LibraryDef) -> tuple[str, ...]:
    device = (library.device_type or library.id or "").strip().lower()
    return load_device_projects_by_type().get(device, ())


def _index_station_areas(rows: list[dict[str, Any]]) -> tuple[dict[str, str], dict[str, str]]:
    """normalize(name) → area；唯一 F/J 码 → area（与报告 coverage 同一套对齐）。"""
    by_norm: dict[str, str] = {}
    buckets: dict[str, list[str]] = {}
    for raw in rows:
        name = str(raw.get("name") or "").strip()
        if not name:
            continue
        area = str(raw.get("area") or "").strip()
        key = normalize_site_key(name)
        if key and key not in by_norm:
            by_norm[key] = area
        code = site_code(name)
        if code:
            buckets.setdefault(code, []).append(area)
    by_code = {code: items[0] for code, items in buckets.items() if len(items) == 1}
    return by_norm, by_code


def _area_for_name(name: str, by_norm: dict[str, str], by_code: dict[str, str]) -> str:
    key = normalize_site_key(name)
    if key in by_norm:
        return by_norm[key]
    code = site_code(name)
    if code and code in by_code:
        return by_code[code]
    return ""


def _stations_from_coverage(
    library: LibraryDef,
    names: tuple[str, ...],
    by_norm: dict[str, str],
    by_code: dict[str, str],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    unmatched: list[str] = []
    for name in names:
        sid = str(name or "").strip()
        if not sid:
            continue
        area = _area_for_name(sid, by_norm, by_code)
        if not area:
            unmatched.append(sid)
        rows.append({"station_id": sid, "station_name": sid, "area": area})
    if unmatched:
        logger.warning(
            "data_query_agent tree unmatched t_station library=%s count=%s sample=%s",
            library.id,
            len(unmatched),
            unmatched[:8],
        )
    return rows


def _group_tree_stations(
    library: LibraryDef,
    rows: list[dict[str, Any]],
) -> dict[str, Any]:
    by_area: dict[str, list[dict[str, Any]]] = {}
    for raw in rows:
        sid = str(raw.get("station_id") or "").strip()
        if not sid:
            continue
        district = _district_label(raw.get("area"))
        by_area.setdefault(district, []).append(
            {
                "station_id": sid,
                "station_name": str(raw.get("station_name") or sid),
                "district": district,
                "latest_value": None,
                "data_time": None,
                "unit": library.series_unit or "",
            }
        )
    districts: list[dict[str, Any]] = []
    for name in sorted(by_area, key=lambda x: (x == _UNASSIGNED_DISTRICT, x)):
        stations = sorted(by_area[name], key=lambda x: str(x.get("station_id") or ""))
        districts.append(
            {
                "district": name,
                "station_count": len(stations),
                "stations": stations,
            }
        )
    return {
        "library_id": library.id,
        "display_name": library.display_name,
        "group": library.group_title or "",
        "station_count": sum(d["station_count"] for d in districts),
        "districts": districts,
    }


async def fetch_perception_tree(
    *,
    library_id: str | None = None,
    executor: SQLExecutor | None = None,
) -> dict[str, Any]:
    """库 → 行政区 → 站点。站来自 device_station_map，区来自 t_station；不读洗数表。"""
    try:
        catalog = get_library_catalog()
    except CatalogError as exc:
        raise BrowseRequestError(503, "catalog_unavailable", str(exc)) from exc
    raw_id = (library_id or "").strip().lower()
    if raw_id:
        libraries = [resolve_browse_library(raw_id, required=True)]
    else:
        libraries = list(catalog.libraries)
    exe = executor or SQLExecutor()
    logger.info(
        "data_query_agent browse tree libraries=%s source=device_station_map",
        ",".join(x.id for x in libraries),
    )
    try:
        station_rows = await exe.execute(build_station_area_sql(), {})
    except BrowseRequestError:
        raise
    except Exception as exc:  # noqa: BLE001
        logger.warning("data_query_agent browse tree failed t_station", exc_info=True)
        raise BrowseRequestError(502, "tree_query_failed", str(exc) or "感知树查询失败") from exc
    by_norm, by_code = _index_station_areas(station_rows)
    nodes: list[dict[str, Any]] = []
    for lib in libraries:
        names = _coverage_names(lib)
        rows = _stations_from_coverage(lib, names, by_norm, by_code)
        nodes.append(_group_tree_stations(lib, rows))
    return {
        "ok": True,
        "default_library_id": catalog.default_library_id,
        "libraries": nodes,
    }
