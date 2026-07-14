from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from typing import Any, Callable, List

from app.core.logging import get_logger
from app.llm.context_budget import (
    MIN_COMPLETION_TOKENS,
    cap_completion_max_tokens_for_context,
    estimate_llm_messages_chars,
    estimate_llm_messages_prompt_tokens,
    prompt_within_context_budget,
    truncate_largest_message_content,
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
    rag_snippets_total: int = 0
    rag_snippets_kept: int = 0
    rag_snippets_dropped: int = 0


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
    build_from_history: Callable[[list[dict[str, Any]]], list[dict[str, Any]]] | None = None,
    context_total_tokens: int,
    requested_max_tokens: int,
    slack_tokens: int,
    trim_enabled: bool = True,
    min_keep: int = 0,
    rag_snippets: list[str] | None = None,
    build_messages: Callable[[list[dict[str, Any]], list[str]], list[dict[str, Any]]] | None = None,
) -> ChatbotLlmBuildResult:
    """
    动态裁剪至落入上下文预算，顺序固定为：

    1. **先**裁剪历史会话（从最旧消息开始丢弃）；
    2. **再**裁剪 RAG：按检索排序从后往前整段丢弃低相关片段；
    3. 仍超窗则截断最长 system 正文；
    4. 最后按中文安全 token 上界压低 ``max_tokens``。

    ``build_messages(hist, snippets)`` 与 ``rag_snippets`` 同时提供时启用 RAG 裁剪；
    否则仅使用 ``build_from_history(hist)``（兼容旧调用方）。
    """
    total = len(history)
    min_keep = max(0, int(min_keep))
    ctx = max(2048, int(context_total_tokens))
    req_max = max(64, int(requested_max_tokens))
    slack = max(64, int(slack_tokens))

    use_rag_trim = build_messages is not None and rag_snippets is not None
    snippets_working = [s for s in (rag_snippets or []) if str(s).strip()] if use_rag_trim else []
    snippets_total = len(snippets_working)

    def _build(hist: list[dict[str, Any]], snippets: list[str]) -> list[dict[str, Any]]:
        if use_rag_trim:
            assert build_messages is not None
            return build_messages(hist, snippets)
        assert build_from_history is not None
        return build_from_history(hist)

    def _within(messages: list[dict[str, Any]], *, max_tokens: int) -> bool:
        prompt_chars = estimate_llm_messages_chars(messages)
        est = estimate_llm_messages_prompt_tokens(messages, context_total_tokens=ctx)
        return prompt_within_context_budget(
            prompt_chars=prompt_chars,
            requested_max_tokens=max_tokens,
            context_total_tokens=ctx,
            slack_tokens=slack,
            estimated_prompt_tokens=est,
        )

    working = list(history)
    dropped = 0

    # 1) 优先裁剪历史
    if trim_enabled:
        while len(working) > min_keep:
            messages = _build(working, snippets_working)
            if _within(messages, max_tokens=req_max):
                break
            working = working[1:]
            dropped += 1

    # 2) 历史仍不够时，从排序靠后的 RAG 片段整段丢弃
    rag_dropped = 0
    if use_rag_trim:
        while len(snippets_working) > 0:
            messages = _build(working, snippets_working)
            if _within(messages, max_tokens=req_max):
                break
            snippets_working = snippets_working[:-1]
            rag_dropped += 1

    messages = deepcopy(_build(working, snippets_working))

    # 3) 仍超窗（单条超长片段 / 巨大 system）：截断最长 system 正文
    content_trim_rounds = 0
    while not _within(messages, max_tokens=req_max) and content_trim_rounds < 64:
        if not truncate_largest_message_content(messages, cut_chars=4000):
            break
        content_trim_rounds += 1

    # 若连最小 completion 也装不下，继续截至 min completion 可过
    while (
        not _within(messages, max_tokens=MIN_COMPLETION_TOKENS)
        and content_trim_rounds < 96
    ):
        if not truncate_largest_message_content(messages, cut_chars=4000):
            break
        content_trim_rounds += 1

    prompt_chars = estimate_llm_messages_chars(messages)
    est_tokens = estimate_llm_messages_prompt_tokens(messages, context_total_tokens=ctx)
    capped_max = cap_completion_max_tokens_for_context(
        prompt_chars=prompt_chars,
        requested_max_tokens=req_max,
        context_total_tokens=ctx,
        slack_tokens=slack,
        estimated_prompt_tokens=est_tokens,
    )

    # 始终打日志，便于确认新代码已加载、以及本轮是否裁剪
    logger.info(
        "chatbot.prompt_budget history_dropped=%s history_kept=%s history_total=%s "
        "rag_dropped=%s rag_kept=%s rag_total=%s content_trim_rounds=%s "
        "prompt_chars=%s est_tokens=%s max_tokens=%s ctx=%s",
        dropped,
        len(working),
        total,
        rag_dropped,
        len(snippets_working),
        snippets_total,
        content_trim_rounds,
        prompt_chars,
        est_tokens,
        capped_max,
        ctx,
    )

    return ChatbotLlmBuildResult(
        messages=messages,
        max_tokens=capped_max,
        history_total=total,
        history_kept=len(working),
        history_dropped=dropped,
        rag_snippets_total=snippets_total,
        rag_snippets_kept=len(snippets_working),
        rag_snippets_dropped=rag_dropped,
    )
