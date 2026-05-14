"""
§4.2 对话锚块（Dialogue Anchor Block）：从最近 assistant 抽取要点，供 system 注入。
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Sequence

from app.llm.graphs.chatbot_anaphora_config import AnaphoraRuntimeConfig, get_anaphora_runtime_config
from app.llm.graphs.chatbot_anaphora_types import AnaphoraType
from app.services.chatbot_image_utils import strip_image_block_from_history

_BULLET_LINE_PATTERNS = (
    re.compile(r"^\s*\d+[\.\、]\s*\S"),
    re.compile(r"^\s*[-*]\s+\S"),
)


def extract_bullets_from_assistant_text(
    text: str,
    *,
    max_items: int = 6,
    max_first_len: int = 400,
    min_line_chars: int = 8,
) -> list[str]:
    """
    v0 规则抽取：按空行/换行/编号/列表切分，过滤过短行，最多 max_items 条。
    """
    raw = strip_image_block_from_history(text or "").strip()
    if not raw:
        return []
    parts = re.split(r"\n\s*\n+", raw)
    candidates: list[str] = []
    for chunk in parts:
        for line in re.split(r"\n+", chunk):
            line = line.strip()
            if len(line) < min_line_chars:
                continue
            candidates.append(line)
    merged: list[str] = []
    seen: set[str] = set()
    for line in candidates:
        if line in seen:
            continue
        seen.add(line)
        merged.append(line)
    out: list[str] = []
    for line in merged:
        if len(out) >= max_items:
            break
        if any(p.match(line) for p in _BULLET_LINE_PATTERNS) or len(line) >= min_line_chars + 4:
            show = line[:max_first_len] + ("…" if len(line) > max_first_len else "")
            out.append(show)
    if not out and raw:
        show = raw[:max_first_len] + ("…" if len(raw) > max_first_len else "")
        out = [show]
    return out[:max_items]


def _last_assistant_text(history_messages: List[Dict[str, Any]]) -> str:
    for m in reversed(history_messages or []):
        if str(m.get("role", "") or "").lower() != "assistant":
            continue
        c = m.get("content", "")
        t = c if isinstance(c, str) else str(c or "")
        p = strip_image_block_from_history(t).strip()
        if p:
            return p
    return ""


def _p1_enabled_for(row_code: str, arc: AnaphoraRuntimeConfig) -> bool:
    row = arc.types.get(row_code)
    return bool(row and row.p1_anchor_block)


def build_dialogue_anchor_block(
    history_messages: List[Dict[str, Any]],
    query: str,
    anaphora_type: str,
    *,
    config_path: str | None = None,
    max_chars: int = 1200,
    slot_bullets: Sequence[str] | None = None,
) -> str | None:
    """
    若 §3.2 P1 列为是且配置开启，则返回锚块文本；否则 None。
    slot_bullets：P2 槽位优先于对历史的再解析。
    """
    _ = (query or "").strip()
    at = (anaphora_type or AnaphoraType.NONE.value).strip()
    if at == AnaphoraType.NONE.value or not history_messages:
        return None
    arc = get_anaphora_runtime_config(config_path)
    if not _p1_enabled_for(at, arc):
        return None
    bullets: list[str] = []
    if slot_bullets:
        bullets = [str(b).strip() for b in slot_bullets if str(b).strip()][:8]
    if not bullets:
        a_text = _last_assistant_text(history_messages)
        bullets = extract_bullets_from_assistant_text(a_text, max_items=6)
    if not bullets:
        return None
    lines = [f"{i + 1}. {b}" for i, b in enumerate(bullets)]
    body = "\n".join(lines)
    intro = (
        "【对话锚·仅供推理】上一轮助手回复中可对照的要点（自动抽取）：\n"
        f"{body}\n"
        "若与本轮用户语义不符，以用户明确命名实体为准。\n"
    )
    if at == AnaphoraType.PAIR_COMPARE.value:
        tail = (
            "本轮用户问题中的对比/区别类指代，默认绑定以上最近两条要点（若仅识别到一条，"
            "则仅就该条作答并说明另一项未在对话中明确列出）。\n"
        )
    elif at == AnaphoraType.ORDINAL.value:
        tail = "序位类指代（第N点/前者/后者）请对齐上述编号与上一轮助手原文语义。\n"
    elif at == AnaphoraType.META_CONFIRM.value:
        tail = "元话语确认/质疑：请先简要复述上一轮助手的主要结论与依据，再作答。\n"
    elif at == AnaphoraType.SINGLE_ENTITY.value:
        tail = "单实体回指：默认绑定上述最近一条中的核心实体或现象描述。\n"
    elif at in (AnaphoraType.ELLIPSIS.value, AnaphoraType.CONTINUATION.value):
        tail = "省略或续写：默认延续上述主题与结论边界，避免要求用户重复已给出的关键信息。\n"
    else:
        tail = ""
    block = intro + tail
    if len(block) > max_chars:
        block = block[: max_chars - 1] + "…"
    return block
