"""
智能客服：指代类短句的检索 query 增强（与会话历史尾部融合，§4.1 P0）。

与 `build_retrieval_query_with_anaphora` / `format_rag_snippets_system_block` /
`format_empty_rag_system_block` 对齐 LangGraph 与 Legacy。
"""

from __future__ import annotations

import re
from typing import Any, Dict, List

from app.llm.graphs.chatbot_anaphora_config import get_anaphora_runtime_config
from app.llm.graphs.chatbot_anaphora_detect import AnaphoraRuleResult, classify_anaphora_rules, should_fuse_retrieval_for_type
from app.llm.graphs.chatbot_anaphora_types import AnaphoraType
from app.services.chatbot_image_utils import strip_image_block_from_history

_MAX_USER_TAIL = 520
_MAX_ASSISTANT_TAIL = 900
_DEFAULT_TOTAL_RAG_QUERY = 2800


def is_confirmation_short_query(query: str) -> bool:
    """
    极短追问 / 元话语确认：命中则尝试用「上轮 user + assistant」增强检索 query。
    （保留兼容单测与旧日志口径；与规则层 meta_confirm 判定大体一致。）
    """
    q = (query or "").strip().replace(" ", "").replace("\n", "")
    if len(q) > 36:
        return False
    if len(q) <= 2:
        return False
    patterns = (
        "确定吗",
        "您确定吗",
        "你确定吗",
        "确定么",
        "确定嘛",
        "真的吗",
        "真吗",
        "靠谱吗",
        "有依据吗",
        "对吗",
        "对不对",
        "是不是",
        "肯定吗",
        "没错吧",
        "是吧",
    )
    if any(p in q for p in patterns):
        return True
    if re.search(r"^(你|您)?确定[吗嘛么]?[?？]?$", query.strip()):
        return True
    if len(q) <= 10 and "确定" in q:
        return True
    return False


def _last_user_and_assistant_texts(history_messages: List[Dict[str, Any]]) -> tuple[str, str]:
    """从会话尾部取最近一条 assistant 与最近一条 user 的纯文本（去图片块）。"""
    last_u, last_a = "", ""
    for m in reversed(history_messages or []):
        role = str(m.get("role", "") or "").lower()
        raw = m.get("content", "")
        text = raw if isinstance(raw, str) else str(raw or "")
        plain = strip_image_block_from_history(text).strip()
        if not plain:
            continue
        if role == "assistant" and not last_a:
            last_a = plain
        elif role == "user" and not last_u:
            last_u = plain
        if last_u and last_a:
            break
    return last_u, last_a


def build_retrieval_query_with_anaphora(
    query: str,
    history_messages: List[Dict[str, Any]] | None,
    *,
    enable_context: bool = True,
    fusion_enabled: bool = True,
    fusion_max_chars: int | None = None,
    config_path: str | None = None,
    anaphora_type: str | None = None,
    rule_result: AnaphoraRuleResult | None = None,
) -> tuple[str, AnaphoraRuleResult, str]:
    """
    :return: (rag_query, rule_result, effective_anaphora_type 用于融合与日志)
    effective 优先使用显式传入的 anaphora_type（如 P3 回写）。
    """
    q = (query or "").strip()
    hist = list(history_messages or [])
    rr = rule_result or classify_anaphora_rules(q, hist, enable_context=enable_context, config_path=config_path)
    eff = (anaphora_type or rr.anaphora_type or AnaphoraType.NONE.value).strip()
    arc = get_anaphora_runtime_config(config_path)
    cap = int(fusion_max_chars) if fusion_max_chars is not None else _DEFAULT_TOTAL_RAG_QUERY
    cap = max(500, cap)

    if not fusion_enabled or not enable_context or not hist:
        return q, rr, eff
    if eff == AnaphoraType.NONE.value or not should_fuse_retrieval_for_type(eff, arc):
        return q, rr, eff

    u_tail, a_tail = _last_user_and_assistant_texts(hist)
    if not u_tail and not a_tail:
        return q, rr, eff
    u_show = u_tail[:_MAX_USER_TAIL] if u_tail else "（无文本）"
    a_show = a_tail[:_MAX_ASSISTANT_TAIL] if a_tail else "（无文本）"
    fused = (
        "【检索会话衔接】以下摘取自上一轮对话，用于召回与本轮短追问相关的知识，非用户本轮原话全文。\n"
        f"【指代类型】{eff}\n"
        f"上轮用户：{u_show}\n"
        f"上轮助手：{a_show}\n"
        f"【本轮用户原话】{q}"
    )
    if len(fused) > cap:
        fused = fused[:cap]
    return fused, rr, eff


def build_retrieval_query_for_chatbot(
    query: str,
    history_messages: List[Dict[str, Any]] | None,
    **kwargs: Any,
) -> str:
    """
    默认返回原 query；§3.2 中 P0 为「是」的类型且历史非空时，拼接「上轮 user + assistant」摘要供向量/混合检索。

    接受可选关键字参数，与 `build_retrieval_query_with_anaphora` 对齐（供 Legacy / 图内统一调用）。
    """
    rag_q, _, _ = build_retrieval_query_with_anaphora(query, history_messages, **kwargs)
    return rag_q


