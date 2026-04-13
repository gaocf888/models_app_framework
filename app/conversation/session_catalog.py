"""
会话目录（方案 B）：标题截断、列表分页上限、TITLE_MODE 等环境变量读取。

与 Redis ZSET `conv:index:{user_id}`、Hash `conv:meta:{user_id}:{session_id}` 字段语义对齐。
"""

from __future__ import annotations

import os
import re
from typing import Any


def title_mode() -> str:
    """off | truncate | llm（llm 未实现时与 truncate 行为一致，仅 title_source 仍为 truncated）。"""
    return os.getenv("CHATBOT_SESSION_TITLE_MODE", "truncate").lower().strip()


def title_max_runes() -> int:
    return max(8, int(os.getenv("CHATBOT_SESSION_TITLE_MAX_RUNES", "28")))


def title_edit_max_runes() -> int:
    """用户主动修改标题时的最大码位长度（默认大于首句截断，便于略长备注）。"""
    return max(16, min(200, int(os.getenv("CHATBOT_SESSION_TITLE_EDIT_MAX_RUNES", "64"))))


def normalize_edited_title(text: str) -> str:
    """
    校验并规范化接口传入的标题：去空白折叠、非空、按码位截断并加省略号。
    若去空后无内容，抛出 ValueError。
    """
    t = re.sub(r"\s+", " ", (text or "").strip())
    if not t:
        raise ValueError("title must not be empty")
    max_r = title_edit_max_runes()
    chars = list(t)
    if len(chars) <= max_r:
        return t
    return "".join(chars[:max_r]) + "…"


def session_list_limit_cap() -> int:
    """单用户列表接口默认上限与硬顶。"""
    return max(1, min(200, int(os.getenv("CONV_SESSION_LIST_MAX", "50"))))


def truncate_for_title(text: str, max_runes: int | None = None) -> str:
    """将首条用户提问压缩为列表展示用标题（按 Unicode 码位截断）。"""
    max_runes = max_runes or title_max_runes()
    t = re.sub(r"\s+", " ", (text or "").strip())
    if not t:
        return "新对话"
    chars = list(t)
    if len(chars) <= max_runes:
        return t
    return "".join(chars[:max_runes]) + "…"


def display_title(meta: dict[str, Any], session_id: str) -> str:
    """列表展示：优先 Hash title，其次 first_user_preview 截断，再次 session_id 缩写。"""
    raw = (meta.get("title") or "").strip()
    if raw:
        return raw
    preview = (meta.get("first_user_preview") or "").strip()
    if preview:
        return truncate_for_title(preview)
    return session_id if len(session_id) <= 16 else session_id[:12] + "…"
