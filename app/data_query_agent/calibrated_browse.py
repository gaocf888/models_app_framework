"""人工清洗点站点浏览：一站一表宽表 → 与准实时相同的 device_time 矩阵。

不走 NL2SQL / schema_routing 的 calibrated schema 开关。表在 public，表名仅来自白名单 YAML。
"""

from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

from app.analysis_agent.deterministics.coverage import (
    canonical_project,
    normalize_site_key,
    site_code,
)
from app.core.logging import get_logger
from app.data_query_agent.catalog import LibraryDef
from app.nl2sql.executor import SQLExecutor

logger = get_logger(__name__)

DATA_SOURCE_REALTIME = "realtime"
DATA_SOURCE_CALIBRATED = "calibrated"
CALIBRATED_LIBRARY_IDS = frozenset({"fcb", "dxswj", "kxsylj"})

_CONFIG_PATH = (
    Path(__file__).resolve().parents[2]
    / "configs"
    / "data_query_agent"
    / "calibrated_station_tables.yaml"
)
_SHEET_TABLE_RE = re.compile(r"^sheet_[a-z0-9]+$")
_COL_RE = re.compile(r"^[A-Za-z0-9_-]+$")
_DEVICE_PREFIX_RE = re.compile(r"^([A-Za-z]+)(.*)$")
_REALTIME_ALIASES = frozenset({"", "realtime", "default", "public"})
_CALIBRATED_ALIASES = frozenset({"calibrated", "checked", "manual"})


def _browse_error(status: int, code: str, message: str, extra: dict[str, Any] | None = None):
    from app.data_query_agent.browse import BrowseRequestError

    return BrowseRequestError(status, code, message, extra)


def parse_browse_data_source(raw: str | None) -> str:
    text = (raw or "").strip().lower()
    if text in _REALTIME_ALIASES:
        return DATA_SOURCE_REALTIME
    if text in _CALIBRATED_ALIASES:
        return DATA_SOURCE_CALIBRATED
    raise _browse_error(422, "invalid_data_source", "data_source 须为 realtime 或 calibrated")


def _cell(value: Any) -> Any:
    from app.data_query_agent.browse import _cell as browse_cell

    return browse_cell(value)


@lru_cache(maxsize=1)
def load_calibrated_table_config() -> dict[str, Any]:
    if not _CONFIG_PATH.is_file():
        logger.warning("calibrated station tables yaml missing path=%s", _CONFIG_PATH)
        return {}
    data = yaml.safe_load(_CONFIG_PATH.read_text(encoding="utf-8"))
    return data if isinstance(data, dict) else {}


def clear_calibrated_table_cache() -> None:
    load_calibrated_table_config.cache_clear()
    _station_index.cache_clear()


@lru_cache(maxsize=1)
def _station_index() -> dict[str, dict[str, str]]:
    """library_id → {normalize_site_key / 权威名 / 唯一站码 → table}。"""
    cfg = load_calibrated_table_config()
    libraries = cfg.get("libraries") if isinstance(cfg.get("libraries"), dict) else {}
    out: dict[str, dict[str, str]] = {}
    for lid, raw in libraries.items():
        lib_id = str(lid or "").strip().lower()
        if lib_id not in CALIBRATED_LIBRARY_IDS or not isinstance(raw, dict):
            continue
        stations = raw.get("stations") if isinstance(raw.get("stations"), dict) else {}
        mapping: dict[str, str] = {}
        code_buckets: dict[str, list[tuple[str, str]]] = {}
        for name, table in stations.items():
            station = str(name or "").strip()
            sheet = str(table or "").strip().lower()
            if not station or not _SHEET_TABLE_RE.match(sheet):
                logger.warning(
                    "skip invalid calibrated mapping library=%s station=%s table=%s",
                    lib_id,
                    station,
                    table,
                )
                continue
            mapping[normalize_site_key(station)] = sheet
            mapping[station] = sheet
            canon = canonical_project(station, lib_id)
            if canon:
                mapping[normalize_site_key(canon)] = sheet
                mapping[canon] = sheet
            code = site_code(station)
            if code:
                code_buckets.setdefault(code, []).append((station, sheet))
        for code, items in code_buckets.items():
            tables = {t for _n, t in items}
            if len(tables) == 1:
                mapping[code] = next(iter(tables))
        out[lib_id] = mapping
    return out


def _library_cfg(library_id: str) -> dict[str, Any]:
    cfg = load_calibrated_table_config()
    libraries = cfg.get("libraries") if isinstance(cfg.get("libraries"), dict) else {}
    raw = libraries.get(library_id) if isinstance(libraries, dict) else None
    return raw if isinstance(raw, dict) else {}


def lookup_calibrated_table(library_id: str, station_id: str) -> str | None:
    lib_id = (library_id or "").strip().lower()
    sid = (station_id or "").strip()
    if lib_id not in CALIBRATED_LIBRARY_IDS or not sid:
        return None
    mapping = _station_index().get(lib_id) or {}
    for key in (
        sid,
        normalize_site_key(sid),
        canonical_project(sid, lib_id) or "",
        site_code(sid),
    ):
        if key and key in mapping:
            return mapping[key]
    return None


