"""
智能客服：规则层意图分类（查库 vs 文档问答）。

与 LangGraph `intent_classify` 节点配合使用：
- `data_query`：倾向结构化台账/检修/缺陷等，走 NL2SQL；
- `kb_qa`：概念、机理、标准解读、故障原因等，走向量 RAG；
- `clarify`：过短或指代不清。

说明：规则可解释、低成本；后续可在此模块旁挂 LLM 分类器，保持 label 兼容即可。
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, NamedTuple, Tuple

from app.services.chatbot_image_utils import (
    ORIGINAL_IMAGE_BLOCK_MARKER,
    PROCESSED_IMAGE_BLOCK_MARKER,
    strip_image_block_from_history,
)

_LEGACY_IMAGE_MARKER = "\n\n[image_urls]\n"

# 偏「知识/机理/规范」类提问 → 文档 RAG
_CONCEPTUAL_MARKERS = (
    "为什么",
    "什么原因",
    "常见原因",
    "机理",
    "原理",
    "如何预防",
    "如何防范",
    "如何控制",
    "危害",
    "风险",
    "标准是什么",
    "规范",
    "条款",
    "符合什么",
    "有何区别",
    "什么是",
    "含义",
    "定义",
    "解释",
    "依据",
    "是否允许",
    "注意事项",
    "经验",
    "论文",
    "参考",
)

# 偏「查数/列表/记录」→ NL2SQL（需业务库已接入）
_DATA_MARKERS = (
    "统计",
    "查询",
    "查出",
    "检索",
    "列出",
    "罗列",
    "导出",
    "有多少",
    "多少条",
    "几条",
    "哪几台",
    "哪台",
    "最近一次",
    "上次",
    "上次检修",
    "台账",
    "检修记录",
    "缺陷记录",
    "缺陷单",
    "工单",
    "记录表",
    "设备清单",
    "列表",
    "排序",
    "top",
    "TOP",
    "第几页",
    "分页",
    "筛选",
    "按时间",
    "按机组",
    "按电厂",
)

_UNCLEAR_PATTERNS = (
    r"^怎么弄[啊呀吗呢]?$",
    r"^怎么办[啊呀吗呢]?$",
    r"^啥意思[啊呀吗呢]?$",
    r"^(这个|那个|它).{0,3}(怎么|怎么办|啥意思)",
)

_STRONG_DATA_RE = re.compile(
    r"(统计|查询|查出|列出|有多少|多少条|几条|台账|检修记录|缺陷记录|设备清单|工单号|编号为)",
    re.I,
)

# 助手上一轮若为「请补充信息」类固定话术，用于推断任务延续
_CLARIFY_REPLY_SNIPPET = "请补充更具体的信息"


def _raw_has_image_blocks(content: str) -> bool:
    if not content:
        return False
    return any(
        m in content
        for m in (
            _LEGACY_IMAGE_MARKER,
            ORIGINAL_IMAGE_BLOCK_MARKER,
            PROCESSED_IMAGE_BLOCK_MARKER,
        )
    )


def build_intent_context_from_history(
    messages: List[Dict[str, Any]] | None,
    *,
    max_messages: int = 8,
    max_chars: int = 720,
    max_line_chars: int = 240,
) -> Tuple[str, str]:
    """
    从历史构造意图用的「可读摘要」与「上一轮任务类型」启发式标签。

    prev_task_type ∈ {unknown, multimodal_qa, text_kb_qa, data_query_thread, after_clarify}
    """
    if not messages:
        return "", "unknown"

    tail = messages[-max(1, max_messages) :]
    lines: list[str] = []
    for m in tail:
        role = str(m.get("role", "user") or "user").lower()
        raw = m.get("content", "")
        text = raw if isinstance(raw, str) else str(raw or "")
        plain = strip_image_block_from_history(text).strip()
        if not plain and role == "user" and _raw_has_image_blocks(text):
            plain = "（上轮含图片）"
        snippet = plain[:max_line_chars] if plain else ""
        if snippet:
            lines.append(f"{role}: {snippet}")

    summary = "\n".join(lines).strip()
    if len(summary) > max_chars:
        summary = summary[-max_chars:]

    prev_task = _infer_prev_task_type_from_tail(tail)
    return summary, prev_task


def _infer_prev_task_type_from_tail(tail: List[Dict[str, Any]]) -> str:
    if not tail:
        return "unknown"

    last_assistant = ""
    for m in reversed(tail):
        if str(m.get("role", "")).lower() == "assistant":
            last_assistant = str(m.get("content", "") or "")
            break
    if last_assistant and _CLARIFY_REPLY_SNIPPET in last_assistant:
        return "after_clarify"

    last_user_raw = ""
    for m in reversed(tail):
        if str(m.get("role", "")).lower() == "user":
            last_user_raw = str(m.get("content", "") or "")
            break
    if not last_user_raw:
        return "unknown"
    if _raw_has_image_blocks(last_user_raw):
        return "multimodal_qa"
    plain_u = strip_image_block_from_history(last_user_raw)
    if _has_data(plain_u) and not _has_conceptual(plain_u):
        return "data_query_thread"
    return "text_kb_qa"


def _history_supports_kb_continuation(history_summary: str, prev_task_type: str) -> bool:
    """是否更像「延续同一咨询线程」而非冷启动短句。"""
    if prev_task_type in {"multimodal_qa", "text_kb_qa", "after_clarify"}:
        return True
    if prev_task_type == "data_query_thread":
        return False
    markers = (
        "缺陷",
        "图片",
        "照片",
        "上图",
        "这张",
        "识别",
        "损伤",
        "裂纹",
        "泄漏",
        "检修",
        "设备",
        "炉",
        "管",
    )
    return bool(history_summary) and any(x in history_summary for x in markers)


class IntentRuleResult(NamedTuple):
    """规则层意图输出；含意图用的历史摘要与前一轮任务类型（可观测、可回归）。"""

    intent_label: str
    intent_reason: str
    intent_confidence: float
    history_summary: str
    prev_task_type: str


def _has_conceptual(q: str) -> bool:
    qn = q.replace(" ", "")
    return any(m in qn for m in _CONCEPTUAL_MARKERS)


def _has_data(q: str) -> bool:
    qn = q.replace(" ", "")
    if any(m.lower() in qn.lower() for m in _DATA_MARKERS):
        return True
    return _STRONG_DATA_RE.search(qn) is not None


def classify_chatbot_intent(
    query: str,
    *,
    enable_nl2sql_route: bool,
    image_urls: List[str],
    history_messages: List[Dict[str, Any]] | None = None,
) -> IntentRuleResult:
    """
    intent_label ∈ {clarify, data_query, kb_qa}

    history_messages：最近若干轮会话（与 load_history 同源）；用于短句/指代消解与路由，
    不改变「查库 vs 文档」的主启发式，仅在边界场景结合摘要与前一轮任务类型。
    """
    q = (query or "").strip()
    h_sum, prev_task = build_intent_context_from_history(history_messages)

    def _out(label: str, reason: str, conf: float) -> IntentRuleResult:
        return IntentRuleResult(label, reason, conf, h_sum, prev_task)

    if not q:
        return _out("clarify", "empty_query", 0.99)

    # 多模态优先：避免「这个呢」等短句在命中长度阈值前先被判为 clarify，且与 NL2SQL 分流解耦
    if image_urls:
        return _out("kb_qa", f"has_images_default_kb_qa|ctx_task={prev_task}", 0.88)

    # 极短纯文本（≤2 字）：若历史表明仍在同一客服问答线程，则延续 kb_qa（避免「呢/继续」误触 clarify）
    if len(q) <= 2:
        if (
            len(q) >= 2
            and h_sum
            and prev_task not in ("unknown", "data_query_thread")
            and _history_supports_kb_continuation(h_sum, prev_task)
        ):
            return _out("kb_qa", f"short_followup_continues_thread|ctx_task={prev_task}", 0.76)
        return _out("clarify", f"query_too_short|ctx_task={prev_task}", 0.92)

    for p in _UNCLEAR_PATTERNS:
        if re.search(p, q):
            if _history_supports_kb_continuation(h_sum, prev_task):
                return _out("kb_qa", f"ambiguous_pattern_resolved_by_ctx|ctx_task={prev_task}", 0.83)
            return _out("clarify", f"ambiguous_query_pattern|ctx_task={prev_task}", 0.9)

    if not enable_nl2sql_route:
        return _out("kb_qa", "nl2sql_route_disabled", 0.85)

    conceptual = _has_conceptual(q)
    data = _has_data(q)

    if data and not conceptual:
        return _out("data_query", "structured_query_heuristic", 0.8)
    if conceptual and not data:
        return _out("kb_qa", "conceptual_qa_heuristic", 0.82)
    if data and conceptual:
        # 同时命中时：更偏「解释/原因」的仍走文档
        if any(x in q for x in ("为什么", "原因", "机理", "原理", "如何形成", "如何预防")):
            return _out("kb_qa", "mixed_prefers_conceptual", 0.72)
        return _out("data_query", "mixed_prefers_structured", 0.7)

    return _out("kb_qa", "default_kb_qa", 0.82)
