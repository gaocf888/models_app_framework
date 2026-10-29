"""解析 SQL 入口闸：像查数才继续；闲聊 / 身份 / 知识问答拒答。

请求里的 library_id 不算查数证据（树上点了分层标再问「你是谁」仍应拒答）。
"""

from __future__ import annotations

import re
from dataclasses import dataclass

NOT_DATA_QUERY_REASON = "not_data_query"
NOT_DATA_QUERY_MESSAGE = (
    "当前问题不是监测数据查询（闲聊、身份介绍或知识问答）。"
    "请改用智能客服问答；数据查询请说明要查的监测类型、行政区、站点或时间范围。"
)

_CHAT_PHRASES = (
    "你是谁",
    "您是谁",
    "你叫什么",
    "你的名字",
    "介绍一下你",
    "介绍下你自己",
    "介绍一下你自己",
    "你是什么模型",
    "你是chatgpt",
    "你是机器人",
    "who are you",
    "what are you",
    "你好啊",
    "您好啊",
    "你好呀",
    "您好呀",
    "早上好",
    "下午好",
    "晚上好",
    "谢谢你",
    "非常感谢",
    "再见",
    "拜拜",
    "你能做什么",
    "你会什么",
    "怎么用这个",
    "如何使用本系统",
    "使用说明",
)
_CHAT_EXACT = frozenset(
    {
        "你好",
        "您好",
        "hello",
        "hi",
        "hey",
        "谢谢",
        "感谢",
        "ok",
        "okay",
        "在吗",
        "在么",
    }
)
_KNOWLEDGE_PHRASES = (
    "什么是",
    "是什么",
    "为什么",
    "什么原因",
    "常见原因",
    "成因",
    "机理",
    "原理",
    "如何预防",
    "如何防范",
    "如何控制",
    "标准是什么",
    "规范",
    "规程",
    "条款",
    "有何区别",
    "有什么区别",
    "含义",
    "定义",
    "解释一下",
    "注意事项",
    "如何处置",
    "怎么处理",
    "如何处理",
    "处置建议",
    "监测方法",
    "论文",
)
_DATA_VERBS = (
    "查询",
    "查出",
    "查一下",
    "查下",
    "查一查",
    "查数据",
    "查数",
    "检索",
    "列出",
    "罗列",
    "统计",
    "导出",
    "筛选",
    "有哪些",
    "有多少",
    "多少条",
    "比较大",
    "偏大",
    "较大",
    "偏小",
    "最大",
    "最小",
    "排行",
    "排名",
    "对比",
)
_DATA_METRICS = (
    "沉降量",
    "年沉降",
    "累计沉降",
    "沉降速率",
    "回弹",
    "水位",
    "埋深",
    "位移",
    "孔隙",
    "气温",
    "降水",
    "total_settle",
    "监测数据",
    "观测数据",
)
_DATA_GRAIN = ("各区", "全市", "全北京", "监测点", "测点")
_LOOKUP_RE = re.compile(
    r"查.{0,12}(沉降|水位|分层|基岩|站点|监测|数据|gnss|位移|地下水|光纤|气象|孔压)",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class QueryKindResult:
    looks_like_data_query: bool
    kind: str  # data_query | chitchat | knowledge | unspecified


def _has_any(text: str, phrases: tuple[str, ...]) -> bool:
    low = text.lower()
    return any(p.lower() in low or p in text for p in phrases if p)


def _is_chitchat(query: str) -> bool:
    compact = re.sub(r"[\s，。！？、,.!?]+", "", query).strip().lower()
    if compact in _CHAT_EXACT or query.strip().lower() in _CHAT_EXACT:
        return True
    return _has_any(query, _CHAT_PHRASES)


def _is_knowledge(query: str) -> bool:
    return _has_any(query, _KNOWLEDGE_PHRASES)


def _has_geo_or_station(query: str) -> bool:
    try:
        from app.nl2sql.scope_parser_subsidence import parse_scope_subsidence

        sub = parse_scope_subsidence(query)
    except Exception:  # noqa: BLE001
        return False
    return bool(sub.district or sub.station_id or sub.station_name)


def _has_time(query: str) -> bool:
    try:
        from app.nl2sql.time_intent_display import extract_time_window_from_question

        win = extract_time_window_from_question(query)
    except Exception:  # noqa: BLE001
        return False
    return bool(win and win[2])


def _has_library_phrase(query: str) -> bool:
    try:
        from app.data_query_agent.library_intent import match_library_ids

        return bool(match_library_ids(query))
    except Exception:  # noqa: BLE001
        return False


def _has_generic_settle(query: str) -> bool:
    try:
        from app.data_query_agent.catalog import get_library_catalog

        words = tuple(get_library_catalog().generic_settle_words or ())
    except Exception:  # noqa: BLE001
        words = ("沉降", "下沉", "回弹", "监测点")
    return _has_any(query, words)


def _has_strong_data_evidence(query: str) -> bool:
    """不含「仅库名」：概念问里带「分层标」不能当成查数。"""
    if _has_any(query, _DATA_VERBS) or _has_any(query, _DATA_METRICS) or _has_any(query, _DATA_GRAIN):
        return True
    if _LOOKUP_RE.search(query):
        return True
    if _has_generic_settle(query):
        return True
    if _has_geo_or_station(query) or _has_time(query):
        return True
    return False


def classify_data_query_kind(query: str) -> QueryKindResult:
    """像查监测表才返回 looks_like_data_query=True。"""
    q = (query or "").strip()
    if not q:
        return QueryKindResult(False, "unspecified")

    strong = _has_strong_data_evidence(q)
    if strong:
        return QueryKindResult(True, "data_query")

    if _is_chitchat(q):
        return QueryKindResult(False, "chitchat")
    if _is_knowledge(q):
        return QueryKindResult(False, "knowledge")

    # 仅点名监测类型（「分层标」）视为查数；「什么是分层标」已在 knowledge 拦截。
    if _has_library_phrase(q):
        return QueryKindResult(True, "data_query")

    return QueryKindResult(False, "unspecified")
