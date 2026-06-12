"""
综合分析流式 SSE 尾帧 meta（与智能客服 finished.meta 超集对齐）。

中间帧仍使用 ``event`` 字段；仅尾帧为 ``{"finished": true, "meta": {...}}``，
便于与 AI 问答共用同一套尾帧解析器。
"""

from __future__ import annotations

from time import perf_counter
from typing import Any

from app.llm.graphs.chatbot_rag_citations import filter_rag_citation_dicts


def build_analysis_finished_meta(
    *,
    request_id: str,
    plan_id: str,
    analysis_type: str,
    data_mode: str,
    used_rag: bool,
    used_plan_rag: bool,
    used_business_rag: bool,
    rag_citations: list[dict[str, Any]] | None,
    start_ts: float,
    synthesis_strategy_effective: str | None = None,
    synthesis_ms: int | None = None,
    used_nl2sql: bool | None = None,
    nl2sql_sql: str | None = None,
    processed_image_urls: list[str] | None = None,
    original_image_urls: list[str] | None = None,
    retrieval_attempts: int | None = None,
    rag_namespace: str | None = None,
) -> dict[str, Any]:
    """组装 finished.meta：智能客服同名字段 + 综合分析扩展字段。"""
    citations = filter_rag_citation_dicts(list(rag_citations or []))
    attempts = retrieval_attempts
    if attempts is None:
        attempts = int(bool(used_plan_rag)) + int(bool(used_business_rag))

    ns = rag_namespace
    if not ns and citations:
        ns = str(citations[0].get("namespace") or "").strip() or None

    nl2sql_used = bool(used_nl2sql) if used_nl2sql is not None else data_mode in ("nl2sql", "img_diag")

    meta: dict[str, Any] = {
        # 与智能客服 finished.meta 同形（无业务含义的字段置空/false，避免误导）
        "used_rag": bool(used_rag),
        "intent_label": f"analysis_{analysis_type}",
        "retrieval_attempts": int(attempts),
        "rag_engine": "hybrid",
        "rag_namespace": ns,
        "rag_scope_reason": None,
        "rag_scope_fallback": False,
        "faq_soft_direct": False,
        "faq_soft_direct_reason": "",
        "status": "completed",
        "duration_ms": int((perf_counter() - start_ts) * 1000),
        "terminate_reason": None,
        "is_partial": False,
        "similar_cases_appended": False,
        "similar_case_namespace": None,
        "fault_detect_sources": [],
        "fault_detect_confidence": 0.0,
        "need_similar_cases": False,
        "used_nl2sql": nl2sql_used,
        "nl2sql_sql": (nl2sql_sql or None) if nl2sql_used else None,
        "suggested_questions": [],
        "rag_citations": citations,
        "processed_image_urls": [
            u for u in (processed_image_urls or []) if isinstance(u, str) and u.strip()
        ],
        "original_image_urls": [
            u for u in (original_image_urls or []) if isinstance(u, str) and u.strip()
        ],
        "stream_id": request_id,
        # 综合分析扩展（trace / 前端展示 / 调试）
        "request_id": request_id,
        "plan_id": plan_id,
        "analysis_type": analysis_type,
        "data_mode": data_mode,
        "used_plan_rag": bool(used_plan_rag),
        "used_business_rag": bool(used_business_rag),
    }
    if synthesis_strategy_effective:
        meta["synthesis_strategy_effective"] = synthesis_strategy_effective
    if synthesis_ms is not None:
        meta["synthesis_ms"] = synthesis_ms
    return meta


def analysis_finished_sse_event(meta: dict[str, Any]) -> dict[str, Any]:
    """尾帧 SSE 载荷（与 ``/chatbot/chat/stream`` 结束帧同形）。"""
    return {"finished": True, "meta": meta}
