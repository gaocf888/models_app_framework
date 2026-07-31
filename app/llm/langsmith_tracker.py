from __future__ import annotations

"""
LangSmith tracing 集成中间层（可选启用）。

设计目标：
- 不强依赖 LangSmith SDK；
- 通过环境变量控制开关与采样；
- 支持扁平 log_run 与 mirror_execution_trace（父子摘要）；
- 未配置或异常时 no-op，不影响主流程。
"""

import os
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Any, Dict, Optional

from app.core.logging import get_logger

logger = get_logger(__name__)

_executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="langsmith")


@dataclass
class LangSmithSettings:
    enabled: bool
    api_key: Optional[str]
    project: Optional[str]
    endpoint: Optional[str]
    async_mode: bool
    sample_rate: float
    mirror_modules: frozenset[str]


def _load_settings() -> LangSmithSettings:
    api_key = os.getenv("LANGSMITH_API_KEY")
    project = os.getenv("LANGSMITH_PROJECT")
    explicit_disabled = os.getenv("LANGSMITH_ENABLED", "").lower() == "false"
    enabled = bool(api_key and project) and not explicit_disabled
    if not enabled:
        logger.info("LangSmithTracker: disabled (missing env or explicitly disabled).")
    modules_raw = os.getenv("LANGSMITH_MIRROR_MODULES", "")
    if modules_raw.strip():
        mirror_modules = frozenset(x.strip() for x in modules_raw.split(",") if x.strip())
    else:
        # 默认不含长任务模块，减少噪声
        mirror_modules = frozenset(
            {"analysis", "analysis_agent", "chatbot", "nl2sql", "llm_infer"}
        )
    try:
        sample_rate = float(os.getenv("LANGSMITH_SAMPLE_RATE", "1.0"))
    except Exception:  # noqa: BLE001
        sample_rate = 1.0
    return LangSmithSettings(
        enabled=enabled,
        api_key=api_key,
        project=project,
        endpoint=os.getenv("LANGSMITH_ENDPOINT"),
        async_mode=os.getenv("LANGSMITH_ASYNC", "true").lower() != "false",
        sample_rate=max(0.0, min(1.0, sample_rate)),
        mirror_modules=mirror_modules,
    )


