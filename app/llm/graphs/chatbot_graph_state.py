from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional, TypedDict


IntentLabel = Literal["kb_qa", "clarify", "unsafe", "handoff_human", "smalltalk"]


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
    image_urls: List[str]
    enable_rag: bool
    enable_context: bool
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

    # ===== 检索域（RAG + C-RAG）=====
    rag_engine: Literal["agentic", "hybrid"]
    context_snippets: List[str]
    retrieval_score: float
    retrieval_attempts: int

    # ===== 生成域（模型输入输出）=====
    llm_messages: List[Dict[str, Any]]
    answer_text: str
    # 流式增量缓存：用于最终 answer 拼接、客户端断连时 partial 落库。
    answer_parts: List[str]
    is_partial: bool

    # ===== 控制与可观测域 =====
    used_rag: bool
    # 状态机建议值：started/intented/retrieved/clarifying/answered/aborted/failed
    # - answered: 正常完成并落库完整 assistant
    # - aborted: 客户端断开，可能落库 partial assistant
    # - failed: 运行异常，仅落库 user（默认）
    status: str
    terminate_reason: Optional[str]
    error: Optional[str]
