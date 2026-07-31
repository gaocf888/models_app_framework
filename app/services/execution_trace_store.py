from __future__ import annotations

"""统一 ExecutionTrace 存储：memory / redis（es 未实现，工厂降级）。

推荐生产路径：Redis（结构化 API /ops/traces）+ Tempo（OTLP 瀑布图），二者并行、互不替代。
"""

import threading
import time
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple

from app.core.logging import get_logger
from app.models.execution_trace import ExecutionTraceRecord
from app.observability.settings import get_execution_trace_settings

logger = get_logger(__name__)


def _now_ts() -> float:
    return time.time()


class ExecutionTraceStore:
    def save(self, record: ExecutionTraceRecord) -> None:  # pragma: no cover - interface
        raise NotImplementedError

    def get(self, request_id: str) -> Optional[ExecutionTraceRecord]:  # pragma: no cover
        raise NotImplementedError

    def list(  # pragma: no cover
        self,
        limit: int,
        offset: int,
        *,
        module: str | None = None,
        kind: str | None = None,
        scene: str | None = None,
        status: str | None = None,
        started_after: str | None = None,
        started_before: str | None = None,
    ) -> Tuple[List[ExecutionTraceRecord], int]:
        raise NotImplementedError

    def backend_name(self) -> str:
        return type(self).__name__


def _parse_started(value: str | None) -> datetime | None:
    if not value:
        return None
    text = value.strip()
    if not text:
        return None
    try:
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        dt = datetime.fromisoformat(text)
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except Exception:  # noqa: BLE001
        return None


