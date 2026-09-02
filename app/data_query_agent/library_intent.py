"""意图 1：锁定 library_id ↔ 物理表。"""

from __future__ import annotations

from dataclasses import dataclass, field

from app.data_query_agent.catalog import LibraryCatalog, LibraryDef, get_library_catalog


@dataclass
class LibraryIntentResult:
    """意图 1 结果：成功则锁库；失败则 interrupt_reason 驱动 HITL。"""

    ok: bool
    library: LibraryDef | None = None
    source: str | None = None  # request | parsed | default | hitl | llm
    warnings: list[str] = field(default_factory=list)
    interrupt_reason: str | None = None
    candidates: list[str] = field(default_factory=list)


def match_library_ids(query: str, catalog: LibraryCatalog | None = None) -> list[str]:
    """按同义词/表名/展示名匹配问句，返回去重后的 library_id。"""
    cat = catalog or get_library_catalog()
    q = query or ""
    q_lower = q.lower()
    hits: list[str] = []
    seen: set[str] = set()
    for phrase, lid in cat.phrases:
        if not phrase:
            continue
        if phrase.lower() in q_lower or phrase in q:
            if lid not in seen:
                seen.add(lid)
                hits.append(lid)
    return hits


def resolve_library_intent(
    query: str,
    library_id: str | None,
    *,
    catalog: LibraryCatalog | None = None,
    hitl_library_id: str | None = None,
) -> LibraryIntentResult:
    """
    优先级：HITL 选库 > 请求 library_id > 问句唯一命中 > 泛化沉降默认 > HITL。
    """
    cat = catalog or get_library_catalog()
    if hitl_library_id:
        lib = cat.get(hitl_library_id)
        if lib is None:
            return LibraryIntentResult(
                ok=False,
                interrupt_reason="library_id_invalid",
                candidates=[],
            )
        return LibraryIntentResult(ok=True, library=lib, source="hitl")

    requested = (library_id or "").strip().lower() or None
    hits = match_library_ids(query, cat)

    if requested:
        lib = cat.get(requested)
        if lib is None:
            return LibraryIntentResult(
                ok=False,
                interrupt_reason="library_id_invalid",
                candidates=hits,
            )
        warnings: list[str] = []
        # 树选库优先于问句解析；冲突只告警，不 HITL。
        if len(hits) == 1 and hits[0] != requested:
            warnings.append("library_conflict_nl_ignored")
        return LibraryIntentResult(ok=True, library=lib, source="request", warnings=warnings)

    if len(hits) == 1:
        lib = cat.get(hits[0])
        return LibraryIntentResult(ok=True, library=lib, source="parsed")

    if len(hits) >= 2:
        return LibraryIntentResult(
            ok=False,
            interrupt_reason="library_ambiguous",
            candidates=hits,
        )

    q = query or ""
    # 泛化「沉降」与基座一致，默认分层标，避免未锁库时扫多表。
    generic = any(w in q for w in cat.generic_settle_words)
    if generic:
        lib = cat.get(cat.default_library_id)
        if lib is not None:
            return LibraryIntentResult(
                ok=True,
                library=lib,
                source="default",
                warnings=["library_defaulted"],
            )

    return LibraryIntentResult(ok=False, interrupt_reason="library_unresolved", candidates=[])
