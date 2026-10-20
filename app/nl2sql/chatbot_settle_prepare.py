"""智能问答沉降查数：运维分流、锁库、规则 scope、period 确定性（失败回退 LLM）。

不照搬数据查询 HITL；confirmed_scope 语义为「规则已解析」的机器快照。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.core.logging import get_logger
from app.nl2sql.scope_station_promote import promote_station_ids

logger = get_logger(__name__)

__all__ = [
    "ChatbotSettlePrepareResult",
    "prepare_chatbot_settle_query",
    "promote_station_ids",
    "try_chatbot_period_list",
]


@dataclass
class ChatbotSettlePrepareResult:
    """组装 NL2SQLQueryRequest 前的沉降/运维分流结果。"""

    ops_routed: bool = False
    library_source: str | None = None  # parsed | default | none | ambiguous
    library_id: str | None = None
    forced_tables: list[str] | None = None
    confirmed_scope: dict[str, Any] | None = None
    structured_filters: dict[str, Any] | None = None
    time_intent_text: str | None = None
    original_query: str | None = None
    period_eligible: bool = False
    library: Any | None = None
    scope: Any | None = None
    ambiguous_message: str | None = None
    warnings: list[str] = field(default_factory=list)


def _subsidence_chatbot() -> bool:
    try:
        from app.llm.graphs.chatbot_business_profile import get_chatbot_business_profile

        return get_chatbot_business_profile().domain == "subsidence"
    except Exception:  # noqa: BLE001
        return False


def prepare_chatbot_settle_query(question: str) -> ChatbotSettlePrepareResult:
    """解析运维/沉降锁库与规则 scope；非地降域返回空准备（沿用裸问句）。"""
    q = (question or "").strip()
    out = ChatbotSettlePrepareResult(time_intent_text=q or None, original_query=q or None)
    if not q or not _subsidence_chatbot():
        return out

    try:
        from app.nl2sql.om_intent import is_ops_question
    except Exception:  # noqa: BLE001
        is_ops_question = None  # type: ignore[assignment]

    if is_ops_question is not None and is_ops_question(q):
        out.ops_routed = True
        out.library_source = "none"
        logger.info("chatbot.settle_prepare ops_routed=1 (no settlement forced_tables)")
        return out

    try:
        from app.data_query_agent.catalog import get_library_catalog
        from app.data_query_agent.library_intent import match_library_ids
        from app.data_query_agent.scope_intent import resolve_scope_intent
        from app.data_query_agent.acquire import is_period_time
    except Exception as exc:  # noqa: BLE001
        logger.warning("chatbot.settle_prepare catalog unavailable err=%s", exc)
        return out

    try:
        cat = get_library_catalog()
    except Exception as exc:  # noqa: BLE001
        logger.warning("chatbot.settle_prepare get_library_catalog failed err=%s", exc)
        return out

    hits = match_library_ids(q, cat)
    library = None
    source: str | None = None

    if len(hits) == 1:
        library = cat.get(hits[0])
        source = "parsed"
    elif len(hits) >= 2:
        names: list[str] = []
        for lid in hits:
            lib = cat.get(lid)
            if lib is not None:
                names.append(lib.display_name or lid)
            else:
                names.append(lid)
        opts = "、".join(names) if names else "、".join(hits)
        out.library_source = "ambiguous"
        out.ambiguous_message = (
            f"您的问题可能对应多种监测类型（{opts}）。"
            "请指明要查哪一类（例如分层标、基岩标、地下水），以便准确查询。"
        )
        logger.info("chatbot.settle_prepare library_ambiguous hits=%s", hits)
        return out
    else:
        generic = any(w in q for w in (cat.generic_settle_words or ()))
        if generic:
            library = cat.get(cat.default_library_id)
            source = "default"
            out.warnings.append("library_defaulted")

    if library is None:
        out.library_source = "none"
        logger.info("chatbot.settle_prepare library unresolved; no forced_tables")
        return out

    out.library = library
    out.library_id = library.id
    out.library_source = source
    out.forced_tables = [library.table]

    scope = resolve_scope_intent(q, library)
    # 再跑一遍站点提升（resolve_scope_intent 已含；保持幂等）
    cs = dict(scope.confirmed_scope or {})
    sid, sname = promote_station_ids(cs.get("station_id"), cs.get("station_name"))
    cs["station_id"] = sid
    cs["station_name"] = sname
    scope.confirmed_scope = cs
    out.scope = scope
    out.confirmed_scope = cs

    filters: dict[str, Any] = {}
    if cs.get("district"):
        filters["district"] = cs["district"]
        filters["area"] = cs["district"]
    if cs.get("station_id"):
        filters["station_id"] = cs["station_id"]
    if cs.get("station_name"):
        filters["station_name"] = cs["station_name"]
    out.structured_filters = filters or None

    grain = str(getattr(scope, "grain", "") or "station")
    out.period_eligible = bool(grain == "station" and is_period_time(scope))
    logger.info(
        "chatbot.settle_prepare library_source=%s library_id=%s table=%s "
        "period_eligible=%s district=%s station=%s",
        out.library_source,
        out.library_id,
        library.table,
        out.period_eligible,
        cs.get("district") or "",
        cs.get("station_id") or cs.get("station_name") or "",
    )
    return out


async def try_chatbot_period_list(
    prepare: ChatbotSettlePrepareResult,
    *,
    max_rows: int = 80,
) -> tuple[str, list[dict[str, Any]]] | None:
    """站点+区间时间确定性 SQL；成功返回 (sql, rows)，失败返回 None（调用方回退 LLM）。"""
    if not prepare.period_eligible or prepare.library is None or prepare.scope is None:
        return None
    try:
        from app.data_query_agent.period_sql import fetch_period_list

        sql, rows = await fetch_period_list(
            prepare.library,
            prepare.scope,
            max_rows=max_rows,
        )
        logger.info(
            "chatbot.period_deterministic hit library=%s rows=%s",
            prepare.library_id,
            len(rows),
        )
        return sql, rows
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "chatbot.period_list_fallback_llm library=%s err=%s",
            prepare.library_id,
            exc,
            exc_info=True,
        )
        return None
