from __future__ import annotations

"""
综合分析智能体 trace 持久化（独立键前缀，不与现网 /analysis Trace 混读）。

后端：memory | redis | elasticsearch/easysearch（工厂失败回退）。
"""

import asyncio
import json
import os
import threading
import time
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Dict, List, Optional, Tuple

from app.core.logging import get_logger

logger = get_logger(__name__)


def _json_default(value: Any) -> Any:
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)


def _parse_iso_ms(value: str | None) -> int | None:
    if not value:
        return None
    text = str(value).strip()
    if not text:
        return None
    try:
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        dt = datetime.fromisoformat(text)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return int(dt.timestamp() * 1000)
    except Exception:  # noqa: BLE001
        return None


class AnalysisAgentTraceStore:
    """存储 analysis_agent 业务 result dict。"""

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
        analysis_type: str | None = None,
        user_id: str | None = None,
    ) -> Tuple[List[dict[str, Any]], int]:
        raise NotImplementedError


class InMemoryAnalysisAgentTraceStore(AnalysisAgentTraceStore):
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
        analysis_type: str | None = None,
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
        if analysis_type:
            rows = [x for x in rows if str(x[1][1].get("analysis_type") or "") == analysis_type]
        if user_id:
            rows = [x for x in rows if str(x[1][1].get("user_id") or "") == user_id]
        total = len(rows)
        page = rows[offset : offset + limit]
        return [dict(x[1][1]) for x in page], total


