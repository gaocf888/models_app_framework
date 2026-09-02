"""grain / hud_enabled / 列表与按实体 HUD 组装。"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Any

from app.core.config import get_app_config
from app.data_query_agent.acquire import AcquireResult
from app.data_query_agent.annual import compute_annual_delta
from app.data_query_agent.catalog import LibraryDef
from app.data_query_agent.scope_intent import ScopeIntentResult

CITY_ENTITY_ID = "beijing"  # 全市 HUD 主键固定，不跟某站/某区走。


def _cell(value: Any) -> Any:
    if isinstance(value, Decimal):
        try:
            return float(value)
        except Exception:  # noqa: BLE001
            return str(value)
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    return value


def _norm_row(row: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for k, v in row.items():
        key = str(k).strip()
        if not key:
            continue
        out[key] = _cell(v)
        out[key.lower()] = _cell(v)
    return out


def _pick(row: dict[str, Any], aliases: tuple[str, ...] | list[str]) -> Any:
    for a in aliases:
        if a in row and row[a] is not None:
            return row[a]
        low = a.lower()
        if low in row and row[low] is not None:
            return row[low]
    return None


def _downsample(points: list[dict[str, Any]], max_n: int) -> list[dict[str, Any]]:
    """等间隔降采样，保留首尾，控制 HUD 点数。"""
    if max_n <= 0 or len(points) <= max_n:
        return points
    if max_n == 1:
        return [points[-1]]
    step = (len(points) - 1) / (max_n - 1)
    idxs = sorted({min(len(points) - 1, int(round(i * step))) for i in range(max_n)})
    return [points[i] for i in idxs]


def _placeholder_blocks() -> dict[str, Any]:
    """岩性/层位/防灾策略无物理列：显式 unavailable，前端不渲染空块。"""
    return {
        "geology": {
            "status": "unavailable",
            "reason": "no_column",
            "layer": None,
            "lithology": None,
        },
        "strategy": {
            "status": "unavailable",
            "reason": "no_source",
            "items": [],
        },
    }


def _core_metrics(library: LibraryDef, item: dict[str, Any]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for m in library.core_metrics:
        out.append(
            {
                "id": m.id,
                "name": m.name,
                "value": item.get(m.id),
                "unit": m.unit,
            }
        )
    return out


def _hud_grain_allowed(grain: str, *, city_enabled: bool) -> bool:
    if grain in ("station", "station_series", "district"):
        return True
    if grain == "city":
        return city_enabled
    return False


def _hud_title(library: LibraryDef, entity: str) -> str:
    tmpl = library.hud_title_template or "{entity} · {display_name}"
    try:
        return tmpl.format(entity=entity, display_name=library.display_name, library_id=library.id)
    except Exception:  # noqa: BLE001
        return f"{entity} · {library.display_name}"


def _series_metrics(library: LibraryDef) -> list:
    mets = list(library.series_metrics or ())
    if mets:
        return mets
    if library.series_column:
        from app.data_query_agent.catalog import LibrarySeriesMetric

        return [
            LibrarySeriesMetric(
                id=library.series_column,
                name=library.series_column,
                unit=library.series_unit,
            )
        ]
    return []


def _index_series(
    *,
    series_item,
    library: LibraryDef,
    grain: str,
) -> dict[str, dict[str, list[dict[str, Any]]]]:
    """按实体再按度量列索引。区行只认 area，忽略仅有 station_id 的点。"""
    series_full: dict[str, dict[str, list[dict[str, Any]]]] = {}
    if not (series_item and series_item.ok):
        return series_full
    metrics = _series_metrics(library)
    metric_ids = [m.id for m in metrics] or [library.series_column]
    for raw in series_item.rows:
        nrow = _norm_row(raw)
        if grain == "district":
            key = _pick(nrow, ("area", "district"))
        elif grain == "city":
            key = CITY_ENTITY_ID
        else:
            key = _pick(nrow, ("station_id",))
        if key is None:
            continue
        t = _pick(nrow, ("data_time", "t", "bucket"))
        bucket = series_full.setdefault(str(key), {})
        for mid in metric_ids:
            aliases = (mid, library.series_column, library.annual_source or "", "v", "avg_value")
            if mid != library.series_column:
                aliases = (mid,)
            v = _pick(nrow, aliases)
            bucket.setdefault(mid, []).append({"t": t, "v": _cell(v)})
    for _eid, by_m in series_full.items():
        for mid, pts in by_m.items():
            pts.sort(key=lambda p: str(p.get("t") or ""))
    return series_full


def _points_for(series_full: dict, entity_id: str, metric: str) -> list[dict[str, Any]]:
    return list((series_full.get(entity_id) or {}).get(metric) or [])


def _series_payload(
    *,
    library: LibraryDef,
    entity_id: str,
    series_full: dict,
    grain: str,
    max_pts: int,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    agg = "avg" if grain in ("district", "city") else None
    bucket = "day" if agg else None
    primary = {
        "metric": library.series_column,
        "unit": library.series_unit,
        "agg": agg,
        "time_bucket": bucket,
        "points": _downsample(_points_for(series_full, entity_id, library.series_column), max_pts),
    }
    series_list: list[dict[str, Any]] = []
    for m in _series_metrics(library):
        series_list.append(
            {
                "id": m.id,
                "name": m.name,
                "metric": m.id,
                "unit": m.unit,
                "agg": agg,
                "time_bucket": bucket,
                "points": _downsample(_points_for(series_full, entity_id, m.id), max_pts),
            }
        )
    return primary, series_list


def assemble_result(
    *,
    library: LibraryDef,
    scope: ScopeIntentResult,
    acquire: AcquireResult,
    include_hud: bool,
    expose_sql: bool,
    extra_warnings: list[str] | None = None,
) -> dict[str, Any]:
    """组装 list + hud_enabled；HUD 跟行实体走，权威字典 hud_by_entity。"""
    cfg = get_app_config().data_query_agent
    warnings = list(extra_warnings or [])
    if scope.warnings:
        warnings.extend(scope.warnings)
    if scope.lithology_warning:
        warnings.append("lithology_unsupported")
        warnings.append("layer_unsupported")

    list_rows_raw = acquire.list_item.rows if acquire.list_item.ok else []
    norm_rows = [_norm_row(r) for r in list_rows_raw]

    planned_grain = scope.grain
    has_station = any(
        _pick(r, ("station_id", "station_name")) is not None for r in norm_rows
    )
    only_area = bool(norm_rows) and all(
        _pick(r, ("area", "district")) is not None
        and _pick(r, ("station_id", "station_name")) is None
        for r in norm_rows
    )
    grain = planned_grain
    # 计划站点但行只有区：校正为 district，避免误开站点 HUD。
    if planned_grain == "station" and only_area:
        grain = "district"
    elif planned_grain in ("district", "city"):
        grain = planned_grain
    elif not has_station and planned_grain == "station" and norm_rows:
        grain = "unknown"

    city_enabled = bool(getattr(cfg, "hud_city_enabled", True))
    has_area = any(_pick(r, ("area", "district")) is not None for r in norm_rows)
    has_entity = (
        (grain in ("station", "station_series") and has_station)
        or (grain == "district" and has_area)
        or (grain == "city" and bool(norm_rows))
    )
    # HUD 跟列表行实体走，不跟监测库走；unknown / 无 entity_id 不开按钮。
    hud_enabled = (
        include_hud
        and library.hud_supported
        and _hud_grain_allowed(grain, city_enabled=city_enabled)
        and has_entity
    )

    columns = [{"key": c.key, "title": c.title} for c in library.columns]
    if grain == "district":
        columns = [
            {"key": "area", "title": "行政区"},
            {"key": library.core_metrics[0].id if library.core_metrics else library.core_metric, "title": "指标"},
            {"key": "station_count", "title": "站点数"},
        ]
    elif grain == "city":
        columns = [
            {"key": "area", "title": "范围"},
            {"key": library.core_metrics[0].id if library.core_metrics else library.core_metric, "title": "指标"},
            {"key": "station_count", "title": "站点数"},
        ]

    list_out: list[dict[str, Any]] = []
    station_ids: list[str] = []
    area_ids: list[str] = []
    for row in norm_rows:
        item: dict[str, Any] = {}
        if grain in ("district", "city"):
            item["area"] = _pick(row, ("area", "district"))
            if grain == "city" and item["area"] is None:
                item["area"] = "全市"
            core_key = library.core_metrics[0].id if library.core_metrics else library.core_metric
            item[core_key] = _pick(row, (core_key, library.core_metric))
            item["station_count"] = _pick(row, ("station_count",))
        else:
            for col in library.columns:
                item[col.key] = _pick(row, col.aliases or (col.key,))
        sid = item.get("station_id") or item.get("station_name")
        if sid is not None:
            sid_s = str(sid)
            item["station_id"] = item.get("station_id") or sid_s
            station_ids.append(str(item["station_id"]))
        if grain == "district" and item.get("area") is not None:
            area_ids.append(str(item["area"]))
        list_out.append(item)

    series_full = _index_series(series_item=acquire.series_item, library=library, grain=grain)

    win = scope.annual_window or {}
    # 列表缺年变化时，用同实体 HUD 序列在窗内算终值−初值。
    if library.annual_key and grain in ("station", "station_series", "district"):
        missing_annual = False
        for item in list_out:
            if grain == "district":
                key = str(item.get("area") or "")
            else:
                key = str(item.get("station_id") or "")
            delta = compute_annual_delta(
                _points_for(series_full, key, library.annual_source or library.series_column),
                start=win.get("start") or "",
                end=win.get("end") or "",
            )
            if delta is not None:
                item[library.annual_key] = delta
            elif item.get(library.annual_key) is None:
                missing_annual = True
        if missing_annual and series_full:
            warnings.append("annual_metric_uncomputed")

    hud_by_entity: dict[str, Any] = {}
    hud_by_station: dict[str, Any] = {}
    if hud_enabled:
        max_pts = int(cfg.hud_max_points_per_station)
        # 站点：原始序列；hud_by_station 与 hud_by_entity 同一对象，供旧前端。
        if grain in ("station", "station_series"):
            max_st = int(cfg.hud_max_stations)
            keep = station_ids[:max_st]
            keep_set = set(keep)
            if len(station_ids) > max_st:
                warnings.append("hud_series_truncated")
            for item in list_out:
                sid_s = str(item.get("station_id") or "")
                if not sid_s:
                    item["hud_available"] = False
                    continue
                available = sid_s in keep_set
                item["hud_entity_type"] = "station"
                item["hud_entity_id"] = sid_s
                item["hud_available"] = available
                if not available:
                    continue
                primary, series_list = _series_payload(
                    library=library,
                    entity_id=sid_s,
                    series_full=series_full,
                    grain=grain,
                    max_pts=max_pts,
                )
                panel = {
                    "entity_type": "station",
                    "entity_id": sid_s,
                    "title": _hud_title(library, str(item.get("station_name") or sid_s)),
                    "library_id": library.id,
                    "station_id": sid_s,
                    "station_name": item.get("station_name"),
                    "area": item.get("area"),
                    "identity": {
                        "station_id": sid_s,
                        "station_name": item.get("station_name"),
                        "area": item.get("area"),
                        "grain": grain,
                    },
                    "core_metrics": _core_metrics(library, item),
                    "series": primary,
                    "series_list": series_list,
                    "blocks": _placeholder_blocks(),
                }
                hud_by_station[sid_s] = panel
                hud_by_entity[sid_s] = panel
        elif grain == "district":
            # 区 HUD：时序已在 SQL 内按 area 日 AVG；超限行仅关按钮，不拿前 N 站再平均。
            max_d = int(getattr(cfg, "hud_max_districts", 16) or 16)
            keep = area_ids[:max_d]
            keep_set = set(keep)
            if len(area_ids) > max_d:
                warnings.append("hud_series_truncated")
            for item in list_out:
                area = str(item.get("area") or "")
                if not area:
                    item["hud_available"] = False
                    continue
                available = area in keep_set
                item["hud_entity_type"] = "district"
                item["hud_entity_id"] = area
                item["hud_available"] = available
                if not available:
                    continue
                primary, series_list = _series_payload(
                    library=library,
                    entity_id=area,
                    series_full=series_full,
                    grain="district",
                    max_pts=max_pts,
                )
                hud_by_entity[area] = {
                    "entity_type": "district",
                    "entity_id": area,
                    "title": _hud_title(library, area),
                    "library_id": library.id,
                    "identity": {
                        "area": area,
                        "station_count": item.get("station_count"),
                        "grain": "district",
                    },
                    "core_metrics": _core_metrics(library, item),
                    "series": primary,
                    "series_list": series_list,
                    "blocks": _placeholder_blocks(),
                    "warnings": [],
                }
        elif grain == "city":
            # 全市一行实体 beijing；折线为该库全市日平均。
            for item in list_out:
                item["hud_entity_type"] = "city"
                item["hud_entity_id"] = CITY_ENTITY_ID
                item["hud_available"] = True
            primary, series_list = _series_payload(
                library=library,
                entity_id=CITY_ENTITY_ID,
                series_full=series_full,
                grain="city",
                max_pts=max_pts,
            )
            hud_by_entity[CITY_ENTITY_ID] = {
                "entity_type": "city",
                "entity_id": CITY_ENTITY_ID,
                "title": _hud_title(library, "全市"),
                "library_id": library.id,
                "identity": {
                    "area": None,
                    "station_count": list_out[0].get("station_count") if list_out else None,
                    "grain": "city",
                },
                "core_metrics": _core_metrics(library, list_out[0]) if list_out else [],
                "series": primary,
                "series_list": series_list,
                "blocks": _placeholder_blocks(),
                "warnings": [],
            }

        if acquire.series_item is not None and not acquire.series_item.ok:
            warnings.append("hud_series_failed")  # 列表仍成功，对应实体 points=[]

    payload: dict[str, Any] = {
        "library_id": library.id,
        "result_grain": grain,
        "hud_enabled": hud_enabled,
        "columns": columns,
        "list": list_out,
        "warnings": warnings,
    }
    if hud_enabled:
        payload["hud_by_entity"] = hud_by_entity
        payload["hud_by_station"] = hud_by_station  # 区/市为空 dict，前端优先读 entity
    if expose_sql:
        payload["sql"] = {
            "q_list": acquire.list_item.sql,
            "q_hud_series": acquire.series_item.sql if acquire.series_item else None,
        }
    return payload


def assemble_entity_hud(
    *,
    library: LibraryDef,
    grain: str,
    entity_id: str,
    acquire: AcquireResult,
    expose_sql: bool = False,
    extra_warnings: list[str] | None = None,
    annual_window: dict[str, str] | None = None,
) -> dict[str, Any]:
    """单实体 HUD，结构与 run-stream 的 hud_by_entity[id] 一致；不套用列表行数上限。"""
    cfg = get_app_config().data_query_agent
    warnings = list(extra_warnings or [])
    entity_type = "station" if grain in ("station", "station_series") else grain
    series_full = _index_series(series_item=acquire.series_item, library=library, grain=grain)
    max_pts = int(cfg.hud_max_points_per_station)

    item: dict[str, Any] = {}
    found_list = False
    if acquire.list_item.ok:
        for raw in acquire.list_item.rows:
            nrow = _norm_row(raw)
            if entity_type == "station":
                sid = _pick(nrow, ("station_id", "station_name"))
                if sid is None or str(sid) != entity_id:
                    continue
                for col in library.columns:
                    item[col.key] = _pick(nrow, col.aliases or (col.key,))
                item["station_id"] = str(_pick(nrow, ("station_id",)) or entity_id)
                item["station_name"] = _pick(nrow, ("station_name",))
                item["area"] = _pick(nrow, ("area", "district"))
                found_list = True
                break
            if entity_type == "district":
                area = _pick(nrow, ("area", "district"))
                if area is None or str(area) != entity_id:
                    continue
                core_key = library.core_metrics[0].id if library.core_metrics else library.core_metric
                item = {
                    "area": str(area),
                    "station_count": _pick(nrow, ("station_count",)),
                    core_key: _pick(nrow, (core_key, library.core_metric)),
                }
                found_list = True
                break
            if entity_type == "city":
                core_key = library.core_metrics[0].id if library.core_metrics else library.core_metric
                item = {
                    "area": "全市",
                    "station_count": _pick(nrow, ("station_count",)),
                    core_key: _pick(nrow, (core_key, library.core_metric)),
                }
                found_list = True
                break

    found_series = entity_id in series_full
    win = annual_window or {}
    if library.annual_key and item and grain in ("station", "station_series", "district"):
        delta = compute_annual_delta(
            _points_for(series_full, entity_id, library.annual_source or library.series_column),
            start=win.get("start") or "",
            end=win.get("end") or "",
        )
        if delta is not None:
            item[library.annual_key] = delta

    primary, series_list = _series_payload(
        library=library,
        entity_id=entity_id,
        series_full=series_full,
        grain=grain,
        max_pts=max_pts,
    )
    if acquire.series_item is not None and not acquire.series_item.ok:
        warnings.append("hud_series_failed")

    if entity_type == "station":
        title_entity = str(item.get("station_name") or entity_id)
        panel: dict[str, Any] = {
            "entity_type": "station",
            "entity_id": entity_id,
            "title": _hud_title(library, title_entity),
            "library_id": library.id,
            "station_id": entity_id,
            "station_name": item.get("station_name"),
            "area": item.get("area"),
            "identity": {
                "station_id": entity_id,
                "station_name": item.get("station_name"),
                "area": item.get("area"),
                "grain": grain,
            },
            "core_metrics": _core_metrics(library, item),
            "series": primary,
            "series_list": series_list,
            "blocks": _placeholder_blocks(),
        }
    elif entity_type == "district":
        panel = {
            "entity_type": "district",
            "entity_id": entity_id,
            "title": _hud_title(library, entity_id),
            "library_id": library.id,
            "identity": {
                "area": entity_id,
                "station_count": item.get("station_count"),
                "grain": "district",
            },
            "core_metrics": _core_metrics(library, item),
            "series": primary,
            "series_list": series_list,
            "blocks": _placeholder_blocks(),
            "warnings": [],
        }
    else:
        panel = {
            "entity_type": "city",
            "entity_id": CITY_ENTITY_ID,
            "title": _hud_title(library, "全市"),
            "library_id": library.id,
            "identity": {
                "area": None,
                "station_count": item.get("station_count"),
                "grain": "city",
            },
            "core_metrics": _core_metrics(library, item),
            "series": primary,
            "series_list": series_list,
            "blocks": _placeholder_blocks(),
            "warnings": [],
        }

    payload: dict[str, Any] = {
        "ok": True,
        "library_id": library.id,
        "entity_type": entity_type,
        "entity_id": panel["entity_id"],
        "hud": panel,
        "warnings": warnings,
        "found": found_list or found_series,
    }
    if expose_sql:
        payload["sql"] = {
            "q_list": acquire.list_item.sql if acquire.list_item else None,
            "q_hud_series": acquire.series_item.sql if acquire.series_item else None,
        }
    return payload
