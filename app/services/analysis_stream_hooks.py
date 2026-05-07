"""
综合分析流式路由的后处理钩子（结构化 JSON 异步投递预留）。

**触发路径**：`POST /analysis/run-with-nl2sql-stream`、`POST /analysis/run-img-diag-stream` 在 summary 流结束后，
后台任务均调用 `dispatch_analysis_nl2sql_stream_structured`（函数名历史遗留；看图诊断复用同一钩子列表）。

用法：
    from app.services.analysis_stream_hooks import register_analysis_nl2sql_stream_structured_hook

    async def push_to_kafka(payload: dict) -> None:
        ...

    register_analysis_nl2sql_stream_structured_hook(push_to_kafka)

`payload` 为 `AnalysisV2Result.model_dump(mode="json")` 的可 JSON 序列化字典。
"""

from __future__ import annotations

from typing import Any, Awaitable, Callable

from app.core.logging import get_logger

logger = get_logger(__name__)

_analysis_nl2sql_stream_structured_hooks: list[Callable[[dict[str, Any]], Awaitable[None]]] = []


def register_analysis_nl2sql_stream_structured_hook(
    fn: Callable[[dict[str, Any]], Awaitable[None]],
) -> None:
    """注册异步回调；流式 synthesis 完成后在后台任务中依次 await，异常单独捕获不打断其它钩子。"""
    _analysis_nl2sql_stream_structured_hooks.append(fn)


async def dispatch_analysis_nl2sql_stream_structured(payload: dict[str, Any]) -> None:
    """内部调用：将完整分析结果投递给已注册钩子。"""
    for fn in list(_analysis_nl2sql_stream_structured_hooks):
        try:
            await fn(payload)
        except Exception:  # noqa: BLE001
            logger.exception("analysis_nl2sql_stream_structured_hook failed")
