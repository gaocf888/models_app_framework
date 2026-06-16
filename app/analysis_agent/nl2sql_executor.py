from __future__ import annotations

import time
from typing import Any

from app.core.logging import get_logger
from app.models.nl2sql import NL2SQLQueryRequest
from app.nl2sql.errors import NL2SQLExecutionError
from app.nl2sql.question_intent_display import trace_include_question_intent
from app.services.nl2sql_service import NL2SQLService

logger = get_logger(__name__)


def plan_item_resolved(item_id: str, *, gathered_data: dict[str, Any], task_status: dict[str, str]) -> bool:
    """会话级 plan 子任务是否已有终态（成功/空/失败），可跳过 NL2SQL。"""
    if item_id in task_status:
        return True
    return item_id in gathered_data


async def run_nl2sql_for_plan_item(
    *,
    nl2sql: NL2SQLService,
    user_id: str,
    session_id: str,
    question: str,
    item_id: str,
    analysis_type: str,
    plan_template_version: str,
    analysis_request_id: str,
    query: str,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """经 NL2SQL 公共基座取数：五元组对齐 QA 沉淀与 RAG 召回。"""
    t0 = time.perf_counter()
    include_intent = trace_include_question_intent()
    try:
        resp = await nl2sql.query(
            NL2SQLQueryRequest(
                user_id=user_id,
                session_id=session_id,
                question=question,
                analysis_type=analysis_type,
                analysis_request_id=analysis_request_id,
                plan_item_id=item_id,
                plan_template_version=plan_template_version,
                time_intent_text=(query or "").strip(),
            ),
            record_conversation=False,
            include_parsed_intent=include_intent,
        )
    except NL2SQLExecutionError as exc:
        latency_ms = int((time.perf_counter() - t0) * 1000)
        logger.error(
            "analysis_agent nl2sql execution failed item_id=%s latency_ms=%s error_code=%s detail=%s",
            item_id,
            latency_ms,
            exc.error_code,
            exc.log_detail(),
        )
        raise
    latency_ms = int((time.perf_counter() - t0) * 1000)
    rows = list(resp.rows or [])
    call_rec = {
        "item_id": item_id,
        "question": question[:500],
        "sql": resp.sql,
        "row_count": len(rows),
        "latency_ms": latency_ms,
        "request_id": analysis_request_id,
        "analysis_type": analysis_type,
        "plan_template_version": plan_template_version,
        "cache_hit": False,
        "question_intent": resp.parsed_intent,
    }
    return rows, call_rec


def task_status_from_rows(
    item_id: str,
    rows: list[dict],
    *,
    mandatory: bool,
    error: str | None = None,
) -> str:
    if error:
        return "mandatory_failed" if mandatory else "optional_failed"
    if rows:
        return "success"
    return "mandatory_empty" if mandatory else "optional_empty"
