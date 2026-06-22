from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any


class AnalysisStreamCancelled(Exception):
    """用户通过 ``/analysis/stream/stop`` 请求中断流式输出。"""


async def is_stream_cancelled(cancel_checker: Callable[[], Awaitable[bool]] | None) -> bool:
    if cancel_checker is None:
        return False
    try:
        return bool(await cancel_checker())
    except Exception:  # noqa: BLE001
        return False


async def raise_if_stream_cancelled(cancel_checker: Callable[[], Awaitable[bool]] | None) -> None:
    if await is_stream_cancelled(cancel_checker):
        raise AnalysisStreamCancelled()


async def cancel_asyncio_tasks(tasks: list[Any]) -> None:
    pending = [t for t in tasks if t is not None and not t.done()]
    for task in pending:
        task.cancel()
    if pending:
        await asyncio.gather(*pending, return_exceptions=True)
