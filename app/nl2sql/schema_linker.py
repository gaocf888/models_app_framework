"""显式 Schema 链接：在 LLM 生成前收窄表/列 catalog。"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from typing import Any

from app.core.logging import get_logger
from app.nl2sql.nl2sql_business_profile import get_nl2sql_business_profile
from app.nl2sql.semantic_layer import (
    SemanticBinding,
    SemanticAssets,
    is_station_catalog_question,
    load_semantic_assets,
)
from app.nl2sql.question_scope_models import QuestionIntent

logger = get_logger(__name__)


@dataclass
class LinkedColumn:
    table: str
    column: str
    role: str
    reason: str


@dataclass
class LinkedTable:
    name: str
    reason: str
    score: float


@dataclass
class LinkedJoin:
    left: str
    right: str
    reason: str


@dataclass
class LinkedSchema:
    tables: list[LinkedTable] = field(default_factory=list)
    columns: list[LinkedColumn] = field(default_factory=list)
    joins: list[LinkedJoin] = field(default_factory=list)
    union_tables: list[str] = field(default_factory=list)
    suggested_filters: list[dict[str, Any]] = field(default_factory=list)
    confidence: float = 0.0
    status: str = "ok"
    fail_reason: str | None = None
    semantic_version: str = ""
    allowlist_version: str = ""
    catalog_fingerprint: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "tables": [{"name": t.name, "reason": t.reason, "score": t.score} for t in self.tables],
            "columns": [
                {"table": c.table, "column": c.column, "role": c.role, "reason": c.reason}
                for c in self.columns
            ],
            "joins": [{"left": j.left, "right": j.right, "reason": j.reason} for j in self.joins],
            "union_tables": list(self.union_tables),
            "suggested_filters": list(self.suggested_filters),
            "confidence": self.confidence,
            "status": self.status,
            "fail_reason": self.fail_reason,
            "semantic_version": self.semantic_version,
            "allowlist_version": self.allowlist_version,
            "catalog_fingerprint": self.catalog_fingerprint,
        }

    def table_names(self) -> set[str]:
        return {t.name.lower() for t in self.tables}

    def column_set(self) -> set[str]:
        return {f"{c.table.lower()}.{c.column.lower()}" for c in self.columns}


_STATION_TABLE = "t_station"
_PROJECT_JOIN_SUFFIX = ".project_name=t_station.name"


def _fp(parts: list[str]) -> str:
    raw = "\n".join(parts)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def _normalize_forced_tables(forced_tables: list[str] | None) -> set[str]:
    out: set[str] = set()
    for raw in forced_tables or []:
        t = str(raw or "").strip().lower()
        if t:
            out.add(t)
    return out



_MEASURE_COLUMNS = frozenset(
    {
        "total_settle",
        "deep",
        "elevation",
        "pressure",
        "temp",
        "real_time_rain",
        "displacement_3d",
        "displacement_2d",
    }
)
_TIME_COLUMNS = frozenset({"data_time"})
_KEY_COLUMNS = frozenset(
    {"id", "station_id", "station_name", "project_name", "name", "code", "area"}
)


def _column_role(column: str) -> str:
    c = (column or "").strip().lower()
    if c in _MEASURE_COLUMNS:
        return "measure"
    if c in _TIME_COLUMNS:
        return "time"
    if c in _KEY_COLUMNS:
        return "key"
    return "dim"


def _append_all_table_columns(
    linked: LinkedSchema,
    table: str,
    table_columns: dict[str, set[str]],
    *,
    reason: str,
) -> None:
    """将已链接表的 catalog 全列纳入 SchemaLink（含 lon/lat 等）。"""
    existing = {(c.table.lower(), c.column.lower()) for c in linked.columns}
    for col in sorted(table_columns.get(table, set()) or []):
        cl = str(col).strip().lower()
        if not cl:
            continue
        key = (table.lower(), cl)
        if key in existing:
            continue
        linked.columns.append(
            LinkedColumn(table=table, column=cl, role=_column_role(cl), reason=reason)
        )
        existing.add(key)


def _pick_candidate_tables(
    semantic: SemanticBinding,
    allowlist: set[str],
    assets: SemanticAssets | None,
) -> list[tuple[str, str, float]]:
    candidates: dict[str, tuple[str, float]] = {}

    for m in semantic.metrics:
        for tbl in m.preferred_tables:
            tl = tbl.lower()
            if tl in allowlist:
                candidates[tl] = (f"metric:{m.id}", max(candidates.get(tl, ("", 0.0))[1], m.confidence))

    for tbl in semantic.device_type_tables:
        tl = tbl.lower()
        if tl in allowlist:
            candidates[tl] = (f"device_type", max(candidates.get(tl, ("", 0.0))[1], 0.8))

    if semantic.default_table and semantic.default_table.lower() in allowlist:
        tl = semantic.default_table.lower()
        candidates.setdefault(tl, ("default_subsidence", 0.5))

    ranked = sorted(
        [(tbl, reason, score) for tbl, (reason, score) in candidates.items()],
        key=lambda x: (-x[2], x[0]),
    )
    return ranked[:3]


def link_schema(
    question: str,
    intent: QuestionIntent,
    semantic: SemanticBinding,
    table_columns: dict[str, set[str]],
    *,
    allowlist: set[str] | None = None,
    join_whitelist: set[str] | None = None,
    assets: SemanticAssets | None = None,
    forced_tables: list[str] | None = None,
) -> LinkedSchema:
    profile = get_nl2sql_business_profile()
    allow = allowlist or set(table_columns.keys())
    if profile and profile.table_allowlist:
        profile_allow = {t.lower() for t in profile.table_allowlist}
        allow = {t for t in allow if t in profile_allow}

    semantic_root = None
    if profile:
        from pathlib import Path

        semantic_root = Path(__file__).resolve().parents[2] / profile.semantic_dict_path
        if assets is None and semantic_root.is_dir():
            assets = load_semantic_assets(str(semantic_root.resolve()))

    linked = LinkedSchema(
        semantic_version=semantic.semantic_version,
        allowlist_version=profile.allowlist_version_fp() if profile else "",
    )

    ranked = _pick_candidate_tables(semantic, allow, assets)
    forced = _normalize_forced_tables(forced_tables)
    station_catalog = is_station_catalog_question(question) or (
        (semantic.default_table or "").strip().lower() == _STATION_TABLE
        or "station_catalog_query" in (semantic.warnings or [])
    )
    if station_catalog and _STATION_TABLE in allow:
        forced_non_station = {t for t in forced if t != _STATION_TABLE}
        if not forced_non_station:
            ranked = [(_STATION_TABLE, "station_catalog", 1.0)]
    if forced:
        ranked = [r for r in ranked if r[0] in forced]
        if not ranked:
            for tbl in sorted(forced):
                if tbl == _STATION_TABLE:
                    continue
                if tbl in allow:
                    ranked.append((tbl, "forced_table", 1.0))
        if not ranked:
            linked.status = "failed"
            linked.fail_reason = "forced_table_not_in_allowlist"
            linked.confidence = 0.0
            return linked
    elif not ranked:
        linked.status = "failed"
        linked.fail_reason = "no_candidate_table_in_allowlist"
        linked.confidence = 0.0
        return linked

    for tbl, reason, score in ranked:
        linked.tables.append(LinkedTable(name=tbl, reason=reason, score=score))

    primary = ranked[0][0]
    primary_is_station = primary == _STATION_TABLE
    cols = table_columns.get(primary, set())

    # measure + time columns from semantic metrics
    for m in semantic.metrics:
        for col in m.preferred_columns:
            cl = col.lower()
            if cl in cols:
                linked.columns.append(
                    LinkedColumn(table=primary, column=cl, role="measure", reason=f"metric:{m.id}")
                )
        tc = m.time_column.lower()
        if tc in cols:
            linked.columns.append(
                LinkedColumn(table=primary, column=tc, role="time", reason=f"metric:{m.id}")
            )

    # dim columns on primary table
    for dim_col, role in (
        ("station_id", "dim"),
        ("station_name", "dim"),
        ("project_name", "dim"),
        ("data_time", "time"),
    ):
        if dim_col in cols and not any(c.column == dim_col and c.table == primary for c in linked.columns):
            linked.columns.append(
                LinkedColumn(table=primary, column=dim_col, role=role, reason="table_default")
            )

    _append_all_table_columns(linked, primary, table_columns, reason="table_all_columns")

    need_station = (not primary_is_station) and bool(
        semantic.district_codes
        or semantic.station_ids
        or semantic.station_names
        or getattr(semantic, "project_names", None)
        or getattr(semantic, "device_coverage_project_names", None)
        or getattr(semantic, "preferred_station_names", None)
    )
    if need_station and _STATION_TABLE in allow:
        if not any(t.name == _STATION_TABLE for t in linked.tables):
            linked.tables.append(LinkedTable(name=_STATION_TABLE, reason="district_or_station_filter", score=0.7))
        _append_all_table_columns(
            linked, _STATION_TABLE, table_columns, reason="station_all_columns"
        )
        linked.joins.append(LinkedJoin(left=f"{primary}.project_name", right="t_station.name", reason="project_name=name"))

    if semantic.district_codes:
        for d in semantic.district_codes:
            linked.suggested_filters.append(
                {"table": _STATION_TABLE, "column": "area", "op": "=", "value": d, "source": "semantic"}
            )

    # 站点场地 → project_name / t_station.name；层位0/显式标 → station_name
    project_names = list(getattr(semantic, "project_names", None) or [])
    coverage_names = list(getattr(semantic, "device_coverage_project_names", None) or [])
    preferred_marks = list(getattr(semantic, "preferred_station_names", None) or [])
    if not project_names and semantic.station_names and not preferred_marks:
        # 兼容旧 binding：无 preferred 时，station_names 按 project_name 过滤
        project_names = list(semantic.station_names)

    # 点名站优先；否则用监测方式官方覆盖（P7）
    if project_names:
        effective_projects = project_names
        project_filter_source = (
            "device_station_map"
            if any(str(w).startswith("device_station_map_intersect") for w in (semantic.warnings or []))
            else "semantic"
        )
    else:
        effective_projects = coverage_names
        project_filter_source = "device_station_map" if coverage_names else "semantic"

    def _append_project_name_filters(table: str, column: str) -> None:
        if not effective_projects:
            return
        if len(effective_projects) == 1:
            linked.suggested_filters.append(
                {
                    "table": table,
                    "column": column,
                    "op": "=",
                    "value": effective_projects[0],
                    "source": project_filter_source,
                }
            )
        else:
            linked.suggested_filters.append(
                {
                    "table": table,
                    "column": column,
                    "op": "in",
                    "value": list(effective_projects),
                    "source": project_filter_source,
                }
            )

    if primary_is_station:
        # 清单问句：覆盖名单落在 t_station.name
        _append_project_name_filters(_STATION_TABLE, "name")
    else:
        if semantic.station_ids:
            for sid in semantic.station_ids:
                linked.suggested_filters.append(
                    {"table": primary, "column": "station_id", "op": "=", "value": sid, "source": "semantic"}
                )

        _append_project_name_filters(primary, "project_name")

        if preferred_marks:
            filter_source = (
                "fcb_compress"
                if getattr(semantic, "compress_pairs", None)
                else "fcb_layer0"
            )
            if len(preferred_marks) == 1:
                linked.suggested_filters.append(
                    {
                        "table": primary,
                        "column": "station_name",
                        "op": "=",
                        "value": preferred_marks[0],
                        "source": filter_source,
                    }
                )
            else:
                linked.suggested_filters.append(
                    {
                        "table": primary,
                        "column": "station_name",
                        "op": "in",
                        "value": list(preferred_marks),
                        "source": filter_source,
                    }
                )

    # auxiliary tables for multi-metric questions（锁表时禁止拉入其它 t_data_wash_*）
    aux_tables: set[str] = set()
    if (not forced) and (not primary_is_station):
        for m in semantic.metrics[1:]:
            for tbl in m.preferred_tables:
                tl = tbl.lower()
                if tl != primary and tl in allow:
                    aux_tables.add(tl)
    for aux in aux_tables:
        linked.tables.append(LinkedTable(name=aux, reason="auxiliary_metric", score=0.6))
        _append_all_table_columns(linked, aux, table_columns, reason="auxiliary_all_columns")
        linked.joins.append(
            LinkedJoin(
                left=f"{aux}.project_name",
                right="t_station.name",
                reason="aux_join_station",
            )
        )

    linked.confidence = min(1.0, ranked[0][2] + 0.1 * (len(ranked) - 1))
    linked.catalog_fingerprint = _fp(
        [t.name for t in linked.tables] + [f"{c.table}.{c.column}" for c in linked.columns]
    )

    if not linked.columns:
        linked.status = "weak"
        linked.fail_reason = "no_columns_linked"
    elif (not primary_is_station) and not any(c.role == "measure" for c in linked.columns):
        linked.status = "weak"
        linked.fail_reason = "no_measure_column"

    logger.info(
        "SchemaLink status=%s tables=%s primary=%s confidence=%.2f station_catalog=%s cols=%d",
        linked.status,
        [t.name for t in linked.tables],
        primary,
        linked.confidence,
        station_catalog,
        len(linked.columns),
    )
    return linked


def filter_catalog_tables_by_linked_schema(
    catalog_tables: list[Any],
    linked: LinkedSchema,
    *,
    mode: str,
    full_table_names: set[str],
) -> list[Any]:
    """按链接结果过滤反射表列表。"""
    if mode == "legacy_wide":
        return catalog_tables
    linked_names = linked.table_names()
    if not linked_names:
        return catalog_tables
    if mode == "linked_only":
        return [t for t in catalog_tables if (t.name or "").lower() in linked_names]
    # linked_prefer: 链接表 + 同 allowlist 内其余表（排序前置）
    linked_list = [t for t in catalog_tables if (t.name or "").lower() in linked_names]
    rest = [t for t in catalog_tables if (t.name or "").lower() not in linked_names]
    return linked_list + rest


def narrow_validation_sets(
    linked: LinkedSchema,
    allowed_tables: set[str],
    allowed_columns: set[str],
    table_columns: dict[str, set[str]],
    *,
    mode: str,
) -> tuple[set[str], set[str], dict[str, set[str]]]:
    """
    按 Schema Link 结果收窄表白名单与列白名单。

    列白名单统一为**裸列名**（与 ``SQLValidator.validate_identifiers`` 一致），
    避免 ``table.column`` 与 SQL 中 ``alias.column`` 抽取的裸名无法匹配。
    """
    if mode == "legacy_wide" or linked.status == "failed":
        return allowed_tables, allowed_columns, table_columns
    lt = linked.table_names()
    if not lt:
        return allowed_tables, allowed_columns, table_columns
    new_tables = {t for t in allowed_tables if t in lt}
    new_tc = {k: v for k, v in table_columns.items() if k in new_tables}
    known_bare = {c for cols in new_tc.values() for c in cols}

    def _to_bare(col: str) -> str:
        c = col.strip().lower()
        return c.split(".", 1)[-1] if "." in c else c

    linked_bare: set[str] = set()
    for c in linked.columns:
        t = c.table.lower()
        col = c.column.lower()
        if t not in new_tables:
            continue
        if new_tc.get(t) and col not in new_tc[t]:
            continue
        linked_bare.add(col)

    if linked_bare:
        new_cols = linked_bare
        if known_bare:
            new_cols &= known_bare
        prior_bare = {_to_bare(c) for c in allowed_columns if c}
        # 仅保留属于收窄表集合的先验列：qualified 按表过滤，bare 与 known 求交
        scoped_prior: set[str] = set()
        for c in allowed_columns:
            raw = (c or "").strip().lower()
            if not raw:
                continue
            if "." in raw:
                t, col = raw.split(".", 1)
                if t in new_tables:
                    scoped_prior.add(col)
            elif not known_bare or raw in known_bare:
                scoped_prior.add(raw)
        if scoped_prior:
            new_cols &= scoped_prior
        elif prior_bare and known_bare:
            new_cols &= prior_bare & known_bare
    else:
        new_cols = set(known_bare) if known_bare else {_to_bare(c) for c in allowed_columns if c}
    return new_tables, new_cols, new_tc
