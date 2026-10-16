"""浏览路径：感知树 + 每站最新列表。不走 run-stream / NL2SQL，直接参数化 SQL。

树：device_station_map.yaml 出站，t_station.area 出区（与报告/NL2SQL 同一套覆盖），不读洗数表。
列表：wash.project_name ≡ t_station.name（与 HUD entity_id、树点击 station_id 同源）。
列表默认 catalog.default_library_id（分层标 fcb）；一请求一库一表，不做 fcb+jyb UNION。
"""

from __future__ import annotations

import re
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from app.analysis_agent.deterministics.coverage import (
    load_device_projects_by_type,
    normalize_site_key,
    site_code,
)
from app.core.config import get_app_config
from app.core.logging import get_logger
from app.data_query_agent.annual import compute_annual_delta, resolve_annual_window
from app.data_query_agent.catalog import CatalogError, LibraryDef, get_library_catalog
from app.nl2sql.executor import SQLExecutor

logger = get_logger(__name__)

_IDENT_RE = re.compile(r"^[a-z][a-z0-9_]*$")
_WASH_TABLE_RE = re.compile(r"^t_data_wash_[a-z0-9_]+$")
_VIRTUAL_COL_KEYS = frozenset({"station_id", "station_name", "area", "data_time"})
_UNASSIGNED_DISTRICT = "未分区"

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


def _scope_filters(*, district: str | None, station_id: str | None) -> tuple[str, dict[str, Any]]:
    clauses: list[str] = []
    params: dict[str, Any] = {}
    d = (district or "").strip()
    if d:
        clauses.append("s.area = :district")
        params["district"] = d
    sid = (station_id or "").strip()
    if sid:
        clauses.append("w.project_name = :station_id")
        params["station_id"] = sid
    sql = (" AND " + " AND ".join(clauses)) if clauses else ""
    return sql, params


def build_latest_sql(
    library: LibraryDef,
    *,
    district: str | None = None,
    station_id: str | None = None,
    limit: int | None = None,
    offset: int = 0,
    count_only: bool = False,
) -> tuple[str, dict[str, Any]]:
    """每场地 project_name 取 data_time 最大一行，LEFT JOIN t_station.area。"""
    table = _wash_table(library)
    scope_sql, params = _scope_filters(district=district, station_id=station_id)
    if count_only:
        sql = (
            f"SELECT COUNT(*) AS total FROM ("
            f"SELECT DISTINCT w.project_name "
            f"FROM {table} w "
            f"LEFT JOIN t_station s ON s.name = w.project_name "
            f"WHERE w.project_name IS NOT NULL AND btrim(w.project_name) <> ''{scope_sql}"
            f") t"
        )
        return sql, params

    extras = "".join(f", w.{col} AS {alias}" for col, alias in _physical_selects(library))
    inner = (
        f"SELECT DISTINCT ON (w.project_name) "
        f"w.project_name AS station_id, "
        f"w.project_name AS station_name, "
        f"s.area AS area, "
        f"w.data_time AS data_time"
        f"{extras} "
        f"FROM {table} w "
        f"LEFT JOIN t_station s ON s.name = w.project_name "
        f"WHERE w.project_name IS NOT NULL AND btrim(w.project_name) <> ''{scope_sql} "
        f"ORDER BY w.project_name, w.data_time DESC NULLS LAST"
    )
    sql = (
        f"SELECT * FROM ({inner}) latest "
        f"ORDER BY area NULLS LAST, station_id "
    )
    if limit is not None:
        sql += "LIMIT :limit OFFSET :offset"
        params = dict(params)
        params["limit"] = int(limit)
        params["offset"] = int(offset)
    return sql, params


def build_annual_sql(library: LibraryDef) -> str:
    table = _wash_table(library)
    src = _ident(library.annual_source or library.core_metric, kind="列名")
    return (
        f"SELECT w.project_name AS station_id, w.data_time AS t, w.{src} AS v "
        f"FROM {table} w "
        f"WHERE w.project_name = ANY(:names) "
        f"AND w.data_time >= :start AND w.data_time <= :end "
        f"ORDER BY w.project_name, w.data_time"
    )


