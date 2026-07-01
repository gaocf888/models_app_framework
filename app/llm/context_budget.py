from __future__ import annotations

from typing import Any

from app.core.logging import get_logger

logger = get_logger(__name__)

MIN_COMPLETION_TOKENS = 64
# chat 模板对 role / 特殊 token 的额外字符（启发式计入预算，略放大以免低估）
CHAT_MESSAGES_SLO_CHARS = 64


def estimate_prompt_tokens_upper_bound(prompt_chars: int, *, context_total_tokens: int) -> int:
    """
    输入 prompt 的 token 数上界（启发式，偏保守）。

    与检修提取、智能客服历史裁剪共用，避免 input + max_tokens 超出 vLLM 上下文。
    """
    c = max(0, prompt_chars)
    ctx = max(2048, int(context_total_tokens))
    est_sparse = max(1, (c * 11 + 21) // 22)
    est_mixed = max(1, (c * 10 + 24) // 25)
    est_dense = max(1, (c * 2 + 2) // 3)
    base = max(est_sparse, est_mixed)
    density_ceiling = max(4096, (ctx * 85) // 100)
    if est_dense < density_ceiling:
        base = max(base, est_dense)
    ratio_ceiling = max(2048, ctx - 2048)
    est_ratio_high = min(max(1, (c * 20 + 26) // 27), ratio_ceiling)
    return max(base, est_ratio_high)


def cap_completion_max_tokens_for_context(
    *,
    prompt_chars: int,
    requested_max_tokens: int,
    context_total_tokens: int,
    slack_tokens: int,
) -> int:
    """保证真实 input_tokens + capped 大概率不超过 context（启发式上界）。"""
    ctx = max(2048, int(context_total_tokens))
    req = max(MIN_COMPLETION_TOKENS, int(requested_max_tokens))
    slack_cfg = max(64, int(slack_tokens))

    est = estimate_prompt_tokens_upper_bound(prompt_chars, context_total_tokens=ctx)
    # 模板/特殊 token、与服务端计数偏差；+128 与 fencepost 减轻「差 1 token」400
    reserve = max(slack_cfg, 512) + 384 + 128
    room = ctx - est - reserve - 1
    if room < MIN_COMPLETION_TOKENS:
        logger.warning(
            "context_budget completion exhausted prompt_chars=%s est=%s reserve=%s ctx=%s",
            prompt_chars,
            est,
            reserve,
            ctx,
        )
        capped = MIN_COMPLETION_TOKENS
    else:
        capped = min(req, room)

    out = max(MIN_COMPLETION_TOKENS, capped)
    if out < req:
        logger.info(
            "context_budget capped max_tokens requested=%s capped=%s prompt_chars=%s ctx=%s est=%s reserve=%s",
            req,
            out,
            prompt_chars,
            ctx,
            est,
            reserve,
        )
    return out


def prompt_within_context_budget(
    *,
    prompt_chars: int,
    requested_max_tokens: int,
    context_total_tokens: int,
    slack_tokens: int,
) -> bool:
    """启发式判断：当前 prompt + 请求的 max_tokens 是否在上下文窗口内。"""
    est = estimate_prompt_tokens_upper_bound(
        prompt_chars, context_total_tokens=context_total_tokens
    )
    slack_cfg = max(64, int(slack_tokens))
    reserve = max(slack_cfg, 512) + 384 + 128
    return est + max(MIN_COMPLETION_TOKENS, int(requested_max_tokens)) + reserve + 1 <= int(
        context_total_tokens
    )


def llm_message_content_chars(content: Any) -> int:
    if isinstance(content, str):
        return len(content)
    if isinstance(content, list):
        total = 0
        for block in content:
            if not isinstance(block, dict):
                total += len(str(block))
                continue
            if block.get("type") == "text":
                total += len(str(block.get("text") or ""))
            elif block.get("type") == "image_url":
                # 多模态图片 token 难精确估计；预留固定字符量避免低估
                total += 512
        return total
    return len(str(content or ""))


def estimate_llm_messages_chars(messages: list[dict[str, Any]]) -> int:
    total = CHAT_MESSAGES_SLO_CHARS
    for m in messages:
        total += len(str(m.get("role") or "")) + 4
        total += llm_message_content_chars(m.get("content"))
    return total