def calibrated_supported(library_id: str | None, station_id: str | None) -> bool:
    return lookup_calibrated_table(library_id or "", station_id or "") is not None


def calibrated_metric_title(library_id: str) -> str | None:
    title = str(_library_cfg(library_id).get("metric_title") or "").strip()
    return title or None


def attach_data_source_fields(
    payload: dict[str, Any],
    *,
    library_id: str,
    station_id: str | None,
    data_source: str,
) -> dict[str, Any]:
    payload["data_source"] = data_source
    payload["calibrated_supported"] = calibrated_supported(library_id, station_id)
    if data_source == DATA_SOURCE_CALIBRATED:
        payload["metric_title"] = calibrated_metric_title(library_id)
    else:
        payload.setdefault("metric_title", None)
    return payload


def _quote_ident(name: str, *, kind: str = "identifier") -> str:
    raw = (name or "").strip()
    if kind == "表名":
        if not _SHEET_TABLE_RE.match(raw):
            raise _browse_error(400, "invalid_table", f"人工清洗表不在白名单: {name}")
    elif not _COL_RE.match(raw):
        raise _browse_error(400, "invalid_identifier", f"非法{kind}: {name}")
    return '"' + raw.replace('"', "") + '"'


def display_device_name(physical: str, suffixes: tuple[str, ...]) -> str:
    s = (physical or "").strip()
    low = s.lower()
    for suf in suffixes:
        token = (suf or "").strip()
        if token and low.endswith(token.lower()):
            s = s[: len(s) - len(token)].rstrip("_-")
            break
    m = _DEVICE_PREFIX_RE.match(s)
    if m:
        return m.group(1).upper() + m.group(2)
    return s


def _time_expr(table_alias: str, quoted_time_col: str) -> str:
    ref = f"{table_alias}.{quoted_time_col}"
    text = f"({ref})::text"
    return (
        f"(CASE WHEN {text} ~ '^[0-9]{{4}}/' "
        f"THEN to_timestamp({text}, 'YYYY/MM/DD HH24:MI:SS') "
        f"ELSE CAST({ref} AS timestamp) END)"
    )


def _empty_matrix(
    *,
    library: LibraryDef,
    station_id: str,
    page_n: int,
    size: int,
    warnings: list[str],
    supported: bool,
) -> dict[str, Any]:
    from app.data_query_agent.browse import RESULT_GRAIN_DEVICE_TIME

    return attach_data_source_fields(
        {
            "ok": True,
            "library_id": library.id,
            "display_name": library.display_name,
            "district": None,
            "station_id": station_id,
            "result_grain": RESULT_GRAIN_DEVICE_TIME,
            "page": page_n,
            "page_size": size,
            "total": 0,
            "has_more": False,
            "columns": [{"key": "data_time", "title": "监测时间"}],
            "list": [],
            "hud_enabled": False,
            "warnings": warnings,
        },
        library_id=library.id,
        station_id=station_id,
        data_source=DATA_SOURCE_CALIBRATED,
    ) | {"calibrated_supported": supported}


def _skip_columns(cfg: dict[str, Any], lib_cfg: dict[str, Any]) -> set[str]:
    skip = {str(x).strip().lower() for x in (cfg.get("skip_columns") or []) if str(x).strip()}
    skip.add(str(cfg.get("time_column") or "caijishijian").strip().lower())
    skip.add("id")
    extra = lib_cfg.get("skip_columns")
    if isinstance(extra, list):
        skip.update(str(x).strip().lower() for x in extra if str(x).strip())
    return skip


def _suffixes(lib_cfg: dict[str, Any]) -> tuple[str, ...]:
    raw = lib_cfg.get("column_suffix_strip") or []
    if not isinstance(raw, list):
        return ()
    return tuple(str(x).strip() for x in raw if str(x).strip())


def build_column_catalog_sql(schema: str, table: str) -> tuple[str, dict[str, Any]]:
    return (
        "SELECT column_name FROM information_schema.columns "
        "WHERE table_schema = :schema AND table_name = :table "
        "ORDER BY ordinal_position",
        {"schema": schema, "table": table},
    )


def build_calibrated_count_sql(table: str, time_col: str) -> tuple[str, dict[str, Any]]:
    qt = _quote_ident(table, kind="表名")
    qc = _quote_ident(time_col, kind="列名")
    expr = _time_expr("t", qc)
    sql = (
        f"SELECT COUNT(*) AS total, MAX({expr}) AS latest "
        f"FROM {qt} t WHERE t.{qc} IS NOT NULL"
    )
    return sql, {}


