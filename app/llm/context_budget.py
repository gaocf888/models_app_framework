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


def _budget_reserve_tokens(slack_tokens: int) -> int:
    slack_cfg = max(64, int(slack_tokens))
    # 模板/特殊 token、与服务端计数偏差；+128 与 fencepost 减轻「差 1 token」400
    return max(slack_cfg, 512) + 384 + 128


def count_cjk_chars(text: str) -> int:
    """统计 CJK 统一汉字与常见全角标点（中文文档 token≈字）。"""
    n = 0
    for ch in text:
        o = ord(ch)
        if (
            0x4E00 <= o <= 0x9FFF
            or 0x3400 <= o <= 0x4DBF
            or 0xF900 <= o <= 0xFAFF
            or 0x3000 <= o <= 0x303F
            or 0xFF00 <= o <= 0xFFEF
        ):
            n += 1
    return n


def estimate_text_tokens_upper_bound(text: str, *, context_total_tokens: int) -> int:
    """
    基于正文的 token 上界（智能客服 / 中文 RAG 偏保守）。

    - 汉字按 ≈1.1 token/字；
    - 全体长度再按 1.12× 字符数作硬上界（覆盖表格、编号、chat 模板低估）；
    - 与原有西欧密度估算取 max。
    """
    raw = text or ""
    c = len(raw)
    if c <= 0:
        return 1
    char_based = estimate_prompt_tokens_upper_bound(c, context_total_tokens=context_total_tokens)
    cjk = count_cjk_chars(raw)
    other = max(0, c - cjk)
    content_est = max(1, (cjk * 110 + 99) // 100 + (other * 10 + 19) // 20)
    # 字符硬上界：避免「估 ~36k、真实 38913」仍放过 max_tokens=2048
    char_floor = max(1, (c * 112 + 99) // 100)
    template_pad = 384
    return max(char_based, content_est + template_pad, char_floor + template_pad)


def cap_completion_max_tokens_for_context(
    *,
    prompt_chars: int,
    requested_max_tokens: int,
    context_total_tokens: int,
    slack_tokens: int,
    estimated_prompt_tokens: int | None = None,
) -> int:
    """保证真实 input_tokens + capped 大概率不超过 context（启发式上界）。"""
    ctx = max(2048, int(context_total_tokens))
    req = max(MIN_COMPLETION_TOKENS, int(requested_max_tokens))
    reserve = _budget_reserve_tokens(slack_tokens)

    if estimated_prompt_tokens is not None:
        est = max(1, int(estimated_prompt_tokens))
    else:
        est = estimate_prompt_tokens_upper_bound(prompt_chars, context_total_tokens=ctx)
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
    estimated_prompt_tokens: int | None = None,
) -> bool:
    """启发式判断：当前 prompt + 请求的 max_tokens 是否在上下文窗口内。"""
    if estimated_prompt_tokens is not None:
        est = max(1, int(estimated_prompt_tokens))
    else:
        est = estimate_prompt_tokens_upper_bound(
            prompt_chars, context_total_tokens=context_total_tokens
        )
    reserve = _budget_reserve_tokens(slack_tokens)
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


def llm_message_content_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if not isinstance(block, dict):
                parts.append(str(block))
                continue
            if block.get("type") == "text":
                parts.append(str(block.get("text") or ""))
            elif block.get("type") == "image_url":
                parts.append("[image]")
        return "\n".join(parts)
    return str(content or "")


def flatten_llm_messages_text(messages: list[dict[str, Any]]) -> str:
    parts: list[str] = []
    for m in messages:
        parts.append(str(m.get("role") or ""))
        parts.append(llm_message_content_text(m.get("content")))
    return "\n".join(parts)


def estimate_llm_messages_prompt_tokens(
    messages: list[dict[str, Any]],
    *,
    context_total_tokens: int,
) -> int:
    """Chat messages 的 token 上界（含中文密度，供智能客服预算使用）。"""
    text_est = estimate_text_tokens_upper_bound(
        flatten_llm_messages_text(messages),
        context_total_tokens=context_total_tokens,
    )
    # 再与 messages 字符统计对齐，防止 flatten 与计长路径偏差
    chars = estimate_llm_messages_chars(messages)
    return max(text_est, (chars * 112 + 99) // 100)


def ensure_chatbot_stream_max_tokens(
    messages: list[dict[str, Any]],
    *,
    requested_max_tokens: int,
    context_total_tokens: int,
    slack_tokens: int,
) -> int:
    """
    流式调用前最后一道闸：按保守 est 压低 max_tokens，避免
    input_tokens + max_tokens > context 的 vLLM 400。
    """
    return cap_completion_max_tokens_for_context(
        prompt_chars=estimate_llm_messages_chars(messages),
        requested_max_tokens=requested_max_tokens,
        context_total_tokens=context_total_tokens,
        slack_tokens=slack_tokens,
        estimated_prompt_tokens=estimate_llm_messages_prompt_tokens(
            messages, context_total_tokens=context_total_tokens
        ),
    )


def truncate_largest_message_content(
    messages: list[dict[str, Any]],
    *,
    cut_chars: int = 4000,
) -> bool:
    """优先截断最长 system 正文尾部，用于「只剩 1 条超长 RAG 仍超窗」的兜底。"""
    best_i = -1
    best_len = 0
    prefer_system = -1
    prefer_len = 0
    for i, m in enumerate(messages):
        n = llm_message_content_chars(m.get("content"))
        if n > best_len:
            best_len = n
            best_i = i
        if str(m.get("role") or "").lower() == "system" and n > prefer_len:
            prefer_len = n
            prefer_system = i
    target = prefer_system if prefer_system >= 0 and prefer_len >= 512 else best_i
    if target < 0 or best_len < 512:
        return False
    msg = messages[target]
    content = msg.get("content")
    if isinstance(content, str):
        if len(content) <= cut_chars + 128:
            keep = max(256, len(content) // 2)
            if keep >= len(content):
                return False
            msg["content"] = content[:keep] + "\n…(已截断)"
            return True
        msg["content"] = content[: len(content) - cut_chars] + "\n…(已截断)"
        return True
    return False
