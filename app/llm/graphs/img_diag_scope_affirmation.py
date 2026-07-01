"""匹配成功确认环节：识别用户纯肯定反馈 vs 含校正信息的反馈。"""

from __future__ import annotations

import re
from typing import Any

from app.llm.graphs.img_diag_scope_display import normalize_scope_patch_keys
from app.llm.graphs.img_diag_scope_exclusions import detect_scope_field_exclusions_from_text

_AFFIRMATIVE_PHRASES: tuple[str, ...] = (
    "确认上述台账",
    "确认上述",
    "确认以上信息",
    "确认以上",
    "确认无误",
    "确认台账",
    "以上确认",
    "上述无误",
    "准确无误",
    "可以继续",
    "开始分析",
    "开始吧",
    "没问题",
    "没毛病",
    "确认",
    "正确",
    "准确",
    "继续",
    "可以了",
    "就这样",
    "对的",
    "无误",
    "好的",
    "可以",
    "是的",
    "行",
    "好",
    "是",
    "嗯",
    "嗯嗯",
    "成",
    "得",
    "yes",
    "yeah",
    "confirm",
    "correct",
    "ok",
    "OK",
)

_AFFIRMATIVE_PREFIXES: tuple[str, ...] = ("那就", "请", "那", "嗯")
_AFFIRMATIVE_SUFFIXES: tuple[str, ...] = ("了呢", "就好", "了啊", "了", "吧", "的", "啊", "呢", "啦", "哦", "噢", "咯")

_AFFIRMATIVE_SYMBOLS: frozenset[str] = frozenset({"👍", "√", "✓", "✔", "+"})

_CORRECTION_HINTS = re.compile(
    r"(?:应为|应该是|不是|改成|改为|修正|纠正|补充|修改|去除|去掉|不要|错误|不对|实际是|其实是|"
    r"检测位置|测厚位置|受热面|机组|锅炉|排数|管数|第\d+|[\d]+号锅炉|[\d]+号机组)"
)

_FUZZY_AFFIRMATIVE_MAX_EXTRA_CHARS = 2


def _normalize_token(text: str) -> str:
    return re.sub(r"[，,。.!！?？…~\-_\s]+", "", (text or "").strip()).lower()


_AFFIRMATIVE_PHRASES_SORTED: tuple[str, ...] = tuple(
    sorted(_AFFIRMATIVE_PHRASES, key=len, reverse=True)
)

_AFFIRMATIVE_ALLOWED: frozenset[str] = frozenset(
    _normalize_token(p) for p in _AFFIRMATIVE_PHRASES if p
) | frozenset({"ok"})


def _strip_affirmative_decorations(token: str) -> str:
    """剥常见礼貌前缀与语气后缀，便于匹配「请继续」「没问题了」等变体。"""
    t = _normalize_token(token)
    if not t:
        return t
    changed = True
    while changed:
        changed = False
        for prefix in _AFFIRMATIVE_PREFIXES:
            if t.startswith(prefix) and len(t) > len(prefix):
                t = t[len(prefix) :]
                changed = True
                break
        if changed:
            continue
        for suffix in _AFFIRMATIVE_SUFFIXES:
            if t.endswith(suffix) and len(t) > len(suffix):
                t = t[: -len(suffix)]
                changed = True
                break
    return t


def _fuzzy_matches_affirmative(token: str) -> bool:
    """词表项前缀匹配，允许末尾少量语气词（如「确认哈」）。"""
    for phrase in _AFFIRMATIVE_PHRASES_SORTED:
        base = _normalize_token(phrase)
        if not base or not token.startswith(base):
            continue
        extra = token[len(base) :]
        if len(extra) <= _FUZZY_AFFIRMATIVE_MAX_EXTRA_CHARS:
            return True
    return False


def _is_affirmative_token(part: str) -> bool:
    norm = _normalize_token(part)
    if not norm:
        return True
    if norm in _AFFIRMATIVE_SYMBOLS:
        return True
    if norm in _AFFIRMATIVE_ALLOWED:
        return True
    stripped = _strip_affirmative_decorations(norm)
    if stripped in _AFFIRMATIVE_ALLOWED:
        return True
    if _fuzzy_matches_affirmative(norm):
        return True
    if stripped != norm and _fuzzy_matches_affirmative(stripped):
        return True
    return False


def is_affirmative_supplement(text: str) -> bool:
    """口语补充是否仅为肯定/继续，不含校正语义。"""
    raw = (text or "").strip()
    if not raw:
        return True
    if _CORRECTION_HINTS.search(raw):
        return False
    if detect_scope_field_exclusions_from_text(raw):
        return False
    parts = [p.strip() for p in re.split(r"[，,。.!！?？\s]+", raw) if p.strip()]
    if not parts:
        return True
    return all(_is_affirmative_token(part) for part in parts)


def has_scope_correction_patch(patch: dict[str, Any] | None) -> bool:
    if not isinstance(patch, dict) or not patch:
        return False
    normalized = normalize_scope_patch_keys(patch)
    return bool(normalized)


def is_matched_confirm_affirmative_response(action: str, payload: dict[str, Any] | None) -> bool:
    """
    首次「匹配成功确认」后，用户反馈是否可视为纯肯定（可跳过再次解析/校验）。

    - 空 confirm、或仅「确认/继续/正确」等 → True
    - scope_patch、字段排除、校正口语 → False
    """
    if action == "abort":
        return False
    payload = payload or {}
    if has_scope_correction_patch(payload.get("scope_patch")):
        return False
    supplement = str(payload.get("user_supplement") or "").strip()
    return is_affirmative_supplement(supplement)