class RedisAnalysisAgentTraceStore(AnalysisAgentTraceStore):
    """Redis：JSON 正文 + ZSET 索引；键前缀 analysis_agent:trace:*。"""

    def __init__(
        self,
        redis_url: str,
        ttl_minutes: int = 1440,
        max_items: int = 10000,
    ) -> None:
        try:
            from redis import asyncio as aioredis  # type: ignore[import-not-found]
        except Exception as exc:  # noqa: BLE001
            raise RuntimeError(f"redis.asyncio not available: {exc}") from exc

        self._ttl_seconds = max(0, ttl_minutes * 60)
        self._max_items = max(1000, max_items)
        self._key_prefix = "analysis_agent:trace:"
        self._index_key = "analysis_agent:trace:index"
        self._index_type_prefix = "analysis_agent:trace:index:type:"
        self._index_user_prefix = "analysis_agent:trace:index:user:"
        self._redis = aioredis.from_url(
            redis_url,
            encoding="utf-8",
            decode_responses=True,
            socket_connect_timeout=5,
            socket_timeout=5,
        )
        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(target=self._loop.run_forever, daemon=True)
        self._thread.start()

    def _run(self, coro: Any) -> Any:
        fut = asyncio.run_coroutine_threadsafe(coro, self._loop)
        return fut.result(timeout=15)

    async def _save_async(self, record: dict[str, Any]) -> None:
        rid = str(record.get("request_id") or "").strip()
        if not rid:
            return
        now_ms = int(time.time() * 1000)
        key = f"{self._key_prefix}{rid}"
        atype = str(record.get("analysis_type") or "unknown")
        uid = str(record.get("user_id") or "").strip() or "_"
        type_key = f"{self._index_type_prefix}{atype}"
        user_key = f"{self._index_user_prefix}{uid}"
        payload = json.dumps(record, ensure_ascii=False, default=_json_default)
        pipe = self._redis.pipeline(transaction=True)
        pipe.set(key, payload)
        if self._ttl_seconds > 0:
            pipe.expire(key, self._ttl_seconds)
        pipe.zadd(self._index_key, {rid: now_ms})
        pipe.zadd(type_key, {rid: now_ms})
        pipe.zadd(user_key, {rid: now_ms})
        pipe.zremrangebyrank(self._index_key, 0, -(self._max_items + 1))
        pipe.zremrangebyrank(type_key, 0, -(self._max_items + 1))
        pipe.zremrangebyrank(user_key, 0, -(self._max_items + 1))
        await pipe.execute()

    async def _get_async(self, request_id: str) -> Optional[dict[str, Any]]:
        raw = await self._redis.get(f"{self._key_prefix}{request_id}")
        if not raw:
            return None
        try:
            data = json.loads(raw)
            return data if isinstance(data, dict) else None
        except Exception:  # noqa: BLE001
            logger.exception("RedisAnalysisAgentTraceStore parse failed request_id=%s", request_id)
            return None

    async def _list_async(
        self,
        limit: int,
        offset: int,
        *,
        score_min_ms: int | None,
        score_max_ms: int | None,
        analysis_type: str | None,
        user_id: str | None,
    ) -> Tuple[List[dict[str, Any]], int]:
        index_key = self._index_key
        if analysis_type and user_id:
            type_key = f"{self._index_type_prefix}{analysis_type}"
            user_key = f"{self._index_user_prefix}{user_id}"
            import hashlib

            digest = hashlib.md5(f"{analysis_type}:{user_id}".encode()).hexdigest()[:12]  # noqa: S324
            merged = f"analysis_agent:trace:index:tmp:{digest}"
            await self._redis.zinterstore(merged, {type_key: 1.0, user_key: 1.0}, aggregate="MAX")
            await self._redis.expire(merged, 5)
            index_key = merged
        elif analysis_type:
            index_key = f"{self._index_type_prefix}{analysis_type}"
        elif user_id:
            index_key = f"{self._index_user_prefix}{user_id}"

        min_score = score_min_ms if score_min_ms is not None else "-inf"
        max_score = score_max_ms if score_max_ms is not None else "+inf"
        total = int(await self._redis.zcount(index_key, min_score, max_score))
        ids = await self._redis.zrevrangebyscore(
            index_key, max_score, min_score, start=offset, num=limit
        )
        out: list[dict[str, Any]] = []
        for rid in ids or []:
            doc = await self._get_async(str(rid))
            if doc:
                out.append(doc)
        return out, total

    def save(self, record: dict[str, Any]) -> None:
        try:
            self._run(self._save_async(record))
        except Exception as exc:  # noqa: BLE001
            logger.error("RedisAnalysisAgentTraceStore save failed: %s", exc)

    def get(self, request_id: str) -> Optional[dict[str, Any]]:
        try:
            return self._run(self._get_async(request_id))
        except Exception as exc:  # noqa: BLE001
            logger.error("RedisAnalysisAgentTraceStore get failed: %s", exc)
            return None

    def list(
        self,
        limit: int,
        offset: int,
        *,
        score_min_ms: int | None = None,
        score_max_ms: int | None = None,
        analysis_type: str | None = None,
        user_id: str | None = None,
    ) -> Tuple[List[dict[str, Any]], int]:
        limit = max(1, min(limit, 1000))
        offset = max(0, offset)
        try:
            return self._run(
                self._list_async(
                    limit,
                    offset,
                    score_min_ms=score_min_ms,
                    score_max_ms=score_max_ms,
                    analysis_type=analysis_type,
                    user_id=user_id,
                )
            )
        except Exception as exc:  # noqa: BLE001
            logger.error("RedisAnalysisAgentTraceStore list failed: %s", exc)
            return [], 0


