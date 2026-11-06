"""llm_section 的 static_body 与叙述正文组合（{{NARRATIVE}} 书签）。"""

from __future__ import annotations

NARRATIVE_MARKER = "{{NARRATIVE}}"

_SENTENCE_END = ("。", "！", "？", ".", "!", "?", "；", ";")


def narrative_post_inline(static_body: str) -> bool:
    """``{{NARRATIVE}}`` 后紧接正文（无换行）时，叙述与后缀同段拼接。"""
    body = static_body or ""
    if NARRATIVE_MARKER not in body:
        return False
    _pre, _sep, rest = body.partition(NARRATIVE_MARKER)
    return bool(rest) and not rest.startswith("\n")


def split_static_narrative(static_body: str) -> tuple[str, str]:
    """
    拆分 static_body。

    - 含 ``{{NARRATIVE}}``：返回 (前缀, 后缀)
    - 无书签但有正文：整段作为前缀（叙述后接）
    - 空：("", "")
    """
    body = (static_body or "").strip()
    if not body:
        return "", ""
    if NARRATIVE_MARKER in body:
        pre, _, post = body.partition(NARRATIVE_MARKER)
        return pre.strip(), post.strip()
    return body, ""


def apply_static_narrative(static_body: str, narrative: str) -> str:
    """将 LLM 叙述嵌入 static_body（前缀 / 叙述 / 后缀）。"""
    pre, post = split_static_narrative(static_body)
    nar = (narrative or "").strip()
    if narrative_post_inline(static_body) and nar and post:
        if not nar.endswith(_SENTENCE_END):
            nar = nar + "。"
        mid = nar + post
        parts = [p for p in (pre, mid) if p]
        return "\n\n".join(parts)
    parts = [p for p in (pre, nar, post) if p]
    return "\n\n".join(parts)
