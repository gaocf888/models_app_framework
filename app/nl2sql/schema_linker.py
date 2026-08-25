"""显式 Schema 链接：在 LLM 生成前收窄表/列 catalog。"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from typing import Any

from app.core.logging import get_logger
from app.nl2sql.nl2sql_business_profile import get_nl2sql_business_profile
from app.nl2sql.semantic_layer import SemanticBinding, SemanticAssets, load_semantic_assets
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
    if not ranked:
        linked.status = "failed"
        linked.fail_reason = "no_candidate_table_in_allowlist"
        linked.confidence = 0.0
        return linked

    for tbl, reason, score in ranked:
        linked.tables.append(LinkedTable(name=tbl, reason=reason, score=score))

    primary = ranked[0][0]
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

    need_station = bool(semantic.district_codes) or bool(semantic.station_ids) or bool(semantic.station_names)
    if need_station and _STATION_TABLE in allow:
        if not any(t.name == _STATION_TABLE for t in linked.tables):
            linked.tables.append(LinkedTable(name=_STATION_TABLE, reason="district_or_station_filter", score=0.7))
        st_cols = table_columns.get(_STATION_TABLE, set())
        for sc, role in (("name", "dim"), ("area", "dim"), ("code", "dim"), ("id", "dim")):
            if sc in st_cols:
                linked.columns.append(
                    LinkedColumn(table=_STATION_TABLE, column=sc, role=role, reason="station_dim")
                )
        join_expr = f"{primary}.project_name=t_station.name"
        linked.joins.append(LinkedJoin(left=f"{primary}.project_name", right="t_station.name", reason="project_name=name"))

    if semantic.district_codes:
        for d in semantic.district_codes:
            linked.suggested_filters.append(
                {"table": _STATION_TABLE, "column": "area", "op": "=", "value": d, "source": "semantic"}
            )
    if semantic.station_ids:
        for sid in semantic.station_ids:
            linked.suggested_filters.append(
                {"table": primary, "column": "station_id", "op": "=", "value": sid, "source": "semantic"}
            )
    if semantic.station_names:
        for sn in semantic.station_names:
            linked.suggested_filters.append(
                {"table": primary, "column": "station_name", "op": "like", "value": sn, "source": "semantic"}
            )

    # auxiliary tables for multi-metric questions
    aux_tables: set[str] = set()
    for m in semantic.metrics[1:]:
        for tbl in m.preferred_tables:
            tl = tbl.lower()
            if tl != primary and tl in allow:
                aux_tables.add(tl)
    for aux in aux_tables:
        linked.tables.append(LinkedTable(name=aux, reason="auxiliary_metric", score=0.6))
        for col in table_columns.get(aux, set()):
            if col in ("total_settle", "deep", "elevation", "pressure", "temp", "real_time_rain", "displacement_3d", "displacement_2d"):
                linked.columns.append(
                    LinkedColumn(table=aux, column=col, role="measure", reason="auxiliary")
                )
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
    elif not any(c.role == "measure" for c in linked.columns):
        linked.status = "weak"
        linked.fail_reason = "no_measure_column"

    logger.info(
        "SchemaLink status=%s tables=%s primary=%s confidence=%.2f",
        linked.status,
        [t.name for t in linked.tables],
        primary,
        linked.confidence,
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
    if mode == "legacy_wide" or linked.status == "failed":
        return allowed_tables, allowed_columns, table_columns
    lt = linked.table_names()
    if not lt:
        return allowed_tables, allowed_columns, table_columns
    new_tables = {t for t in allowed_tables if t in lt}
    new_tc = {k: v for k, v in table_columns.items() if k in new_tables}
    new_cols: set[str] = set()
    for c in linked.columns:
        key = f"{c.table.lower()}.{c.column.lower()}"
        if c.table.lower() in new_tables:
            new_cols.add(key)
    if new_cols:
        scoped_cols = {c for c in allowed_columns if c.split(".")[0] in new_tables}
        if scoped_cols:
            new_cols &= scoped_cols
    else:
        new_cols = {c for c in allowed_columns if c.split(".")[0] in new_tables}
    return new_tables, new_cols, new_tc

