from __future__ import annotations

"""Execution Trace / OTLP（Tempo）环境配置：推荐 Redis + Tempo 并行保留。"""

import os
from dataclasses import dataclass
from functools import lru_cache
from typing import FrozenSet

from app.core.logging import get_logger

logger = get_logger(__name__)


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)))
    except Exception:  # noqa: BLE001
        return default


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except Exception:  # noqa: BLE001
        return default


def _parse_modules(raw: str | None) -> FrozenSet[str]:
    if raw is None or not raw.strip():
        return frozenset(
            {
                "analysis",
                "analysis_agent",
                "chatbot",
                "nl2sql",
                "llm_infer",
                "rag_ingest",
                "inspection_extract",
                "graph_rebuild",
            }
        )
    return frozenset(x.strip() for x in raw.split(",") if x.strip())


def _resolve_backend(redis_url: str | None) -> str:
    """
    解析存储后端：
    - 显式 EXECUTION_TRACE_BACKEND / ANALYSIS_TRACE_BACKEND
    - 未配置时：有 REDIS_URL → redis，否则 memory
    - es 尚未实现：有 Redis 则降级 redis，否则 memory
    """
    raw = (os.getenv("EXECUTION_TRACE_BACKEND") or os.getenv("ANALYSIS_TRACE_BACKEND") or "").strip().lower()
    if raw in {"easysearch", "elasticsearch"}:
        raw = "es"
    if not raw:
        return "redis" if redis_url else "memory"
    if raw == "es":
        # 方案预留；当前实现走 Redis+Tempo，避免误伤业务 ES
        fallback = "redis" if redis_url else "memory"
        logger.warning(
            "EXECUTION_TRACE_BACKEND=es is not implemented; falling back to %s (recommended: redis + Tempo)",
            fallback,
        )
        return fallback
    if raw == "redis" and not redis_url:
        logger.warning("EXECUTION_TRACE_BACKEND=redis but REDIS_URL empty; falling back to memory")
        return "memory"
    if raw not in {"memory", "redis"}:
        fallback = "redis" if redis_url else "memory"
        logger.warning("unknown EXECUTION_TRACE_BACKEND=%s; using %s", raw, fallback)
        return fallback
    return raw


@dataclass(frozen=True)
class ExecutionTraceSettings:
    enabled: bool
    backend: str
    ttl_minutes: int
    max_items: int
    modules: FrozenSet[str]
    query_max_chars: int
    analysis_dual_write: bool
    otlp_enabled: bool
    otlp_endpoint: str
    otlp_protocol: str
    otlp_service_name: str
    otlp_sample_rate: float
    otlp_modules: FrozenSet[str]
    otlp_job_live_export: bool
    redis_url: str | None
    # 导出前是否预写 tempo_trace_id 到 Redis（便于 API 跳转 Grafana）
    otlp_preassign_trace_id: bool


@lru_cache(maxsize=1)
def get_execution_trace_settings() -> ExecutionTraceSettings:
    redis_url = os.getenv("REDIS_URL") or None
    modules = _parse_modules(os.getenv("EXECUTION_TRACE_MODULES"))
    otlp_modules_raw = os.getenv("OTEL_TRACE_MODULES")
    otlp_modules = _parse_modules(otlp_modules_raw) if otlp_modules_raw is not None else modules
    backend = _resolve_backend(redis_url)
    # Docker 同网（vllm-external）时容器名为 monitoring-tempo；宿主机映射用 host.docker.internal:4318
    default_endpoint = os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT") or "http://monitoring-tempo:4318"
    return ExecutionTraceSettings(
        enabled=_env_bool("EXECUTION_TRACE_ENABLED", True),
        backend=backend,
        ttl_minutes=max(10, _env_int("EXECUTION_TRACE_TTL_MINUTES", _env_int("ANALYSIS_TRACE_TTL_MINUTES", 1440))),
        max_items=max(100, _env_int("EXECUTION_TRACE_MAX_ITEMS", _env_int("ANALYSIS_TRACE_MAX_ITEMS", 10000))),
        modules=modules,
        query_max_chars=max(64, _env_int("EXECUTION_TRACE_QUERY_MAX_CHARS", 2048)),
        analysis_dual_write=_env_bool("ANALYSIS_TRACE_DUAL_WRITE", True),
        otlp_enabled=_env_bool("EXECUTION_TRACE_OTLP_ENABLED", False) or _env_bool("OTEL_TRACES_ENABLED", False),
        otlp_endpoint=default_endpoint.rstrip("/"),
        otlp_protocol=(os.getenv("OTEL_EXPORTER_OTLP_PROTOCOL") or "http/json").lower(),
        otlp_service_name=os.getenv("OTEL_SERVICE_NAME") or "models-app",
        otlp_sample_rate=max(0.0, min(1.0, _env_float("OTEL_TRACE_SAMPLE_RATE", 1.0))),
        otlp_modules=otlp_modules,
        otlp_job_live_export=_env_bool("OTEL_JOB_LIVE_EXPORT", False),
        redis_url=redis_url,
        otlp_preassign_trace_id=_env_bool("OTEL_PREASSIGN_TRACE_ID", True),
    )


def module_enabled(module: str) -> bool:
    cfg = get_execution_trace_settings()
    return cfg.enabled and module in cfg.modules
