from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional, TypedDict


IntentLabel = Literal["kb_qa", "clarify", "data_query", "unsafe", "handoff_human", "smalltalk"]


class ChatbotGraphState(TypedDict, total=False):
    """
    Chatbot LangGraph 的共享状态对象。

    设计目的：
    - 所有节点都只读/只写这一个 state，避免“隐式全局变量”导致的排障困难；
    - 字段按业务域分组，便于按阶段定位问题（意图、检索、生成、落库）；
    - `total=False` 允许节点做“增量更新”，每个节点只返回自己负责的字段。

    维护约束：
    - 新增字段时优先放入对应业务域，不要混放；
    - 若字段会进入 SSE `meta` 或 LangSmith，请保持 key 稳定，避免下游解析破坏；
    - `status` 与 `terminate_reason` 属于运维关键字段，变更前需同步文档与回归用例。
    """

    # ===== 请求输入域（来自 ChatRequest）=====
    user_id: str
    session_id: str
    query: str
    original_image_urls: List[str]
    image_urls: List[str]
    enable_rag: bool
    enable_context: bool
    enable_nl2sql_route: bool
    client_prompt_version: Optional[str]
    # 单轮读取历史窗口（每次最多读多少条）；与 CONV_MAX_HISTORY_MESSAGES（总保留上限）不是同一个概念。
    history_limit: int

    # ===== Prompt 域（模板策略）=====
    prompt_version: Optional[str]
    prompt_template_id: Optional[str]
    prompt_variant: Optional[str]
    system_prompt: str

    # ===== 意图域（路由控制）=====
    intent_label: IntentLabel
    intent_confidence: float
    intent_reason: str
    # 规则层从会话历史抽取（仅供观测/排障；不参与下游强制分支）
    intent_history_summary: str
    intent_prev_task_type: str

    # ===== 多轮历史（load_history）=====
    history_messages: List[Dict[str, Any]]

    # ===== 指代消解（§3.2，P0～P3）=====
    anaphora_type: str
    anaphora_rule_type: str
    anaphora_confidence: float
    anaphora_score_gap: float
    anaphora_source: str
    anaphora_anchor_block: str
    anaphora_slot_bullets: List[str]

    # ===== 检索域（RAG + C-RAG）=====
    rag_engine: Literal["agentic", "hybrid"]
    # 本轮主 RAG 限定 namespace；None 表示全库（由 rag_scope_resolve 写入，C-RAG 重试复用）
    rag_namespace: Optional[str]
    rag_scope_reason: str
    rag_scope_fallback: bool
    rag_query_boost: Optional[str]
    context_snippets: List[str]
    # 与本轮注入模型的向量片段对应的结构化引用（SSE finished.meta.rag_citations）
    rag_citations: List[Dict[str, Any]]
    retrieval_score: float
    retrieval_attempts: int
    # 高分 FAQ 软直通：为 true 时 kb_build_messages 不注入 history_messages（见 chatbot_faq_soft_direct.py）
    faq_soft_direct: bool
    faq_soft_direct_reason: str

    # ===== 生成域（模型输入输出）=====
    llm_messages: List[Dict[str, Any]]
    llm_max_tokens: int
    history_trim_dropped: int
    answer_text: str
    # 流式增量缓存：用于最终 answer 拼接、客户端断连时 partial 落库。
    answer_parts: List[str]
    is_partial: bool

    # ===== 相似案例 / 故障域（namespace 可配置）=====
    need_similar_cases: bool
    case_rag_query: str
    fault_detect_sources: List[str]
    fault_detect_confidence: float
    enable_fault_vision: Optional[bool]
    similar_cases_appended: bool

    # ===== NL2SQL 分支 =====
    used_nl2sql: bool
    nl2sql_sql: str
    nl2sql_failed: bool
    nl2sql_error_code: Optional[str]
    # 查数收紧分析旁路结构（列/行样本等）；正文仍为 answer_text Markdown
    nl2sql_analysis: Optional[Dict[str, Any]]
    # 查数成功待流式分析：含 system/user_content/table_fallback/display_rows 等
    nl2sql_analysis_stream_plan: Optional[Dict[str, Any]]

    # ===== 关联问题推荐 =====
    suggested_questions: List[str]

    # ===== 控制与可观测域 =====
    used_rag: bool
    # 状态机建议值：started/intented/retrieved/clarifying/answered/aborted/failed
    # - answered: 正常完成并落库完整 assistant
    # - aborted: 客户端断开，可能落库 partial assistant
    # - failed: 运行异常，仅落库 user（默认）
    status: str
    terminate_reason: Optional[str]
    error: Optional[str]
