from __future__ import annotations

"""
综合分析 LangGraph 状态 TypedDict 定义。

`StateGraph(AnalysisGraphState)` 下各字段为独立 channel，节点可只返回变更子集；
勿使用 `StateGraph(dict)`：其对应单一 `__root__` 通道，子集返回值会覆盖整份状态导致丢键。
"""

from typing import Any, Dict, List, Literal, Optional, TypedDict

AnalysisType = Literal["overheat_guidance", "maintenance_strategy", "custom"]
DataMode = Literal["payload", "nl2sql"]


class NL2SQLPlanItem(TypedDict, total=False):
    """对外 API / trace 中描述单条 NL2SQL 计划项的轻量结构（与 `_PlanTask` 字段对应）。"""

    item_id: str
    purpose: str
    question: str
    expected_tables: List[str]
    expected_fields: List[str]
    namespace_hint: str
    max_rows: int
    status: Literal["pending", "success", "failed", "skipped"]
    sql: Optional[str]
    rows: Optional[List[Dict[str, Any]]]
    error: Optional[str]


class AnalysisGraphState(TypedDict, total=False):
    """
    综合分析 LangGraph 共享状态。

    节点只增量返回本状态子集；LangGraph 按字段 last-write 合并。
    字段包含：
    - 请求快照（model_dump）与运行期 ID；
    - RAG / 质量门 / NL2SQL 中间结果；
    - 最终 `v2_result`（由 finalize 节点写入）。
    """

    # ----- 请求（序列化快照）-----
    payload_request: Dict[str, Any]
    nl2sql_request: Dict[str, Any]

    request_id: str
    plan_id: str
    user_id: str
    session_id: str
    analysis_type: AnalysisType
    query: str
    data_mode: DataMode
    options: Dict[str, Any]
    _checkpoint_thread_id: str

    # ----- 通用 -----
    node_latency_ms: Dict[str, int]
    node_status: Dict[str, str]
    degrade_reasons: List[str]

    # ----- Payload 分支 -----
    input_payload: Dict[str, Any]
    context_snippets: List[str]
    rag_sources: List[Dict[str, Any]]
    used_rag: bool
    quality_report: Dict[str, Any]

    summary: str
    structured_report: Dict[str, Any]
    suggestions: List[Dict[str, Any]]
    template_versions: Dict[str, str]

    # ----- NL2SQL 分支 -----
    intent_llm_result: Dict[str, Any]
    planner_warnings: List[str]
    intent_version: str
    data_plan_version: str
    plan_rag_sources: List[Dict[str, Any]]
    plan_context: List[str]
    plan_tasks: List[Dict[str, Any]]
    nl2sql_calls: List[Dict[str, Any]]
    gathered_data: Dict[str, List[Dict[str, Any]]]
    task_status: Dict[str, str]
    acquire_latency_ms: int

    synthesis_version: str
    report_version: str

    # ----- 输出 -----
    v2_result: Any  # AnalysisV2Result，避免循环引用用 Any