def build_calibrated_page_sql(
    table: str,
    time_col: str,
    devices: list[tuple[str, str]],
    *,
    limit: int,
    offset: int,
) -> tuple[str, dict[str, Any]]:
    qt = _quote_ident(table, kind="表名")
    qc = _quote_ident(time_col, kind="列名")
    expr = _time_expr("t", qc)
    selects = [f"{expr} AS data_time"]
    for physical, display in devices:
        selects.append(
            f"t.{_quote_ident(physical, kind='列名')} AS {_quote_ident(display, kind='列名')}"
        )
    sql = (
        f"SELECT {', '.join(selects)} FROM {qt} t "
        f"WHERE t.{qc} IS NOT NULL "
        f"ORDER BY data_time DESC NULLS LAST "
        f"LIMIT :limit OFFSET :offset"
    )
    return sql, {"limit": int(limit), "offset": int(offset)}


def _devices_from_catalog(
    rows: list[dict[str, Any]],
    *,
    skip: set[str],
    suffixes: tuple[str, ...],
) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    seen: set[str] = set()
    for raw in rows:
        physical = str(raw.get("column_name") or "").strip()
        if not physical or physical.lower() in skip:
            continue
        if not _COL_RE.match(physical):
            logger.warning("skip calibrated column with illegal name %s", physical)
            continue
        display = display_device_name(physical, suffixes)
        if not display or display in seen:
            continue
        seen.add(display)
        out.append((physical, display))
    return out


def _calibrated_warnings(library_id: str, latest: Any) -> list[str]:
    notes = ["人工清洗仅每日 0:00 / 12:00"]
    if latest is not None:
        notes.append(f"最新至 {_cell(latest)}")
    if library_id == "dxswj":
        notes.append("单元格为水位标高(m)，不是埋深")
    return notes


async def fetch_calibrated_device_matrix(
    *,
    library: LibraryDef,
    station_id: str,
    page_n: int,
    size: int,
    offset: int,
    executor: SQLExecutor,
) -> dict[str, Any]:
    from app.data_query_agent.browse import RESULT_GRAIN_DEVICE_TIME

    lib_id = (library.id or "").strip().lower()
    if lib_id not in CALIBRATED_LIBRARY_IDS:
        raise _browse_error(
            422,
            "calibrated_library_unsupported",
            "人工清洗仅支持分层标、地下水、孔隙水的点站点浏览",
        )
    table = lookup_calibrated_table(lib_id, station_id)
    if not table:
        return _empty_matrix(
            library=library,
            station_id=station_id,
            page_n=page_n,
            size=size,
            warnings=["该站点暂无人工清洗数据"],
            supported=False,
        )

    cfg = load_calibrated_table_config()
    lib_cfg = _library_cfg(lib_id)
    schema = str(cfg.get("schema") or "public").strip() or "public"
    time_col = str(cfg.get("time_column") or "caijishijian").strip() or "caijishijian"
    skip = _skip_columns(cfg, lib_cfg)
    suffixes = _suffixes(lib_cfg)

    catalog_sql, catalog_params = build_column_catalog_sql(schema, table)
    logger.info(
        "data_query_agent browse calibrated library=%s station_id=%s table=%s page=%s size=%s",
        lib_id,
        station_id,
        table,
        page_n,
        size,
    )
    try:
        col_rows = await executor.execute(catalog_sql, catalog_params)
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "calibrated column catalog failed library=%s station=%s table=%s",
            lib_id,
            station_id,
            table,
            exc_info=True,
        )
        raise _browse_error(502, "browse_query_failed", str(exc) or "浏览列表查询失败") from exc

    if not col_rows:
        return _empty_matrix(
            library=library,
            station_id=station_id,
            page_n=page_n,
            size=size,
            warnings=["该站点人工清洗表不存在或无法读取"],
            supported=True,
        )

    devices = _devices_from_catalog(col_rows, skip=skip, suffixes=suffixes)
    if not devices:
        return _empty_matrix(
            library=library,
            station_id=station_id,
            page_n=page_n,
            size=size,
            warnings=["该站点人工清洗表无设备列"],
            supported=True,
        )

    count_sql, count_params = build_calibrated_count_sql(table, time_col)
    page_sql, page_params = build_calibrated_page_sql(
        table, time_col, devices, limit=size, offset=offset
    )
    try:
        count_rows = await executor.execute(count_sql, count_params)
        data_rows = await executor.execute(page_sql, page_params)
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "calibrated matrix query failed library=%s station=%s table=%s",
            lib_id,
            station_id,
            table,
            exc_info=True,
        )
        raise _browse_error(502, "browse_query_failed", str(exc) or "浏览列表查询失败") from exc

    total = 0
    latest = None
    if count_rows:
        total = int(count_rows[0].get("total") or 0)
        latest = count_rows[0].get("latest")
    items: list[dict[str, Any]] = []
    for raw in data_rows or []:
        row: dict[str, Any] = {
            "data_time": _cell(raw.get("data_time")),
            "hud_available": False,
        }
        for _physical, display in devices:
            row[display] = _cell(raw.get(display))
        items.append(row)

    columns: list[dict[str, Any]] = [{"key": "data_time", "title": "监测时间"}]
    columns.extend({"key": display, "title": display} for _physical, display in devices)
    return attach_data_source_fields(
        {
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
            "warnings": _calibrated_warnings(lib_id, latest),
        },
        library_id=library.id,
        station_id=station_id,
        data_source=DATA_SOURCE_CALIBRATED,
    )
