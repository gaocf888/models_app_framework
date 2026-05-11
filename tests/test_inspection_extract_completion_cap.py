from __future__ import annotations

from app.services.inspection_extract_llm_orchestrator import (
    cap_completion_max_tokens_for_context,
    _estimate_prompt_tokens_upper_bound,
)


def test_estimate_prefers_dense_when_below_ceiling() -> None:
    # ~36k chars：稠密约 24k+ tokens，稀疏估算偏低时应抬升
    c = 36865
    est = _estimate_prompt_tokens_upper_bound(c, context_total_tokens=32768)
    assert est >= 24500


def test_estimate_ignores_dense_when_would_dominate_context() -> None:
    # ~61k chars：稠密估算会「占满窗口」，退回按 ~2.5 字符/token，避免无谓压死输出预算
    c = 61442
    est = _estimate_prompt_tokens_upper_bound(c, context_total_tokens=32768)
    assert est == (c * 10 + 24) // 25


def test_cap_reduces_parse_budget_near_context_limit() -> None:
    """对齐 vLLM：24577 input + 8192 max 会 400；封顶后应显著低于 8192。"""
    c = 61442
    capped = cap_completion_max_tokens_for_context(
        prompt_chars=c,
        requested_max_tokens=8192,
        context_total_tokens=32768,
        slack_tokens=768,
    )
    assert capped < 8192
    assert capped >= 64
    est = _estimate_prompt_tokens_upper_bound(c, context_total_tokens=32768)
    assert est + capped + 384 + 768 + 768 <= 32768 + 512  # 允许估算误差一档


def test_cap_leaves_headroom_for_small_prompts() -> None:
    capped = cap_completion_max_tokens_for_context(
        prompt_chars=2000,
        requested_max_tokens=8192,
        context_total_tokens=32768,
        slack_tokens=768,
    )
    assert capped == 8192
