from __future__ import annotations

from app.llm.context_budget import (
    estimate_llm_messages_chars,
    estimate_llm_messages_prompt_tokens,
    estimate_text_tokens_upper_bound,
    prompt_within_context_budget,
)
from app.llm.graphs.chatbot_llm_messages import (
    assemble_chatbot_llm_messages,
    trim_history_and_build_chatbot_messages,
)


def _synthesis_msg(n: int, *, chars: int = 6000) -> dict:
    return {
        "role": "assistant",
        "content": f"[synthesis-{n}] " + ("报" * chars),
    }


def test_trim_drops_oldest_history_until_within_budget() -> None:
    history = []
    for i in range(8):
        history.append({"role": "user", "content": f"analysis-query-{i}"})
        history.append(_synthesis_msg(i, chars=18000))

    def build_from_history(hist: list[dict]) -> list[dict]:
        msgs = [{"role": "system", "content": "system " * 200}]
        for m in hist:
            msgs.append({"role": m["role"], "content": m["content"]})
        msgs.append({"role": "user", "content": "abnormal-levels"})
        return msgs

    result = trim_history_and_build_chatbot_messages(
        history,
        build_from_history=build_from_history,
        context_total_tokens=40960,
        requested_max_tokens=2048,
        slack_tokens=768,
        trim_enabled=True,
        min_keep=0,
    )

    assert result.history_dropped > 0
    assert result.history_kept < len(history)
    assert result.history_kept + result.history_dropped == len(history)
    prompt_chars = estimate_llm_messages_chars(result.messages)
    est = estimate_llm_messages_prompt_tokens(result.messages, context_total_tokens=40960)
    assert prompt_within_context_budget(
        prompt_chars=prompt_chars,
        requested_max_tokens=2048,
        context_total_tokens=40960,
        slack_tokens=768,
        estimated_prompt_tokens=est,
    )


def test_trim_keeps_recent_messages() -> None:
    # 旧轮极大、新轮中等：应先丢掉旧轮并尽量保留较新一轮
    history = [
        {"role": "user", "content": "old-user"},
        {"role": "assistant", "content": "old-assistant " + ("史" * 50000)},
        {"role": "user", "content": "new-user"},
        {"role": "assistant", "content": "new-assistant " + ("新" * 8000)},
    ]

    def build_from_history(hist: list[dict]) -> list[dict]:
        msgs = [{"role": "system", "content": "sys"}]
        for m in hist:
            msgs.append({"role": m["role"], "content": m["content"]})
        msgs.append({"role": "user", "content": "q"})
        return msgs

    result = trim_history_and_build_chatbot_messages(
        history,
        build_from_history=build_from_history,
        context_total_tokens=40960,
        requested_max_tokens=2048,
        slack_tokens=768,
        trim_enabled=True,
        min_keep=0,
    )

    kept_text = " ".join(
        str(m.get("content") or "")
        for m in result.messages
        if m.get("role") in ("user", "assistant")
    )
    assert result.history_dropped > 0
    assert "old-assistant" not in kept_text
    assert "new-assistant" in kept_text or "new-user" in kept_text


def test_trim_disabled_keeps_full_history() -> None:
    history = [{"role": "user", "content": "u1"}, {"role": "assistant", "content": "a1"}]

    def build_from_history(hist: list[dict]) -> list[dict]:
        return [{"role": "user", "content": str(len(hist))}]

    result = trim_history_and_build_chatbot_messages(
        history,
        build_from_history=build_from_history,
        context_total_tokens=40960,
        requested_max_tokens=2048,
        slack_tokens=768,
        trim_enabled=False,
        min_keep=0,
    )

    assert result.history_dropped == 0
    assert result.history_kept == 2


def test_cjk_text_estimate_exceeds_char_only_for_han() -> None:
    # ~38k 汉字：中文上界应 >= 字符数硬地板，避免低估后仍要 2048 completion
    text = "水" * 38000
    est = estimate_text_tokens_upper_bound(text, context_total_tokens=40960)
    assert est >= 38000
    # 对接近满窗的 prompt，应判定装不下 2048 输出
    assert not prompt_within_context_budget(
        prompt_chars=len(text),
        requested_max_tokens=2048,
        context_total_tokens=40960,
        slack_tokens=768,
        estimated_prompt_tokens=est,
    )


def test_rag_trim_drops_trailing_snippets_after_history() -> None:
    """先裁历史，仍超窗时再从 RAG 列表尾部整段丢弃。"""
    history = [
        {"role": "user", "content": "旧问"},
        {"role": "assistant", "content": "旧答" + ("史" * 12000)},
    ]
    # 靠前高相关短，靠后低相关极长
    snippets = [
        "[1] 高相关短片段：" + ("前" * 200),
        "[2] 中等相关：" + ("中" * 200),
        "[3] 低相关超长：" + ("后" * 45000),
    ]

    def build_messages(hist: list[dict], snips: list[str]) -> list[dict]:
        return assemble_chatbot_llm_messages(
            system_chunks=["你是助手。", "\n\n".join(snips)],
            history=hist,
            query="描述本厂水冷壁管排信息",
        )

    result = trim_history_and_build_chatbot_messages(
        history,
        build_messages=build_messages,
        rag_snippets=snippets,
        context_total_tokens=40960,
        requested_max_tokens=2048,
        slack_tokens=768,
        trim_enabled=True,
        min_keep=0,
    )

    assert result.history_dropped >= 1  # 先裁历史
    assert result.rag_snippets_dropped >= 1  # 再裁靠后 RAG
    assert result.rag_snippets_kept + result.rag_snippets_dropped == 3
    joined = "\n".join(str(m.get("content") or "") for m in result.messages)
    assert "[3] 低相关超长" not in joined
    assert "[1] 高相关短片段" in joined

    prompt_chars = estimate_llm_messages_chars(result.messages)
    est = estimate_llm_messages_prompt_tokens(result.messages, context_total_tokens=40960)
    assert prompt_within_context_budget(
        prompt_chars=prompt_chars,
        requested_max_tokens=result.max_tokens,
        context_total_tokens=40960,
        slack_tokens=768,
        estimated_prompt_tokens=est,
    )


def test_history_trimmed_before_rag_when_both_oversized() -> None:
    """同时过大时：必须先有 history_dropped，再 rag_dropped（若仍需）。"""
    history = [
        {"role": "user", "content": "h0"},
        {"role": "assistant", "content": "H" + ("史" * 20000)},
        {"role": "user", "content": "h1"},
        {"role": "assistant", "content": "H" + ("史" * 20000)},
    ]
    snippets = [
        "[1] " + ("知" * 5000),
        "[2] " + ("知" * 5000),
        "[3] " + ("知" * 25000),
    ]

    def build_messages(hist: list[dict], snips: list[str]) -> list[dict]:
        return assemble_chatbot_llm_messages(
            system_chunks=["sys", "\n\n".join(snips)],
            history=hist,
            query="q",
        )

    result = trim_history_and_build_chatbot_messages(
        history,
        build_messages=build_messages,
        rag_snippets=snippets,
        context_total_tokens=40960,
        requested_max_tokens=2048,
        slack_tokens=768,
        trim_enabled=True,
        min_keep=0,
    )

    assert result.history_dropped > 0
    # 全量历史+全量 RAG 必超窗；裁完历史后若仍超才会动 RAG
    assert result.rag_snippets_kept <= 3
    assert result.rag_snippets_kept + result.rag_snippets_dropped == 3
