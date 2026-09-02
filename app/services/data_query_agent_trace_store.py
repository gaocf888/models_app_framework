"""数据查询智能体 trace：键前缀 data_query_agent:trace:*，不写入 analysis / analysis_agent 索引。"""

from __future__ import annotations

import json
import os
import threading
import time
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Dict, List, Optional, Tuple

from app.core.logging import get_logger

logger = get_logger(__name__)

_KEY_PREFIX = "data_query_agent:trace:"
_INDEX_KEY = "data_query_agent:trace:index"
_INDEX_LIB_PREFIX = "data_query_agent:trace:index:library:"
_INDEX_USER_PREFIX = "data_query_agent:trace:index:user:"


def _json_default(value: Any) -> Any:
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)


class DataQueryAgentTraceStore:
    def save(self, record: dict[str, Any]) -> None:  # pragma: no cover
        raise NotImplementedError

    def get(self, request_id: str) -> Optional[dict[str, Any]]:  # pragma: no cover
        raise NotImplementedError

    def list(  # pragma: no cover
        self,
        limit: int,
        offset: int,
        *,
        score_min_ms: int | None = None,
        score_max_ms: int | None = None,
        library_id: str | None = None,
        user_id: str | None = None,
    ) -> Tuple[List[dict[str, Any]], int]:
        raise NotImplementedError


class InMemoryDataQueryAgentTraceStore(DataQueryAgentTraceStore):
    def __init__(self, max_items: int = 2000) -> None:
        self._max_items = max(100, max_items)
        self._data: Dict[str, tuple[float, dict[str, Any]]] = {}
        self._lock = threading.Lock()

    def save(self, record: dict[str, Any]) -> None:
        rid = str(record.get("request_id") or "").strip()
        if not rid:
            return
        now = time.time()
        with self._lock:
            self._data[rid] = (now, dict(record))
            if len(self._data) > self._max_items:
                items = sorted(self._data.items(), key=lambda kv: kv[1][0], reverse=True)
                self._data = dict(items[: self._max_items])

    def get(self, request_id: str) -> Optional[dict[str, Any]]:
        with self._lock:
            hit = self._data.get(request_id)
        return None if hit is None else dict(hit[1])

    def list(
        self,
        limit: int,
        offset: int,
        *,
        score_min_ms: int | None = None,
        score_max_ms: int | None = None,
        library_id: str | None = None,
        user_id: str | None = None,
    ) -> Tuple[List[dict[str, Any]], int]:
        limit = max(1, min(limit, 1000))
        offset = max(0, offset)
        with self._lock:
            rows = sorted(self._data.items(), key=lambda kv: kv[1][0], reverse=True)
        if score_min_ms is not None or score_max_ms is not None:
            min_s = (score_min_ms / 1000.0) if score_min_ms is not None else float("-inf")
            max_s = (score_max_ms / 1000.0) if score_max_ms is not None else float("inf")
            rows = [x for x in rows if min_s <= x[1][0] <= max_s]
        if library_id:
            rows = [x for x in rows if str(x[1][1].get("library_id") or "") == library_id]
        if user_id:
            rows = [x for x in rows if str(x[1][1].get("user_id") or "") == user_id]
        total = len(rows)
        page = rows[offset : offset + limit]
        return [dict(x[1][1]) for x in page], total


