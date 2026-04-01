from __future__ import annotations

import threading
import time
from contextlib import contextmanager
from typing import Iterator

from app.core.logging import get_logger

logger = get_logger(__name__)


class MinerUConcurrencyGate:
    """
    MinerU 解析全局并发：优先使用 Redis 列表信号量（多 uvicorn worker 共享），
    无 Redis 或连接失败时退化为进程内 BoundedSemaphore。
    """

    def __init__(
        self,
        *,
        redis_url: str | None,
        max_concurrent: int,
        key_prefix: str = "mineru:ingest",
    ) -> None:
        self._max = max(1, max_concurrent)
        self._pool_key = f"{key_prefix}:sem_pool"
        self._init_key = f"{key_prefix}:sem_pool_initialized"
        self._lock_key = f"{key_prefix}:sem_pool_init_lock"
        self._local = threading.BoundedSemaphore(self._max)
        self._redis = None
        self._redis_url = redis_url
        if redis_url:
            try:
                import redis as redis_sync  # type: ignore[import-untyped]

                r = redis_sync.Redis.from_url(redis_url, decode_responses=True, socket_timeout=30)
                r.ping()
                self._redis = r
                logger.info("MinerU concurrency: using Redis pool at prefix=%s max=%s", key_prefix, self._max)
            except Exception as e:  # noqa: BLE001
                logger.warning(
                    "MinerU concurrency: Redis unavailable (%s), falling back to in-process semaphore; "
                    "multi-worker deployments should fix REDIS_URL for correct global limits.",
                    e,
                )
                self._redis = None
        else:
            logger.warning(
                "MinerU concurrency: REDIS_URL empty, using in-process semaphore only; "
                "not safe across multiple uvicorn workers if max_concurrent>1."
            )

    def _ensure_redis_pool(self) -> None:
        assert self._redis is not None
        if self._redis.get(self._init_key):
            return
        with self._redis.lock(self._lock_key, timeout=30, blocking_timeout=30):
            if self._redis.get(self._init_key):
                return
            pipe = self._redis.pipeline()
            pipe.delete(self._pool_key)
            for _ in range(self._max):
                pipe.rpush(self._pool_key, "1")
            pipe.set(self._init_key, "1")
            pipe.execute()
            logger.info("MinerU Redis semaphore pool initialized: %s tokens", self._max)

    @contextmanager
    def acquire(self, blocking_timeout_s: float) -> Iterator[None]:
        if self._redis is not None:
            self._ensure_redis_pool()
            deadline = time.monotonic() + max(1.0, blocking_timeout_s)
            token = None
            while time.monotonic() < deadline:
                token = self._redis.blpop(self._pool_key, timeout=2)
                if token:
                    break
            if not token:
                raise TimeoutError(
                    f"MinerU Redis semaphore timeout after {blocking_timeout_s}s "
                    f"(pool={self._pool_key}, max={self._max})"
                )
            try:
                yield
            finally:
                self._redis.rpush(self._pool_key, "1")
            return

        ok = self._local.acquire(timeout=max(1.0, blocking_timeout_s))
        if not ok:
            raise TimeoutError(f"MinerU local semaphore timeout after {blocking_timeout_s}s")
        try:
            yield
        finally:
            self._local.release()


_gate_singleton: MinerUConcurrencyGate | None = None
_gate_params: tuple[str | None, int, str] | None = None
_gate_lock = threading.Lock()


def get_mineru_gate(redis_url: str | None, max_concurrent: int, key_prefix: str) -> MinerUConcurrencyGate:
    """
    按 (redis_url, max_concurrent, key_prefix) 缓存；任一变化则重建，避免热改配置仍用旧并发上限。
    """
    global _gate_singleton, _gate_params
    params = (redis_url, max(1, max_concurrent), key_prefix)
    with _gate_lock:
        if _gate_singleton is None or _gate_params != params:
            _gate_singleton = MinerUConcurrencyGate(
                redis_url=redis_url,
                max_concurrent=max_concurrent,
                key_prefix=key_prefix,
            )
            _gate_params = params
        return _gate_singleton
