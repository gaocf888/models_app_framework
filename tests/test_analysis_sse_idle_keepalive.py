"""SSE idle keepalive（scope resume 流 Phase 1）。"""

from __future__ import annotations

import asyncio
import unittest
from collections.abc import AsyncIterator

from app.services.analysis_service import (
    _SSE_IDLE_PING_COMMENT,
    _encode_sse_event,
    _sse_stream_with_idle_keepalive,
)


async def _slow_event_source(*, delay_s: float) -> AsyncIterator[dict]:
    yield {"event": "started", "request_id": "anl_test"}
    await asyncio.sleep(delay_s)
    yield {"event": "done", "request_id": "anl_test"}


class TestSseStreamIdleKeepalive(unittest.IsolatedAsyncioTestCase):
    async def test_emits_ping_when_upstream_idle(self) -> None:
        chunks: list[bytes] = []
        async for chunk in _sse_stream_with_idle_keepalive(
            _slow_event_source(delay_s=0.55),
            idle_interval_s=0.15,
        ):
            chunks.append(chunk)

        self.assertEqual(_encode_sse_event({"event": "started", "request_id": "anl_test"}), chunks[0])
        ping_count = sum(1 for c in chunks if c == _SSE_IDLE_PING_COMMENT)
        self.assertGreaterEqual(ping_count, 2)
        self.assertEqual(_encode_sse_event({"event": "done", "request_id": "anl_test"}), chunks[-1])

    async def test_disabled_when_interval_zero(self) -> None:
        chunks: list[bytes] = []
        async for chunk in _sse_stream_with_idle_keepalive(
            _slow_event_source(delay_s=0.4),
            idle_interval_s=0.0,
        ):
            chunks.append(chunk)

        self.assertEqual(2, len(chunks))
        self.assertFalse(any(c == _SSE_IDLE_PING_COMMENT for c in chunks))

    async def test_no_ping_when_events_arrive_quickly(self) -> None:
        async def fast_source() -> AsyncIterator[dict]:
            yield {"event": "a"}
            yield {"event": "b"}

        chunks: list[bytes] = []
        async for chunk in _sse_stream_with_idle_keepalive(
            fast_source(),
            idle_interval_s=0.2,
        ):
            chunks.append(chunk)

        self.assertEqual(2, len(chunks))
        self.assertFalse(any(c == _SSE_IDLE_PING_COMMENT for c in chunks))


if __name__ == "__main__":
    unittest.main()