def _snippets_use_numbered_ref_format(context_snippets: List[str]) -> bool:
    """是否已为 ``[n] 《文档名》`` 编号块（与 ``chunks_to_numbered_llm_snippets`` 输出一致）。"""
    for s in context_snippets or []:
        t = str(s).strip()
        if not t:
            continue
        return t.startswith("[") and "]" in t[:8]
    return False


def format_empty_rag_system_block() -> str:
    """
    无召回时注入的 system 补句：用领域通用能力作答，禁止检索失败话术。

    与 `ChatbotLangGraphRunner._node_kb_build_messages` / legacy `_build_llm_messages` 对齐。
    """
    return (
        "【本轮知识片段】系统未向你注入检索知识片段。"
        "请基于火电厂燃煤锅炉与四管（水冷壁、过热器、再热器、省煤器）领域的通用专业能力作答："
        "先给出结论要点，再补充简要机理与现场排查/注意。"
        "须在适当位置标明「通用经验，非本厂知识库条文」。"
        "禁止输出「没有检索到」「知识库本轮暂无直接条文」「未检索到相关条文」「无知识片段」「请完善知识库」等检索元话语或拒答。"
        "禁止编造本厂具体运行参数、未给出的测点数值、不存在的标准条款号或文件名。"
    )


def format_rag_snippets_system_block(context_snippets: List[str]) -> str:
    """
    与 `ChatbotLangGraphRunner._node_kb_build_messages` / legacy `_build_llm_messages` 对齐的
    「知识片段」system 段全文（含对「确定吗」类短句的硬性说明）。

    无片段时返回 ``format_empty_rag_system_block``，避免模型沿用「未检索到」话术。
    """
    blocks = [str(c).strip() for c in (context_snippets or []) if str(c).strip()]
    if not blocks:
        return format_empty_rag_system_block()
    numbered = _snippets_use_numbered_ref_format(blocks)
    if numbered:
        ctx = "\n\n".join(blocks)
        intro = (
            "以下为带编号的检索知识片段（方括号内数字 n 与片段标题 ``[n]`` 一一对应；"
            "列表顺序仅为检索结果顺序，与用户所指会话中的小节、主题或「第N点」均无对应关系，"
            "禁止用片段顺序顶替会话内容）。"
        )
        cite_rule = (
            "【片段引用标注】凡直接复述或改写片段中的现象、数据、条文、处置步骤，且与某一 [n] 片段内容一一对应时，"
            "须在相关句子或条目末标注 [n]（不同条目可标不同编号）。"
            "纯综合归纳、过渡句、通用安全提醒、标明为「分析/通用经验」的补充可不标；"
            "但不得将未标注 [n] 的表述写成片段原文或规程条文。"
            "禁止编造未列出的文档名或 http 链接；链接由系统另行提供，正文中只需标注 [n]。"
        )
        granularity_rule = (
            "【宽泛问题作答粒度】若用户问题较宽（如「常见…有哪些」「概述」「列举」「分别说明」等），且下列有多条编号片段："
            "须先按下列片段逐条覆盖已被使用的要点——每条被使用的片段尽量单独成节（标题可取该片段主题或《文档名》），"
            "节内来自该片段的现象/原因/措施句末标对应 [n]；"
            "覆盖片段要点之后，允许基于锅炉/四管通用能力做简短归纳、机理或排查补充，但须标明为「分析/通用经验」，"
            "不得把补充内容写成库内条文，禁止编造未列出的文档名或标准条款号；"
            "禁止输出「知识库本轮未检索到相关条文」「没有检索到」「无知识片段」等检索元话语。"
            "若用户问题较窄且只命中单条片段，针对该问作答并标好 [n]，并可补简短分析。"
        )
    else:
        ctx = "\n".join(f"- {c}" for c in blocks)
        intro = (
            "以下为检索得到的知识片段（列表顺序仅为检索结果顺序，与用户所指会话中的小节、主题或「第N点」"
            "均无对应关系，禁止用片段顺序顶替会话内容）。"
            "以片段为事实锚点，允许在其上做简短分析并标明「分析/通用经验」。"
        )
        cite_rule = ""
        granularity_rule = ""
    common = (
        "用户泛指上文（如「上述现场排查/检修建议」）时，"
        "请先在对话历史中按语义对齐助手较近一轮的相关段落再展开；仅当用户明确说「第N点/条」时再对齐编号。"
        "再以片段补充条文或机理，并可在片段之上做简短分析。"
        "当用户本轮仅为「确定吗」「真的吗」「靠谱吗」「有依据吗」「你确定吗」「您确定吗」等极短追问时，"
        "须先复述并回应对话历史中**紧邻的上一轮** assistant 的主要结论与依据，再引用下述片段作补充；"
        "不得以「请明确指代」「没有具体上下文」「请说明指什么」等话术敷衍回避。"
    )
    parts = [intro, cite_rule, granularity_rule if numbered else "", common, ctx]
    return "\n".join(p for p in parts if p)
