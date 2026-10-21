"""确定性/校验后正文的打字机切分（假流式，不经 LLM）。"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from app.core.config import get_app_config

DeltaCallback = Callable[[str], Awaitable[None] | None]


def typewriter_chunks(text: str, chunk_chars: int | None = None) -> list[str]:
    if not text:
        return []
    cfg = get_app_config().analysis_agent
    size = max(1, int(chunk_chars if chunk_chars is not None else cfg.typewriter_chunk_chars))
    return [text[i : i + size] for i in range(0, len(text), size)]


async def emit_typewriter(
    on_delta: DeltaCallback | None,
    text: str,
    *,
    chunk_chars: int | None = None,
    delay_ms: int | None = None,
) -> None:
    if not on_delta or not text:
        return
    cfg = get_app_config().analysis_agent
    delay = max(0, int(delay_ms if delay_ms is not None else cfg.typewriter_delay_ms))
    for piece in typewriter_chunks(text, chunk_chars):
        maybe = on_delta(piece)
        if maybe is not None and hasattr(maybe, "__await__"):
            await maybe  # type: ignore[misc]
        if delay:
            await asyncio.sleep(delay / 1000.0)
