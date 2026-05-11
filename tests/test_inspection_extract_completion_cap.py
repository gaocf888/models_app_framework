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


def test_estimate_conservative_when_dense_dominates_window() -> None:
    # ~61k chars：含 ratio 高密度上界（封顶后与 base 取 max）
    c = 61442
    est = _estimate_prompt_tokens_upper_bound(c, context_total_tokens=32768)
    ratio_capped = min((c * 20 + 26) // 27, 32768 - 2048)
    est_sparse = (c * 11 + 21) // 22
    est_mixed = (c * 10 + 24) // 25
    assert est == max(max(est_sparse, est_mixed), ratio_capped)


def test_estimate_covers_dense_short_prompt_regression() -> None:
    """回归：~34k 字符曾≈24577 token（表格偏重），上界须覆盖。"""
    c = 33390
    est = _estimate_prompt_tokens_upper_bound(c, context_total_tokens=32768)
    assert est >= 24577


def test_estimate_covers_qwen_mixed_report_chunk() -> None:
    """回归：58k 字符≈26k token（~2.25 字符/token），上界须覆盖服务端计数。"""
    c = 58270
    est = _estimate_prompt_tokens_upper_bound(c, context_total_tokens=32768)
    assert est >= 25917


def test_cap_reduces_parse_budget_near_context_limit() -> None:
    """对齐 vLLM：input + max_tokens 不得超出上下文；封顶须留出 reserve。"""
    c = 61442
    slack = 768
    capped = cap_completion_max_tokens_for_context(
        prompt_chars=c,
        requested_max_tokens=8192,
        context_total_tokens=32768,
        slack_tokens=slack,
    )
    assert capped < 8192
    assert capped >= 64
    est = _estimate_prompt_tokens_upper_bound(c, context_total_tokens=32768)
    reserve = max(slack, 512) + 384 + 128
    assert est + capped + reserve + 1 <= 32768


def test_cap_leaves_headroom_for_small_prompts() -> None:
    capped = cap_completion_max_tokens_for_context(
        prompt_chars=2000,
        requested_max_tokens=8192,
        context_total_tokens=32768,
        slack_tokens=768,
    )
    assert capped == 8192
