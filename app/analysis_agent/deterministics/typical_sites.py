"""典型曲线公共集：定场地=fcb∩dxswj；分轴名单=分层标∩对比方式。"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

from app.analysis_agent.deterministics.coverage import (
    load_device_projects_by_type,
    normalize_site_key,
)
from app.core.logging import get_logger

logger = get_logger(__name__)

_YAML = (
    Path(__file__).resolve().parents[3]
    / "configs"
    / "nl2sql_business"
    / "subsidence"
    / "semantic"
    / "dimensions"
    / "typical_curve_sites.yaml"
)

_AXIS_KEYS = ("elevation", "pressure", "rain")


def compute_typical_curve_sites() -> dict[str, tuple[str, ...]]:
    """从 device_station_map 现算：pick=fcb∩dxswj，分轴=fcb∩对应方式。"""
    by_type = load_device_projects_by_type()
    fcb = list(by_type.get("fcb") or ())
    dx = set(by_type.get("dxswj") or ())
    kx = set(by_type.get("kxsylj") or ())
    qx = set(by_type.get("qxz") or ())
    pick = tuple(n for n in fcb if n in dx)
    return {
        "pick_sites": pick,
        "elevation": pick,
        "pressure": tuple(n for n in fcb if n in kx),
        "rain": tuple(n for n in fcb if n in qx),
    }


def _parse_names(raw: Any) -> tuple[str, ...]:
    if not isinstance(raw, list):
        return ()
    return tuple(str(x).strip() for x in raw if str(x).strip())


@lru_cache(maxsize=1)
def load_typical_curve_sites() -> dict[str, tuple[str, ...]]:
    raw = yaml.safe_load(_YAML.read_text(encoding="utf-8")) if _YAML.is_file() else {}
    data = raw if isinstance(raw, dict) else {}
    axis = data.get("axis_sites") if isinstance(data.get("axis_sites"), dict) else {}
    loaded = {
        "pick_sites": _parse_names(data.get("pick_sites")),
        "elevation": _parse_names(axis.get("elevation")),
        "pressure": _parse_names(axis.get("pressure")),
        "rain": _parse_names(axis.get("rain")),
    }
    computed = compute_typical_curve_sites()
    for key in ("pick_sites", *_AXIS_KEYS):
        if set(loaded[key]) != set(computed[key]):
            logger.warning(
                "typical_curve_sites.yaml %s mismatch map intersection, use computed",
                key,
            )
            return computed
    if not loaded["pick_sites"]:
        return computed
    return loaded


def typical_pick_sites() -> tuple[str, ...]:
    return load_typical_curve_sites()["pick_sites"]


def typical_axis_sites(kind: str) -> tuple[str, ...]:
    key = (kind or "").strip().lower()
    if key not in _AXIS_KEYS:
        return ()
    return load_typical_curve_sites()[key]


def in_typical_pick_sites(name: str) -> bool:
    raw = (name or "").strip()
    if not raw:
        return False
    sites = typical_pick_sites()
    if raw in sites:
        return True
    key = normalize_site_key(raw)
    return any(normalize_site_key(s) == key for s in sites)


def in_typical_axis_sites(name: str, kind: str) -> bool:
    raw = (name or "").strip()
    if not raw:
        return False
    sites = typical_axis_sites(kind)
    if raw in sites:
        return True
    key = normalize_site_key(raw)
    return any(normalize_site_key(s) == key for s in sites)
