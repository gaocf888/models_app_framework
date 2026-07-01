from __future__ import annotations

from app.llm.context_budget import estimate_llm_messages_chars, prompt_within_context_budget
from app.llm.graphs.chatbot_llm_messages import trim_history_and_build_chatbot_messages


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
    assert prompt_within_context_budget(
        prompt_chars=prompt_chars,
        requested_max_tokens=2048,
        context_total_tokens=40960,
        slack_tokens=768,
    )


def test_trim_keeps_recent_messages() -> None:
    history = [
        {"role": "user", "content": "old-user"},
        {"role": "assistant", "content": "old-assistant " + ("x" * 50000)},
        {"role": "user", "content": "new-user"},
        {"role": "assistant", "content": "new-assistant " + ("y" * 50000)},
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
    assert "old-user" not in kept_text or result.history_dropped == 0
    if result.history_dropped > 0:
        assert "new-assistant" in kept_text


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
