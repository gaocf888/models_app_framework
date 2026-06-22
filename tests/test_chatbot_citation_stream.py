"""chatbot_citation_stream 单元测试。"""

from __future__ import annotations

from app.llm.graphs.chatbot_citation_stream import (
    CitationStreamParser,
    iter_parsed_stream_events,
    max_citation_ref_index,
)


def test_split_tokens_become_citation_ref() -> None:
    parser = CitationStreamParser(max_ref_index=3)
    out: list[dict] = []
    for chunk in ("火", " [", "1", "]", "。"):
        out.extend(parser.feed(chunk))
    out.extend(parser.flush())
    assert {"type": "delta", "delta": "火"} in out
    assert {"type": "citation", "ref_index": 1} in out
    assert {"type": "delta", "delta": "。"} in out
    assert not any(ev.get("delta") == "[1]" for ev in out if ev.get("type") == "delta")


def test_invalid_ref_index_emitted_as_text() -> None:
    parser = CitationStreamParser(max_ref_index=2)
    out = parser.feed("见[99]")
    out.extend(parser.flush())
    assert {"type": "citation", "ref_index": 99} not in out
    assert any(ev.get("type") == "delta" and "[99]" in ev.get("delta", "") for ev in out)


def test_non_citation_brackets_passthrough() -> None:
    parser = CitationStreamParser(max_ref_index=5)
    out = parser.feed("1号机组[abc]测试")
    out.extend(parser.flush())
    joined = "".join(ev.get("delta", "") for ev in out if ev.get("type") == "delta")
    assert "[abc]" in joined
    assert not any(ev.get("type") == "citation" for ev in out)


def test_iter_parsed_stream_events_disabled() -> None:
    events = list(
        iter_parsed_stream_events(iter(["a[1]"]), max_ref_index=1, enabled=False)
    )
    assert events == [{"type": "delta", "delta": "a[1]"}]


def test_max_citation_ref_index() -> None:
    assert max_citation_ref_index([{"ref_index": 1}, {"ref_index": 3}]) == 3
    assert max_citation_ref_index([]) is None