class LangSmithTracker:
    """LangSmith trace 记录器（单例推荐通过 get_langsmith_tracker）。"""

    def __init__(self) -> None:
        self._settings = _load_settings()
        self._client = None

        if not self._settings.enabled:
            return

        try:
            from langsmith import Client  # type: ignore[import-not-found]

            kwargs: Dict[str, Any] = {
                "api_key": self._settings.api_key,
            }
            # 部分版本 Client 支持 api_url；失败则忽略
            if self._settings.endpoint:
                kwargs["api_url"] = self._settings.endpoint
            try:
                self._client = Client(**kwargs)
            except TypeError:
                self._client = Client(api_key=self._settings.api_key)
            logger.info("LangSmithTracker: initialized for project=%s", self._settings.project)
        except Exception as exc:  # noqa: BLE001
            logger.warning("LangSmithTracker: failed to initialize LangSmith client: %s", exc)
            self._client = None

    @property
    def enabled(self) -> bool:
        return self._client is not None

    def log_run(
        self,
        name: str,
        run_type: str,
        inputs: Dict[str, Any],
        outputs: Optional[Dict[str, Any]] = None,
        metadata: Optional[Dict[str, Any]] = None,
        *,
        run_id: str | None = None,
        parent_run_id: str | None = None,
        module: str = "unknown",
    ) -> Optional[str]:
        """记录一次简单的 LangSmith run；返回 run_id（可能为本地生成）。"""
        if not self.enabled:
            return None

        rid = run_id or str(uuid.uuid4())

        def _do() -> None:
            started = time.perf_counter()
            try:
                kwargs: Dict[str, Any] = {
                    "id": rid,
                    "name": name,
                    "run_type": run_type,
                    "inputs": inputs,
                    "outputs": outputs or {},
                    "extra": metadata or {},
                    "project_name": self._settings.project,
                }
                if parent_run_id:
                    kwargs["parent_run_id"] = parent_run_id
                try:
                    self._client.create_run(**kwargs)  # type: ignore[union-attr]
                except TypeError:
                    # 旧 SDK 可能不支持 id/parent_run_id/project_name
                    self._client.create_run(  # type: ignore[union-attr]
                        name=name,
                        run_type=run_type,
                        inputs=inputs,
                        outputs=outputs or {},
                        extra=metadata or {},
                    )
                self._metric(module, "ok", time.perf_counter() - started)
            except Exception as exc:  # noqa: BLE001
                logger.warning("LangSmithTracker: failed to log run '%s': %s", name, exc)
                self._metric(module, "error", time.perf_counter() - started)

        if self._settings.async_mode:
            try:
                _executor.submit(_do)
            except Exception as exc:  # noqa: BLE001
                logger.warning("LangSmithTracker: schedule failed: %s", exc)
                self._metric(module, "error", 0.0)
        else:
            _do()
        return rid

    def start_parent(
        self,
        name: str,
        inputs: Dict[str, Any],
        metadata: Optional[Dict[str, Any]] = None,
        *,
        module: str = "unknown",
    ) -> Optional[str]:
        return self.log_run(
            name=name,
            run_type="chain",
            inputs=inputs,
            outputs={},
            metadata=metadata,
            module=module,
        )

    def end_parent(
        self,
        run_id: str,
        outputs: Optional[Dict[str, Any]] = None,
        error: Optional[str] = None,
        *,
        module: str = "unknown",
    ) -> None:
        # 简化：再写一条同 id 的完成摘要（SDK 若支持 update_run 可后续增强）
        meta = {"error": error} if error else {}
        self.log_run(
            name="parent_end",
            run_type="chain",
            inputs={},
            outputs=outputs or {},
            metadata=meta,
            run_id=run_id,
            module=module,
        )

    def log_child(
        self,
        parent_run_id: str,
        name: str,
        run_type: str,
        inputs: Dict[str, Any],
        outputs: Optional[Dict[str, Any]] = None,
        metadata: Optional[Dict[str, Any]] = None,
        *,
        module: str = "unknown",
    ) -> Optional[str]:
        return self.log_run(
            name=name,
            run_type=run_type,
            inputs=inputs,
            outputs=outputs,
            metadata=metadata,
            parent_run_id=parent_run_id,
            module=module,
        )

    def mirror_execution_trace(self, record: Any) -> None:
        """由 ExecutionTraceRecord 镜像到 LangSmith（采样 + 模块白名单）。"""
        if not self.enabled:
            return
        module = getattr(record, "module", "unknown")
        if module not in self._settings.mirror_modules:
            self._metric(module, "skipped", 0.0)
            return
        import random

        if random.random() > self._settings.sample_rate:
            self._metric(module, "skipped", 0.0)
            return

        request_id = getattr(record, "request_id", "")
        parent = self.start_parent(
            name=f"{module}_request",
            inputs={
                "request_id": request_id,
                "kind": getattr(record, "kind", "request"),
                "scene": getattr(record, "scene", None),
            },
            metadata={
                "request_id": request_id,
                "module": module,
                "status": getattr(record, "status", None),
                "degrade_reasons": list(getattr(record, "degrade_reasons", None) or []),
            },
            module=module,
        )
        if parent and hasattr(record, "meta") and isinstance(record.meta, dict):
            record.meta["langsmith_run_id"] = parent
            try:
                from app.observability.meta_patch import patch_execution_trace_meta

                patch_execution_trace_meta(str(request_id), {"langsmith_run_id": parent})
            except Exception:  # noqa: BLE001
                pass
        for node in getattr(record, "nodes", None) or []:
            self.log_child(
                parent_run_id=parent or "",
                name=f"node:{getattr(node, 'node_id', 'unknown')}",
                run_type="tool",
                inputs={"node_id": getattr(node, "node_id", None)},
                outputs={
                    "status": getattr(node, "status", None),
                    "latency_ms": getattr(node, "latency_ms", None),
                },
                metadata={"request_id": request_id},
                module=module,
            )

    @staticmethod
    def _metric(module: str, result: str, latency: float) -> None:
        try:
            from app.core.metrics import LANGSMITH_EXPORT_LATENCY, LANGSMITH_RUNS_TOTAL

            LANGSMITH_RUNS_TOTAL.labels(module=module, result=result).inc()
            if latency > 0:
                LANGSMITH_EXPORT_LATENCY.labels(module=module).observe(latency)
        except Exception:  # noqa: BLE001
            pass


_tracker_singleton: LangSmithTracker | None = None
_tracker_lock = threading.Lock()


def get_langsmith_tracker() -> LangSmithTracker:
    global _tracker_singleton
    if _tracker_singleton is None:
        with _tracker_lock:
            if _tracker_singleton is None:
                _tracker_singleton = LangSmithTracker()
    return _tracker_singleton
