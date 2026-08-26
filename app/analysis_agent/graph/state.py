from __future__ import annotations

from typing import Any, Dict, List, Optional, TypedDict


class AnalysisAgentState(TypedDict, total=False):
    request_id: str
    user_id: str
    session_id: str
    analysis_type: str
    query: str
    options: Dict[str, Any]

    ordered_slots: List[Dict[str, Any]]
    plan_tasks: List[Dict[str, Any]]
    slot_index: int
    slots_total: int

    gathered_data: Dict[str, List[Dict[str, Any]]]
    intent_context: List[str]
    from_report_spec: bool
    report_title: str
    report_tables: List[Dict[str, Any]]
    report_charts: List[Dict[str, Any]]
    task_status: Dict[str, str]
    nl2sql_calls: List[Dict[str, Any]]
    context_snippets: List[str]

    slot_outputs: List[Dict[str, Any]]
    slot_trace: List[Dict[str, Any]]
    summary_parts: List[str]

    structured_report: Dict[str, Any]
    trace: Dict[str, Any]
    error: Optional[str]

    pending_events: List[Dict[str, Any]]

    # 取数 / 质量门控制（T1：全量 acquire_data，无缺数 HITL）
    acquire_retry: bool
    slot_retry_nl2sql: bool  # 兼容旧测试；主路径改用 acquire_retry
    needs_human_interrupt: bool  # 保留字段；主路径不再置位
    abort_requested: bool
    slot_skipped: bool
    degrade_reasons: List[str]
    human_prompt: Optional[str]
    human_suggested_actions: List[str]
    human_interactions: List[Dict[str, Any]]
    _acquire_retries: int
    _l1_anchor_checked: bool
    quality_l1: Dict[str, Any]

    # 节点间临时
    _last_slot_output: Dict[str, Any]
    _last_stream_chunks: List[str]
    _checkpoint_thread_id: str
    _run_started_at: float
    _final_result: Dict[str, Any]
    _cancel_checker: Any
    _stream_id: str
    _narrative_live_streamed: bool
    _prepared_viz: Dict[str, Any]