class ElasticsearchAnalysisAgentTraceStore(AnalysisAgentTraceStore):
    """ES/EasySearch 归档；索引独立于现网 analysis_trace_archive。"""

    def __init__(
        self,
        *,
        hosts: list[str],
        index_name: str = "analysis_agent_trace_archive",
        ttl_minutes: int = 10080,
        username: str | None = None,
        password: str | None = None,
        api_key: str | None = None,
        verify_certs: bool = False,
        request_timeout: int = 10,
    ) -> None:
        from app.services.analysis_trace_store import _build_elasticsearch_client_kwargs

        try:
            from elasticsearch import Elasticsearch  # type: ignore[import-not-found]
        except Exception as exc:  # noqa: BLE001
            raise RuntimeError(f"elasticsearch client not available: {exc}") from exc
        kwargs = _build_elasticsearch_client_kwargs(
            hosts=hosts,
            username=username,
            password=password,
            api_key=api_key,
            verify_certs=verify_certs,
            request_timeout=request_timeout,
        )
        self._es = Elasticsearch(**kwargs)
        self._index = index_name
        self._ttl_seconds = max(0, int(ttl_minutes) * 60)
        self._ensure_index()

    def _ensure_index(self) -> None:
        if self._es.indices.exists(index=self._index):
            return
        mapping = {
            "mappings": {
                "properties": {
                    "request_id": {"type": "keyword"},
                    "analysis_type": {"type": "keyword"},
                    "user_id": {"type": "keyword"},
                    "started_at_ms": {"type": "long"},
                    "saved_at_ms": {"type": "long"},
                    "expires_at_ms": {"type": "long"},
                    "doc": {"type": "object", "enabled": False},
                }
            }
        }
        self._es.indices.create(index=self._index, body=mapping, ignore=400)

    def save(self, record: dict[str, Any]) -> None:
        rid = str(record.get("request_id") or "").strip()
        if not rid:
            return
        now_ms = int(time.time() * 1000)
        started_ms = _parse_iso_ms(str(record.get("started_at") or "")) or now_ms
        expires_at_ms = now_ms + self._ttl_seconds * 1000 if self._ttl_seconds > 0 else 0
        body = {
            "request_id": rid,
            "analysis_type": str(record.get("analysis_type") or ""),
            "user_id": str(record.get("user_id") or ""),
            "started_at_ms": started_ms,
            "saved_at_ms": now_ms,
            "expires_at_ms": expires_at_ms,
            "doc": record,
        }
        try:
            self._es.index(index=self._index, id=rid, body=body, refresh=False)
        except Exception as exc:  # noqa: BLE001
            logger.error("ElasticsearchAnalysisAgentTraceStore save failed: %s", exc)

    def get(self, request_id: str) -> Optional[dict[str, Any]]:
        try:
            hit = self._es.get(index=self._index, id=request_id, ignore=404)
            if not hit or not hit.get("found"):
                return None
            src = hit.get("_source") or {}
            if self._ttl_seconds > 0:
                exp = int(src.get("expires_at_ms") or 0)
                if exp and exp < int(time.time() * 1000):
                    return None
            doc = src.get("doc")
            return dict(doc) if isinstance(doc, dict) else None
        except Exception as exc:  # noqa: BLE001
            logger.error("ElasticsearchAnalysisAgentTraceStore get failed: %s", exc)
            return None

    def list(
        self,
        limit: int,
        offset: int,
        *,
        score_min_ms: int | None = None,
        score_max_ms: int | None = None,
        analysis_type: str | None = None,
        user_id: str | None = None,
    ) -> Tuple[List[dict[str, Any]], int]:
        limit = max(1, min(limit, 1000))
        offset = max(0, offset)
        must: list[dict[str, Any]] = []
        if analysis_type:
            must.append({"term": {"analysis_type": analysis_type}})
        if user_id:
            must.append({"term": {"user_id": user_id}})
        range_q: dict[str, Any] = {}
        if score_min_ms is not None:
            range_q["gte"] = score_min_ms
        if score_max_ms is not None:
            range_q["lte"] = score_max_ms
        if range_q:
            must.append({"range": {"started_at_ms": range_q}})
        if self._ttl_seconds > 0:
            must.append(
                {
                    "bool": {
                        "should": [
                            {"term": {"expires_at_ms": 0}},
                            {"range": {"expires_at_ms": {"gte": int(time.time() * 1000)}}},
                        ],
                        "minimum_should_match": 1,
                    }
                }
            )
        query: dict[str, Any] = {"bool": {"must": must}} if must else {"match_all": {}}
        try:
            resp = self._es.search(
                index=self._index,
                body={
                    "from": offset,
                    "size": limit,
                    "sort": [{"started_at_ms": {"order": "desc"}}],
                    "query": query,
                    "track_total_hits": True,
                },
            )
            hits = ((resp or {}).get("hits") or {}).get("hits") or []
            total_obj = ((resp or {}).get("hits") or {}).get("total") or 0
            total = int(total_obj.get("value") if isinstance(total_obj, dict) else total_obj)
            out: list[dict[str, Any]] = []
            for h in hits:
                doc = ((h or {}).get("_source") or {}).get("doc")
                if isinstance(doc, dict):
                    out.append(dict(doc))
            return out, total
        except Exception as exc:  # noqa: BLE001
            logger.error("ElasticsearchAnalysisAgentTraceStore list failed: %s", exc)
            return [], 0


