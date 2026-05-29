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

    # 槽执行控制
    slot_retry_nl2sql: bool
    needs_human_interrupt: bool
    abort_requested: bool
    slot_skipped: bool
    human_prompt: Optional[str]
    human_suggested_actions: List[str]
    human_interactions: List[Dict[str, Any]]

    # 节点间临时
    _last_slot_output: Dict[str, Any]
    _last_stream_chunks: List[str]
    _checkpoint_thread_id: str
    _run_started_at: float
    _final_result: Dict[str, Any]