class RedisDataQueryAgentTraceStore(DataQueryAgentTraceStore):
    """Redis JSON + ZSET；键前缀 data_query_agent:trace:*。"""

    def __init__(self, redis_url: str, ttl_minutes: int = 1440, max_items: int = 10000) -> None:
        import redis  # type: ignore[import-untyped]

        self._ttl_seconds = max(0, ttl_minutes * 60)
        self._max_items = max(1000, max_items)
        self._client = redis.from_url(redis_url, decode_responses=True, socket_connect_timeout=2)

    def save(self, record: dict[str, Any]) -> None:
        rid = str(record.get("request_id") or "").strip()
        if not rid:
            return
        now_ms = int(time.time() * 1000)
        lid = str(record.get("library_id") or "_")
        uid = str(record.get("user_id") or "").strip() or "_"
        payload = json.dumps(record, ensure_ascii=False, default=_json_default)
        pipe = self._client.pipeline(transaction=True)
        key = f"{_KEY_PREFIX}{rid}"
        pipe.set(key, payload)
        if self._ttl_seconds > 0:
            pipe.expire(key, self._ttl_seconds)
        pipe.zadd(_INDEX_KEY, {rid: now_ms})
        pipe.zadd(f"{_INDEX_LIB_PREFIX}{lid}", {rid: now_ms})
        pipe.zadd(f"{_INDEX_USER_PREFIX}{uid}", {rid: now_ms})
        pipe.zremrangebyrank(_INDEX_KEY, 0, -(self._max_items + 1))
        pipe.execute()

    def get(self, request_id: str) -> Optional[dict[str, Any]]:
        raw = self._client.get(f"{_KEY_PREFIX}{request_id}")
        if not raw:
            return None
        try:
            data = json.loads(raw)
            return data if isinstance(data, dict) else None
        except Exception:  # noqa: BLE001
            return None

    def list(
        self,
        limit: int,
        offset: int,
        *,
        score_min_ms: int | None = None,
        score_max_ms: int | None = None,
        library_id: str | None = None,
        user_id: str | None = None,
    ) -> Tuple[List[dict[str, Any]], int]:
        limit = max(1, min(limit, 1000))
        offset = max(0, offset)
        index = _INDEX_KEY
        if library_id:
            index = f"{_INDEX_LIB_PREFIX}{library_id}"
        elif user_id:
            index = f"{_INDEX_USER_PREFIX}{user_id}"
        min_s = score_min_ms if score_min_ms is not None else "-inf"
        max_s = score_max_ms if score_max_ms is not None else "+inf"
        ids = self._client.zrevrangebyscore(index, max_s, min_s)
        if library_id and user_id:
            ids = [
                i
                for i in ids
                if (self.get(str(i)) or {}).get("user_id") == user_id
            ]
        total = len(ids)
        page_ids = ids[offset : offset + limit]
        out: list[dict[str, Any]] = []
        for rid in page_ids:
            rec = self.get(str(rid))
            if rec:
                out.append(rec)
        return out, total


def create_data_query_agent_trace_store(
    *,
    backend: str | None = None,
    ttl_minutes: int | None = None,
    max_items: int | None = None,
) -> DataQueryAgentTraceStore:
    from app.core.config import get_app_config

    cfg = get_app_config().data_query_agent
    backend = (backend or cfg.trace_backend or "redis").strip().lower()
    ttl = ttl_minutes if ttl_minutes is not None else int(cfg.trace_ttl_minutes)
    max_n = max_items if max_items is not None else int(cfg.trace_max_items)
    if backend == "redis":
        url = (os.getenv("REDIS_URL") or "").strip()
        if url:
            try:
                return RedisDataQueryAgentTraceStore(url, ttl_minutes=ttl, max_items=max_n)
            except Exception as exc:  # noqa: BLE001
                logger.warning("data_query_agent trace redis failed, fallback memory: %s", exc)
        else:
            logger.warning("data_query_agent TRACE_BACKEND=redis but REDIS_URL empty; memory")
    return InMemoryDataQueryAgentTraceStore(max_items=max_n)


_cached_store: DataQueryAgentTraceStore | None = None


def get_data_query_agent_trace_store() -> DataQueryAgentTraceStore:
    global _cached_store
    if _cached_store is None:
        _cached_store = create_data_query_agent_trace_store()
    return _cached_store


def reset_data_query_agent_trace_store_for_tests() -> None:
    global _cached_store
    _cached_store = InMemoryDataQueryAgentTraceStore(max_items=2000)


def save_data_query_agent_trace(record: dict[str, Any]) -> None:
    try:
        get_data_query_agent_trace_store().save(record)
    except Exception as exc:  # noqa: BLE001
        logger.warning("data_query_agent trace save failed: %s", exc)
