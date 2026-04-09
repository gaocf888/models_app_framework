"""
user_id / session_id 校验：Redis key 安全、长度与控制字符限制。

可通过环境变量调整；``CONV_ID_STRICT`` 为 false 时仍禁止控制字符与空串，但允许冒号（不推荐生产）。
"""

from __future__ import annotations

import os
import re

_CTRL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


class ConversationIdValidationError(ValueError):
    """非法的 user_id / session_id；由 FastAPI 映射为 HTTP 422。"""


def _user_id_max_len() -> int:
    return max(16, min(256, int(os.getenv("CONV_USER_ID_MAX_LEN", "128"))))


def _session_id_max_len() -> int:
    return max(16, min(512, int(os.getenv("CONV_SESSION_ID_MAX_LEN", "256"))))


def id_strict() -> bool:
    return os.getenv("CONV_ID_STRICT", "true").lower() in ("1", "true", "yes", "on")


def validate_user_id(value: str) -> str:
    if not isinstance(value, str):
        raise ConversationIdValidationError("user_id must be a string")
    s = value.strip()
    if not s:
        raise ConversationIdValidationError("user_id must not be empty")
    mx = _user_id_max_len()
    if len(s) > mx:
        raise ConversationIdValidationError(f"user_id exceeds max length ({mx})")
    if _CTRL.search(s):
        raise ConversationIdValidationError("user_id contains invalid control characters")
    if id_strict() and ":" in s:
        raise ConversationIdValidationError(
            "user_id must not contain ':' (reserved for Redis key layout)"
        )
    return s


def validate_session_id(value: str) -> str:
    if not isinstance(value, str):
        raise ConversationIdValidationError("session_id must be a string")
    s = value.strip()
    if not s:
        raise ConversationIdValidationError("session_id must not be empty")
    mx = _session_id_max_len()
    if len(s) > mx:
        raise ConversationIdValidationError(f"session_id exceeds max length ({mx})")
    if _CTRL.search(s):
        raise ConversationIdValidationError("session_id contains invalid control characters")
    if id_strict() and ":" in s:
        raise ConversationIdValidationError(
            "session_id must not contain ':' (reserved for Redis key layout)"
        )
    return s


def validate_pair(user_id: str, session_id: str) -> tuple[str, str]:
    return validate_user_id(user_id), validate_session_id(session_id)