def _row_to_list_item(library: LibraryDef, raw: dict[str, Any]) -> dict[str, Any]:
    norm = {str(k).strip(): _cell(v) for k, v in raw.items() if str(k).strip()}
    item: dict[str, Any] = {}
    for col in library.columns:
        if library.annual_key and col.key == library.annual_key:
            item[col.key] = None
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
    item["hud_entity_type"] = "station"
    item["hud_entity_id"] = str(item.get("station_id") or "")
    item["hud_available"] = bool(library.hud_supported and item.get("station_id"))
    return item


async def _fetch_annual(
    executor: SQLExecutor,
    library: LibraryDef,
    items: list[dict[str, Any]],
) -> None:
    if not library.annual_key or not library.annual_source or not items:
        return
    names = [str(x.get("station_id") or "") for x in items if x.get("station_id")]
    if not names:
        return
    win = resolve_annual_window("")
    sql = build_annual_sql(library)
    rows = await executor.execute(
        sql,
        {"names": names, "start": win["start"], "end": win["end"]},
    )
    by_station: dict[str, list[dict[str, Any]]] = {}
    for raw in rows:
        sid = str(raw.get("station_id") or "")
        if not sid:
            continue
        by_station.setdefault(sid, []).append({"t": raw.get("t"), "v": raw.get("v")})
    for item in items:
        sid = str(item.get("station_id") or "")
        delta = compute_annual_delta(
            by_station.get(sid) or [],
            start=win["start"],
            end=win["end"],
        )
        if delta is not None:
            item[library.annual_key] = _cell(delta)


async def fetch_browse_list(
    *,
    library_id: str | None = None,
    district: str | None = None,
    station_id: str | None = None,
    page: int | None = None,
    page_size: int | None = None,
    executor: SQLExecutor | None = None,
) -> dict[str, Any]:
    """进页默认表 / 点树刷新：每站最新一行，强制分页。"""
    library = resolve_browse_library(library_id, required=False)
    page_n, size, offset = normalize_page(page, page_size)
    d = (district or "").strip() or None
    sid = (station_id or "").strip() or None
    exe = executor or SQLExecutor()
    count_sql, count_params = build_latest_sql(
        library, district=d, station_id=sid, count_only=True
    )
    page_sql, page_params = build_latest_sql(
        library, district=d, station_id=sid, limit=size, offset=offset
    )
    logger.info(
        "data_query_agent browse list library=%s district=%s station_id=%s page=%s size=%s",
        library.id,
        d or "",
        sid or "",
        page_n,
        size,
    )
    try:
        count_rows = await exe.execute(count_sql, count_params)
        raw_page = await exe.execute(page_sql, page_params)
    except BrowseRequestError:
        raise
    except Exception as exc:  # noqa: BLE001
        logger.warning("data_query_agent browse list failed library=%s", library.id, exc_info=True)
        raise BrowseRequestError(502, "browse_query_failed", str(exc) or "浏览列表查询失败") from exc

    total = 0
    if count_rows:
        total = int(count_rows[0].get("total") or 0)
    items = [_row_to_list_item(library, r) for r in raw_page]
    warnings: list[str] = []
    try:
        await _fetch_annual(exe, library, items)
        if library.annual_key and any(x.get(library.annual_key) is None for x in items):
            warnings.append("annual_metric_uncomputed")
    except Exception:  # noqa: BLE001
        logger.warning("data_query_agent browse annual failed library=%s", library.id, exc_info=True)
        warnings.append("annual_metric_uncomputed")

    columns = [{"key": c.key, "title": c.title} for c in library.columns]
    return {
        "ok": True,
        "library_id": library.id,
        "display_name": library.display_name,
        "district": d,
        "station_id": sid,
        "result_grain": "station",
        "page": page_n,
        "page_size": size,
        "total": total,
        "has_more": offset + len(items) < total,
        "columns": columns,
        "list": items,
        "hud_enabled": bool(library.hud_supported and items),
        "warnings": warnings,
    }


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
