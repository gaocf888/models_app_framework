from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, List

from app.core.logging import get_logger
from app.llm.context_budget import (
    cap_completion_max_tokens_for_context,
    estimate_llm_messages_chars,
    prompt_within_context_budget,
)
from app.services.chatbot_image_utils import strip_image_block_from_history

logger = get_logger(__name__)


@dataclass(frozen=True)
class ChatbotLlmBuildResult:
    messages: List[dict[str, Any]]
    max_tokens: int
    history_total: int
    history_kept: int
    history_dropped: int


def assemble_chatbot_llm_messages(
    *,
    system_chunks: list[str],
    history: list[dict[str, Any]],
    query: str,
    image_urls: list[str] | None = None,
) -> list[dict[str, Any]]:
    """
    构建 vLLM messages：合并 system -> 历史 user/assistant -> 当前 user（文本或多模态）。

    与 LangGraph ``kb_build_messages`` / legacy ``_build_llm_messages`` 顺序一致。
    """
    messages: list[dict[str, Any]] = []
    merged_system: list[str] = [c for c in system_chunks if isinstance(c, str) and c.strip()]
    for h in history:
        role = (h.get("role", "user") or "user")
        role_l = str(role).lower()
        content = h.get("content", "")
        if isinstance(content, str):
            content = strip_image_block_from_history(content)
        elif content is not None:
            content = str(content)
        else:
            content = ""
        if not content:
            continue
        if role_l == "system":
            merged_system.append(content)
            continue
        messages.append({"role": role_l, "content": content})

    chunks = merged_system
    if chunks:
        messages.insert(0, {"role": "system", "content": "\n\n".join(chunks)})

    urls = [u for u in (image_urls or []) if isinstance(u, str) and u.strip()]
    q = str(query or "")
    if urls:
        blocks: list[dict[str, Any]] = [{"type": "text", "text": q}]
        for u in urls:
            blocks.append({"type": "image_url", "image_url": {"url": u}})
        messages.append({"role": "user", "content": blocks})
    else:
        messages.append({"role": "user", "content": q})
    return messages


def trim_history_and_build_chatbot_messages(
    history: list[dict[str, Any]],
    *,
    build_from_history: Callable[[list[dict[str, Any]]], list[dict[str, Any]]],
    context_total_tokens: int,
    requested_max_tokens: int,
    slack_tokens: int,
    trim_enabled: bool = True,
    min_keep: int = 0,
) -> ChatbotLlmBuildResult:
    """
    动态裁剪历史：从最旧消息开始丢弃，保留最近若干条，直至 prompt 落入上下文预算。

    ``build_from_history`` 由调用方提供，以便 system/RAG/指代锚块与 history 子集一致。
    """
    total = len(history)
    min_keep = max(0, int(min_keep))
    ctx = max(2048, int(context_total_tokens))
    req_max = max(64, int(requested_max_tokens))
    slack = max(64, int(slack_tokens))

    working = list(history)
    dropped = 0

    if trim_enabled:
        while len(working) > min_keep:
            messages = build_from_history(working)
            prompt_chars = estimate_llm_messages_chars(messages)
            if prompt_within_context_budget(
                prompt_chars=prompt_chars,
                requested_max_tokens=req_max,
                context_total_tokens=ctx,
                slack_tokens=slack,
            ):
                break
            working = working[1:]
            dropped += 1

    messages = build_from_history(working)
    prompt_chars = estimate_llm_messages_chars(messages)
    capped_max = cap_completion_max_tokens_for_context(
        prompt_chars=prompt_chars,
        requested_max_tokens=req_max,
        context_total_tokens=ctx,
        slack_tokens=slack,
    )

    if dropped > 0:
        logger.info(
            "chatbot.history_trim dropped=%s kept=%s total=%s prompt_chars=%s max_tokens=%s ctx=%s",
            dropped,
            len(working),
            total,
            prompt_chars,
            capped_max,
            ctx,
        )

    return ChatbotLlmBuildResult(
        messages=messages,
        max_tokens=capped_max,
        history_total=total,
        history_kept=len(working),
        history_dropped=dropped,
    )