def _in_time_window(rec: ExecutionTraceRecord, started_after: str | None, started_before: str | None) -> bool:
    after = _parse_started(started_after)
    before = _parse_started(started_before)
    if after is None and before is None:
        return True
    try:
        dt = datetime.fromisoformat(rec.started_at.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
    except Exception:  # noqa: BLE001
        return False
    if after is not None and dt < after:
        return False
    if before is not None and dt > before:
        return False
    return True


class InMemoryExecutionTraceStore(ExecutionTraceStore):
    def __init__(self, max_items: int = 2000) -> None:
        self._max_items = max(100, max_items)
        self._data: Dict[str, tuple[float, ExecutionTraceRecord]] = {}
        self._lock = threading.Lock()

    def save(self, record: ExecutionTraceRecord) -> None:
        with self._lock:
            self._data[record.request_id] = (_now_ts(), record)
            if len(self._data) > self._max_items:
                items = sorted(self._data.items(), key=lambda kv: kv[1][0], reverse=True)
                self._data = dict(items[: self._max_items])

    def get(self, request_id: str) -> Optional[ExecutionTraceRecord]:
        with self._lock:
            hit = self._data.get(request_id)
        return None if hit is None else hit[1]

    def list(
        self,
        limit: int,
        offset: int,
        *,
        module: str | None = None,
        kind: str | None = None,
        scene: str | None = None,
        status: str | None = None,
        started_after: str | None = None,
        started_before: str | None = None,
    ) -> Tuple[List[ExecutionTraceRecord], int]:
        limit = max(1, min(limit, 1000))
        offset = max(0, offset)
        with self._lock:
            rows = sorted(self._data.items(), key=lambda kv: kv[1][0], reverse=True)
        filtered: List[ExecutionTraceRecord] = []
        for _, (_ts, rec) in rows:
            if module and rec.module != module:
                continue
            if kind and rec.kind != kind:
                continue
            if scene and (rec.scene or "") != scene:
                continue
            if status and rec.status != status:
                continue
            if not _in_time_window(rec, started_after, started_before):
                continue
            filtered.append(rec)
        total = len(filtered)
        return filtered[offset : offset + limit], total


class RedisExecutionTraceStore(ExecutionTraceStore):
    """同步 redis 客户端；失败由工厂回退 memory。

    Keys:
      exec:trace:{request_id}           JSON 正文
      exec:trace:index                  ZSET score=写入时间
      exec:trace:idx:module:{module}    SET request_id
      exec:trace:idx:kind:{kind}        SET request_id
    """

    def __init__(self, redis_url: str, ttl_minutes: int = 1440, max_items: int = 10000) -> None:
        import redis  # type: ignore[import-untyped]

        self._client = redis.Redis.from_url(redis_url, decode_responses=True)
        self._client.ping()
        self._ttl = max(0, ttl_minutes * 60)
        self._max_items = max(1000, max_items)
        self._prefix = "exec:trace:"
        self._index = "exec:trace:index"
        self._idx_module = "exec:trace:idx:module:"
        self._idx_kind = "exec:trace:idx:kind:"

    def _key(self, request_id: str) -> str:
        return f"{self._prefix}{request_id}"

    def save(self, record: ExecutionTraceRecord) -> None:
        payload = record.model_dump_json()
        mod_key = f"{self._idx_module}{record.module}"
        kind_key = f"{self._idx_kind}{record.kind}"
        pipe = self._client.pipeline()
        pipe.set(self._key(record.request_id), payload)
        if self._ttl > 0:
            pipe.expire(self._key(record.request_id), self._ttl)
        score = _now_ts()
        pipe.zadd(self._index, {record.request_id: score})
        pipe.zremrangebyrank(self._index, 0, -self._max_items - 1)
        pipe.sadd(mod_key, record.request_id)
        pipe.sadd(kind_key, record.request_id)
        if self._ttl > 0:
            pipe.expire(mod_key, self._ttl)
            pipe.expire(kind_key, self._ttl)
        pipe.execute()

    def get(self, request_id: str) -> Optional[ExecutionTraceRecord]:
        raw = self._client.get(self._key(request_id))
        if not raw:
            return None
        try:
            return ExecutionTraceRecord.model_validate_json(raw)
        except Exception as exc:  # noqa: BLE001
            logger.warning("RedisExecutionTraceStore get decode failed: %s", exc)
            return None

    def _ids_from_secondary(self, module: str | None, kind: str | None) -> List[str] | None:
        if not module and not kind:
            return None
        try:
            if module and kind:
                raw_ids = self._client.sinter(f"{self._idx_module}{module}", f"{self._idx_kind}{kind}")
            elif module:
                raw_ids = self._client.smembers(f"{self._idx_module}{module}")
            else:
                raw_ids = self._client.smembers(f"{self._idx_kind}{kind}")
        except Exception as exc:  # noqa: BLE001
            logger.warning("Redis secondary index read failed: %s", exc)
            return None
        scored: List[tuple[float, str]] = []
        for rid in raw_ids or []:
            sc = self._client.zscore(self._index, rid)
            if sc is None:
                continue
            scored.append((float(sc), str(rid)))
        scored.sort(key=lambda x: x[0], reverse=True)
        return [rid for _, rid in scored]

    def list(
        self,
        limit: int,
        offset: int,
        *,
        module: str | None = None,
        kind: str | None = None,
        scene: str | None = None,
        status: str | None = None,
        started_after: str | None = None,
        started_before: str | None = None,
    ) -> Tuple[List[ExecutionTraceRecord], int]:
        limit = max(1, min(limit, 1000))
        offset = max(0, offset)
        ids = self._ids_from_secondary(module, kind)
        if ids is None:
            # 无 module/kind：从全局 ZSET 取一段再过滤
            ids = list(self._client.zrevrange(self._index, 0, max(offset + limit * 5, 500) - 1) or [])
            filter_module, filter_kind = module, kind
        else:
            # 二级索引已按 module/kind 收窄
            filter_module, filter_kind = None, None
        out: List[ExecutionTraceRecord] = []
        for rid in ids:
            rec = self.get(str(rid))
            if rec is None:
                continue
            if filter_module and rec.module != filter_module:
                continue
            if filter_kind and rec.kind != filter_kind:
                continue
            if scene and (rec.scene or "") != scene:
                continue
            if status and rec.status != status:
                continue
            if not _in_time_window(rec, started_after, started_before):
                continue
            out.append(rec)
        total = len(out)
        return out[offset : offset + limit], total


_store_singleton: ExecutionTraceStore | None = None
_store_lock = threading.Lock()


def create_execution_trace_store() -> ExecutionTraceStore:
    cfg = get_execution_trace_settings()
    store: ExecutionTraceStore
    if cfg.backend == "memory" or not cfg.redis_url:
        store = InMemoryExecutionTraceStore(max_items=cfg.max_items)
        logger.info(
            "ExecutionTraceStore backend=memory (configured=%s redis_url=%s) otlp=%s endpoint=%s",
            cfg.backend,
            bool(cfg.redis_url),
            cfg.otlp_enabled,
            cfg.otlp_endpoint,
        )
        return store
    try:
        store = RedisExecutionTraceStore(
            redis_url=cfg.redis_url,
            ttl_minutes=cfg.ttl_minutes,
            max_items=cfg.max_items,
        )
        logger.info(
            "ExecutionTraceStore backend=redis ttl_minutes=%s otlp=%s endpoint=%s",
            cfg.ttl_minutes,
            cfg.otlp_enabled,
            cfg.otlp_endpoint,
        )
        return store
    except Exception as exc:  # noqa: BLE001
        logger.error("init RedisExecutionTraceStore failed, fallback memory: %s", exc)
        return InMemoryExecutionTraceStore(max_items=cfg.max_items)


def get_execution_trace_store() -> ExecutionTraceStore:
    global _store_singleton
    if _store_singleton is None:
        with _store_lock:
            if _store_singleton is None:
                _store_singleton = create_execution_trace_store()
    return _store_singleton


def reset_execution_trace_store_for_tests() -> None:
    """测试用：重置单例。"""
    global _store_singleton
    with _store_lock:
        _store_singleton = InMemoryExecutionTraceStore(max_items=2000)
        get_execution_trace_settings.cache_clear()