def create_analysis_agent_trace_store(
    *,
    backend: str | None = None,
    ttl_minutes: int | None = None,
    max_items: int | None = None,
    es_hosts: str | None = None,
    es_index: str | None = None,
) -> AnalysisAgentTraceStore:
    backend = (
        backend
        or os.getenv("ANALYSIS_AGENT_TRACE_BACKEND")
        or "memory"
    ).strip().lower()
    if backend in {"easysearch", "elasticsearch"}:
        backend = "es"
    ttl = max(
        10,
        ttl_minutes
        if ttl_minutes is not None
        else int(os.getenv("ANALYSIS_AGENT_TRACE_TTL_MINUTES", "1440")),
    )
    max_items = max(
        100,
        max_items
        if max_items is not None
        else int(os.getenv("ANALYSIS_AGENT_TRACE_MAX_ITEMS", "5000")),
    )
    redis_url = (os.getenv("REDIS_URL") or "").strip()

    if backend == "es":
        hosts_raw = (
            es_hosts
            or os.getenv("ANALYSIS_AGENT_TRACE_ES_HOSTS")
            or os.getenv("ANALYSIS_TRACE_ES_HOSTS")
            or os.getenv("RAG_ES_HOSTS")
            or "http://localhost:9200"
        )
        hosts = [x.strip() for x in hosts_raw.split(",") if x.strip()]
        index_name = (
            es_index
            or os.getenv("ANALYSIS_AGENT_TRACE_ES_INDEX")
            or "analysis_agent_trace_archive"
        ).strip()
        try:
            return ElasticsearchAnalysisAgentTraceStore(
                hosts=hosts,
                index_name=index_name,
                ttl_minutes=ttl,
                username=os.getenv("ANALYSIS_AGENT_TRACE_ES_USERNAME")
                or os.getenv("ANALYSIS_TRACE_ES_USERNAME")
                or os.getenv("RAG_ES_USERNAME")
                or None,
                password=os.getenv("ANALYSIS_AGENT_TRACE_ES_PASSWORD")
                or os.getenv("ANALYSIS_TRACE_ES_PASSWORD")
                or os.getenv("RAG_ES_PASSWORD")
                or None,
                api_key=os.getenv("ANALYSIS_AGENT_TRACE_ES_API_KEY")
                or os.getenv("ANALYSIS_TRACE_ES_API_KEY")
                or os.getenv("RAG_ES_API_KEY")
                or None,
                verify_certs=(
                    os.getenv("ANALYSIS_AGENT_TRACE_ES_VERIFY_CERTS")
                    or os.getenv("ANALYSIS_TRACE_ES_VERIFY_CERTS")
                    or "false"
                ).lower()
                == "true",
            )
        except Exception as exc:  # noqa: BLE001
            logger.error(
                "init ElasticsearchAnalysisAgentTraceStore failed, fallback redis/memory: %s",
                exc,
            )

    if backend == "memory" or not redis_url:
        if backend == "redis" and not redis_url:
            logger.warning("ANALYSIS_AGENT_TRACE_BACKEND=redis but REDIS_URL empty; using memory")
        return InMemoryAnalysisAgentTraceStore(max_items=max_items)

    try:
        return RedisAnalysisAgentTraceStore(
            redis_url=redis_url, ttl_minutes=ttl, max_items=max_items
        )
    except Exception as exc:  # noqa: BLE001
        logger.error("init RedisAnalysisAgentTraceStore failed, fallback memory: %s", exc)
        return InMemoryAnalysisAgentTraceStore(max_items=max_items)
