"""监测方式覆盖 ∩ 分层标层位字典（报告侧确定性 SQL / python 共用）。

场地键权威：`device_station_map` 的 project_name（应对齐 t_station.name）。
层位字典 / 重点区 YAML 若含全角括号，规范化后再 JOIN，差集打日志，禁止 silently 丢站。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

from app.core.logging import get_logger
from app.nl2sql.semantic_layer import parse_device_station_map, parse_fcb_layer_map

logger = get_logger(__name__)

_REPO_ROOT = Path(__file__).resolve().parents[3]
_DIMS = _REPO_ROOT / "configs" / "nl2sql_business" / "subsidence" / "semantic" / "dimensions"


_SITE_CODE_RE = re.compile(r"^([A-Za-z]+\d+)")


def normalize_site_key(name: str) -> str:
    """全角括号/逗号对齐半角，便于三份 YAML 互相对齐。"""
    s = (name or "").strip()
    return (
        s.replace("（", "(")
        .replace("）", ")")
        .replace("，", ",")
        .replace(" ", "")
    )


def _site_code(name: str) -> str:
    """F42(牌楼站) / F42(牌楼） → F42；无码返回空。"""
    m = _SITE_CODE_RE.match(normalize_site_key(name))
    return m.group(1).upper() if m else ""


def _load_yaml(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    return data if isinstance(data, dict) else {}


@lru_cache(maxsize=1)
def load_device_projects_by_type() -> dict[str, tuple[str, ...]]:
    """device_type → 官方 project_name 覆盖名单（P7）。"""
    raw = _load_yaml(_DIMS / "device_station_map.yaml")
    return parse_device_station_map(raw)


def coverage_index(device_type: str) -> dict[str, str]:
    """normalize(name) → 覆盖名单中的权威 project_name。"""
    names = load_device_projects_by_type().get((device_type or "").strip().lower(), ())
    out: dict[str, str] = {}
    for name in names:
        key = normalize_site_key(name)
        if key and key not in out:
            out[key] = name
    return out


def coverage_code_index(device_type: str) -> dict[str, str]:
    """监测站码 → 覆盖名单权威名。同一码多条则不收录，避免误绑。"""
    names = load_device_projects_by_type().get((device_type or "").strip().lower(), ())
    buckets: dict[str, list[str]] = {}
    for name in names:
        code = _site_code(name)
        if not code:
            continue
        buckets.setdefault(code, []).append(name)
    return {code: items[0] for code, items in buckets.items() if len(items) == 1}


def canonical_project(name: str, device_type: str) -> str | None:
    """对齐到 device_station_map 权威场地名。先全角规范化，再按 F/J 码唯一回退（F42 牌楼 vs 牌楼站）。"""
    raw = (name or "").strip()
    if not raw:
        return None
    hit = coverage_index(device_type).get(normalize_site_key(raw))
    if hit:
        return hit
    code = _site_code(raw)
    if not code:
        return None
    return coverage_code_index(device_type).get(code)


def in_coverage(name: str, device_type: str) -> bool:
    return canonical_project(name, device_type) is not None


@dataclass(frozen=True)
class LayeredMark:
    project_name: str  # 覆盖名单权威名
    station_name: str
    monitor_layer: int
    table: str  # fcb | jyb


@lru_cache(maxsize=1)
def load_layered_marks() -> tuple[LayeredMark, ...]:
    """monitor_layer 非空的边界标，且场地落入对应监测方式覆盖。"""
    raw = _load_yaml(_DIMS / "fcb_layer_map.yaml")
    (
        _layer0_by_project,
        _project_by_mark,
        _marks,
        _layer0_marks,
        _by_district,
        layers_by_project,
    ) = parse_fcb_layer_map(raw)

    unmatched: list[str] = []
    out: list[LayeredMark] = []
    seen: set[tuple[str, str]] = set()

    for project, layer_map in layers_by_project.items():
        if not isinstance(layer_map, dict):
            continue
        for layer, mark in layer_map.items():
            try:
                layer_i = int(layer)
            except (TypeError, ValueError):
                continue
            mark_s = str(mark or "").strip()
            if not mark_s:
                continue
            is_jyb_mark = mark_s[:1].upper() == "J"
            if is_jyb_mark:
                canon = canonical_project(project, "jyb")
                table = "jyb"
            else:
                canon = canonical_project(project, "fcb")
                table = "fcb"
            if not canon:
                unmatched.append(f"{project}/{mark_s}")
                continue
            key = (canon, mark_s)
            if key in seen:
                continue
            seen.add(key)
            out.append(
                LayeredMark(
                    project_name=canon,
                    station_name=mark_s,
                    monitor_layer=layer_i,
                    table=table,
                )
            )

    if unmatched:
        logger.warning(
            "fcb_layer_map coverage mismatch count=%s sample=%s",
            len(unmatched),
            unmatched[:8],
        )
    return tuple(out)


def endpoint_bind_lists() -> dict[str, list[str]]:
    """SQL 绑定：标号列表 + 场地 allowlist。空列表对应 UNION 分支不选行。"""
    fcb_marks: list[str] = []
    jyb_marks: list[str] = []
    fcb_projects: list[str] = []
    jyb_projects: list[str] = []
    for mark in load_layered_marks():
        if mark.table == "jyb":
            jyb_marks.append(mark.station_name)
            if mark.project_name not in jyb_projects:
                jyb_projects.append(mark.project_name)
        else:
            fcb_marks.append(mark.station_name)
            if mark.project_name not in fcb_projects:
                fcb_projects.append(mark.project_name)
    return {
        "fcb_marks": fcb_marks,
        "jyb_marks": jyb_marks,
        "fcb_projects": fcb_projects,
        "jyb_projects": jyb_projects,
    }


def layer0_mark_keys() -> frozenset[tuple[str, str]]:
    """官方 fcb 覆盖 ∩ 层位 0 代表标 (project_name, station_name)。"""
    return frozenset(
        (m.project_name, m.station_name)
        for m in load_layered_marks()
        if m.table == "fcb" and m.monitor_layer == 0
    )


def layers_by_canonical_project() -> dict[str, dict[int, str]]:
    out: dict[str, dict[int, str]] = {}
    for mark in load_layered_marks():
        out.setdefault(mark.project_name, {})[mark.monitor_layer] = mark.station_name
    return out


def project_names(device_type: str) -> tuple[str, ...]:
    return load_device_projects_by_type().get((device_type or "").strip().lower(), ())
