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
from typing import List, Tuple

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
) -> Tuple[str, str, float]:
    """
    返回 (intent_label, intent_reason, intent_confidence)。
    intent_label ∈ {clarify, data_query, kb_qa}
    """
    q = (query or "").strip()
    if not q:
        return "clarify", "empty_query", 0.99

    if len(q) <= 4:
        return "clarify", "query_too_short", 0.92

    for p in _UNCLEAR_PATTERNS:
        if re.search(p, q):
            return "clarify", "ambiguous_query_pattern", 0.9

    # 多模态：默认走知识问答（避免对图片问题生成 SQL）
    if image_urls:
        return "kb_qa", "has_images_default_kb_qa", 0.88

    if not enable_nl2sql_route:
        return "kb_qa", "nl2sql_route_disabled", 0.85

    conceptual = _has_conceptual(q)
    data = _has_data(q)

    if data and not conceptual:
        return "data_query", "structured_query_heuristic", 0.8
    if conceptual and not data:
        return "kb_qa", "conceptual_qa_heuristic", 0.82
    if data and conceptual:
        # 同时命中时：更偏「解释/原因」的仍走文档
        if any(x in q for x in ("为什么", "原因", "机理", "原理", "如何形成", "如何预防")):
            return "kb_qa", "mixed_prefers_conceptual", 0.72
        return "data_query", "mixed_prefers_structured", 0.7

    return "kb_qa", "default_kb_qa", 0.82
