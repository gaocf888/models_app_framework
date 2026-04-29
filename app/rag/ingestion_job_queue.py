from __future__ import annotations

import os
import socket
import threading
import time
from typing import Optional

from app.core.logging import get_logger

logger = get_logger(__name__)


class IngestionJobQueue:
    """
    Durable ingestion queue.

    Redis mode:
    - pending list: LPUSH by producers, BRPOPLPUSH by consumers
    - processing list: claimed-but-not-acked jobs
    - queued set: dedupe to avoid duplicate enqueue
    """

    def __init__(self, *, redis_url: str | None = None, key_prefix: str = "rag:ingest") -> None:
        self._redis_url = (redis_url or os.getenv("REDIS_URL") or "").strip() or None
        self._prefix = key_prefix
        self._pending_key = f"{key_prefix}:pending"
        self._processing_key = f"{key_prefix}:processing"
        self._queued_set_key = f"{key_prefix}:queued_set"
        self._lease_key_prefix = f"{key_prefix}:lease"
        self._owner = f"{socket.gethostname()}:{os.getpid()}:{threading.get_ident()}"
        self._redis = None
        self._enabled = False

        if not self._redis_url:
            logger.warning("ingestion queue: REDIS_URL empty, durable queue disabled")
            return
        try:
            import redis as redis_sync  # type: ignore[import-untyped]

            self._redis = redis_sync.Redis.from_url(
                self._redis_url, decode_responses=True, socket_timeout=30
            )
            self._redis.ping()
            self._enabled = True
            logger.info("ingestion queue enabled: redis=%s prefix=%s", self._redis_url, self._prefix)
        except Exception as e:  # noqa: BLE001
            logger.warning("ingestion queue: redis unavailable, durable queue disabled err=%s", e)
            self._redis = None
            self._enabled = False

    @property
    def enabled(self) -> bool:
        return self._enabled and self._redis is not None

    def enqueue(self, job_id: str) -> bool:
        """
        Enqueue once by job_id.
        Returns True when newly enqueued, False when already queued/in-flight.
        """
        if not self.enabled:
            return False
        assert self._redis is not None
        added = int(self._redis.sadd(self._queued_set_key, job_id))
        if added == 0:
            return False
        self._redis.lpush(self._pending_key, job_id)
        return True

    def pop(self, block_timeout_s: int = 2) -> Optional[str]:
        if not self.enabled:
            return None
        assert self._redis is not None
        res = self._redis.brpoplpush(self._pending_key, self._processing_key, timeout=max(1, int(block_timeout_s)))
        if not res:
            return None
        return str(res)

    def ack(self, job_id: str) -> None:
        if not self.enabled:
            return
        assert self._redis is not None
        self._redis.lrem(self._processing_key, 1, job_id)
        self._redis.srem(self._queued_set_key, job_id)

    def nack_requeue(self, job_id: str) -> None:
        if not self.enabled:
            return
        assert self._redis is not None
        self._redis.lrem(self._processing_key, 1, job_id)
        self._redis.lpush(self._pending_key, job_id)

    def requeue_processing_on_startup(self, max_items: int = 100000) -> int:
        """
        Move orphaned processing jobs back to pending.
        """
        if not self.enabled:
            return 0
        assert self._redis is not None
        moved = 0
        limit = max(1, int(max_items))
        while moved < limit:
            job_id = self._redis.rpoplpush(self._processing_key, self._pending_key)
            if not job_id:
                break
            moved += 1
        if moved > 0:
            logger.warning("ingestion queue startup recovery: requeued processing jobs=%s", moved)
        return moved

    def pending_len(self) -> int:
        if not self.enabled:
            return 0
        assert self._redis is not None
        return int(self._redis.llen(self._pending_key))

    def acquire_lease(self, job_id: str, ttl_s: int = 7200) -> bool:
        """
        Best-effort distributed lease by job_id to avoid duplicate execution
        across multiple app instances during rolling restart.
        """
        if not self.enabled:
            return True
        assert self._redis is not None
        key = f"{self._lease_key_prefix}:{job_id}"
        ok = self._redis.set(key, self._owner, nx=True, ex=max(30, int(ttl_s)))
        return bool(ok)

    def release_lease(self, job_id: str) -> None:
        if not self.enabled:
            return
        assert self._redis is not None
        key = f"{self._lease_key_prefix}:{job_id}"
        try:
            self._redis.delete(key)
        except Exception:  # noqa: BLE001
            logger.warning("ingestion queue release lease failed job_id=%s", job_id, exc_info=True)

    def sleep_briefly(self, seconds: float = 0.5) -> None:
        time.sleep(max(0.01, seconds))
