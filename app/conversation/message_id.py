from __future__ import annotations

import hashlib
import re

_HEX64 = re.compile(r"^[0-9a-f]{64}$")


def conversation_ts_to_ms(ts: float | int | None) -> int:
    """与 ``ConversationManager`` 历史去重、冷层 message_id 保持同一套毫秒规则。"""
    if ts is None:
        return 0
    t = float(ts)
    if t > 10_000_000_000:
        return int(t)
    return int(t * 1000)


def build_conversation_message_id(
    user_id: str,
    session_id: str,
    role: str,
    content: str,
    ts: float | int | None,
) -> str:
    """
    会话消息稳定 id：sha256(user_id|session_id|role|ts_ms|content)。

    须与持久化层原始 ``content``、``ts`` 一致（含图片块等），与 ES 冷层文档 _id 对齐。
    """
    ts_ms = conversation_ts_to_ms(ts)
    raw = f"{user_id}|{session_id}|{role}|{ts_ms}|{content}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def is_valid_message_id_hex(message_id: str) -> bool:
    s = (message_id or "").strip().lower()
    return bool(_HEX64.match(s))
