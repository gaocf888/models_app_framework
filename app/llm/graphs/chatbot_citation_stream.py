"""
智能客服流式输出：从 LLM 正文中识别 ``[n]`` 引用标记，拆成 text delta 与 citation 事件。

模型仍按 prompt 输出 ``[1]``；本模块在 vLLM token 流与 SSE 之间做后处理，避免 ``" ["`` / ``"1"`` / ``"]"``
被拆成多个 delta 时前端无法区分引用与正文。
"""

from __future__ import annotations

import re
from typing import Any, Iterator

_CITATION_RE = re.compile(r"^\[\s*(\d+)\s*\]")
_INCOMPLETE_CITATION_RE = re.compile(r"^\[\s*\d*\s*$")


def max_citation_ref_index(rag_citations: list[dict[str, Any]] | None) -> int | None:
    """本轮 rag_citations 中最大 ref_index；无引用时返回 None。"""
    if not rag_citations:
        return None
    indices = [
        int(c["ref_index"])
        for c in rag_citations
        if isinstance(c, dict) and c.get("ref_index") is not None
    ]
    return max(indices) if indices else None


def citation_stream_enabled(rag_citations: list[dict[str, Any]] | None) -> bool:
    return max_citation_ref_index(rag_citations) is not None


class CitationStreamParser:
    """跨 chunk 缓冲，识别完整 ``[n]`` 并输出结构化事件。"""

    def __init__(self, *, max_ref_index: int | None = None) -> None:
        self._buf = ""
        self._max_ref_index = max_ref_index

    def feed(self, chunk: str) -> list[dict[str, Any]]:
        if not chunk:
            return []
        self._buf += chunk
        return self._drain(flush=False)

    def flush(self) -> list[dict[str, Any]]:
        return self._drain(flush=True)

    def _drain(self, *, flush: bool) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        while self._buf:
            bracket = self._buf.find("[")
            if bracket == -1:
                out.append({"type": "delta", "delta": self._buf})
                self._buf = ""
                break
            if bracket > 0:
                out.append({"type": "delta", "delta": self._buf[:bracket]})
                self._buf = self._buf[bracket:]

            matched = _CITATION_RE.match(self._buf)
            if matched:
                ref_index = int(matched.group(1))
                if self._ref_index_valid(ref_index):
                    out.append({"type": "citation", "ref_index": ref_index})
                    self._buf = self._buf[matched.end() :]
                    continue
                out.append({"type": "delta", "delta": self._buf[: matched.end()]})
                self._buf = self._buf[matched.end() :]
                continue

            if not flush and _INCOMPLETE_CITATION_RE.match(self._buf):
                break

            close = self._buf.find("]")
            if close != -1:
                out.append({"type": "delta", "delta": self._buf[: close + 1]})
                self._buf = self._buf[close + 1 :]
                continue

            if not flush:
                break

            out.append({"type": "delta", "delta": self._buf[0]})
            self._buf = self._buf[1:]

        if flush and self._buf:
            out.append({"type": "delta", "delta": self._buf})
            self._buf = ""
        return out

    def _ref_index_valid(self, ref_index: int) -> bool:
        if ref_index < 1:
            return False
        if self._max_ref_index is None:
            return True
        return ref_index <= self._max_ref_index


def iter_parsed_stream_events(
    chunks: Iterator[str],
    *,
    max_ref_index: int | None,
    enabled: bool,
) -> Iterator[dict[str, Any]]:
    """同步迭代：将 raw chunk 流转为 delta / citation 事件（供单元测试与 legacy 路径复用）。"""
    if not enabled:
        for chunk in chunks:
            if chunk:
                yield {"type": "delta", "delta": chunk}
        return
    parser = CitationStreamParser(max_ref_index=max_ref_index)
    for chunk in chunks:
        yield from parser.feed(chunk)
    yield from parser.flush()
