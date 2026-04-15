from __future__ import annotations

"""
综合分析 trace 持久化抽象与实现：内存、Redis（索引 + TTL）、Elasticsearch/EasySearch 归档。

工厂方法 `create_analysis_trace_store` 按 `ANALYSIS_TRACE_BACKEND` 选型并在失败时回退。
"""

import asyncio
import hashlib
import json
import os
import threading
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from app.core.logging import get_logger
from app.core.metrics import ANALYSIS_TRACE_INDEX_CLEANUP_COUNT
from app.models.analysis import AnalysisV2Result

logger = get_logger(__name__)


def _build_elasticsearch_client_kwargs(
    *,
    hosts: list[str],
    username: str | None,
    password: str | None,
    api_key: str | None,
    verify_certs: bool,
    request_timeout: int,
) -> dict[str, Any]:
    """
    与 RAG 侧 `ElasticsearchVectorStore._create_client` 对齐：
    - 同一套 `elasticsearch` Python 客户端连接 **Elasticsearch 或 EasySearch**（后者兼容 ES REST API）；
    - 7.x 使用 `http_auth`，8.x 使用 `basic_auth`，避免认证参数不生效。
    """
    auth = None
    if username and password:
        auth = (username, password)
    kwargs: dict[str, Any] = dict(hosts=hosts, verify_certs=verify_certs, request_timeout=request_timeout)
    if api_key:
        kwargs["api_key"] = api_key
    try:
        import elasticsearch as es_module  # type: ignore[import-untyped]
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError(f"elasticsearch client not available: {exc}") from exc
    version = getattr(es_module, "__version__", (0, 0, 0))
    major = int(version[0]) if isinstance(version, (tuple, list)) and version else 0
    # API Key 与 Basic 并存时优先 API Key（与 RAG 侧常见配置一致）
    if auth is not None and not api_key:
        if major >= 8:
            kwargs["basic_auth"] = auth  # type: ignore[assignment]
        else:
            kwargs["http_auth"] = auth  # type: ignore[assignment]
    return kwargs


class AnalysisTraceStore:
    """trace 存储抽象：保存完整 `AnalysisV2Result` 并支持按条件列举。"""

    def save(self, result: AnalysisV2Result) -> None:  # pragma: no cover - interface
        raise NotImplementedError

    def get(self, request_id: str) -> Optional[AnalysisV2Result]:  # pragma: no cover - interface
        raise NotImplementedError

    def list(  # pragma: no cover - interface
        self,
        limit: int,
        offset: int,
        *,
        score_min_ms: int | None = None,
        score_max_ms: int | None = None,
        analysis_type: str | None = None,
        data_mode: str | None = None,
    ) -> Tuple[List[AnalysisV2Result], int]:
        raise NotImplementedError


class InMemoryAnalysisTraceStore(AnalysisTraceStore):
    """进程内 dict 存储，适合开发或 Redis/ES 不可用时的回退。"""

    def __init__(self, max_items: int = 2000) -> None:
        self._max_items = max(100, max_items)
        self._data: Dict[str, tuple[float, AnalysisV2Result]] = {}
        self._lock = threading.Lock()

    def save(self, result: AnalysisV2Result) -> None:
        now = time.time()
        with self._lock:
            self._data[result.request_id] = (now, result)
            if len(self._data) > self._max_items:
                items = sorted(self._data.items(), key=lambda kv: kv[1][0], reverse=True)
                keep = dict(items[: self._max_items])
                self._data = keep

    def get(self, request_id: str) -> Optional[AnalysisV2Result]:
        with self._lock:
            hit = self._data.get(request_id)
        return None if hit is None else hit[1]

    def list(
        self,
        limit: int,
        offset: int,
        *,
        score_min_ms: int | None = None,
        score_max_ms: int | None = None,
        analysis_type: str | None = None,
        data_mode: str | None = None,
    ) -> Tuple[List[AnalysisV2Result], int]:
        limit = max(1, min(limit, 1000))
        offset = max(0, offset)
        with self._lock:
            rows = sorted(self._data.items(), key=lambda kv: kv[1][0], reverse=True)
        if score_min_ms is not None or score_max_ms is not None:
            min_s = (score_min_ms / 1000.0) if score_min_ms is not None else float("-inf")
            max_s = (score_max_ms / 1000.0) if score_max_ms is not None else float("inf")
            rows = [x for x in rows if min_s <= x[1][0] <= max_s]
        if analysis_type:
            rows = [x for x in rows if x[1][1].analysis_type == analysis_type]
        if data_mode:
            rows = [x for x in rows if str(x[1][1].evidence.data_coverage.get("mode", "payload")) == data_mode]
        total = len(rows)
        page = rows[offset : offset + limit]
        return [x[1][1] for x in page], total


class RedisAnalysisTraceStore(AnalysisTraceStore):
    """Redis 存全文 JSON + ZSET 二级索引；在独立线程事件循环中执行异步 IO。"""

    def __init__(
        self,
        redis_url: str,
        ttl_minutes: int = 1440,
        max_items: int = 10000,
        lazy_cleanup_batch_size: int = 200,
    ) -> None:
        try:
            from redis import asyncio as aioredis  # type: ignore[import-not-found]
        except Exception as exc:  # noqa: BLE001
            raise RuntimeError(f"redis.asyncio not available: {exc}") from exc

        self._ttl_seconds = max(0, ttl_minutes * 60)
        self._max_items = max(1000, max_items)
        self._key_prefix = "analysis:trace:"
        self._index_key = "analysis:trace:index"
        self._index_type_prefix = "analysis:trace:index:type:"
        self._index_mode_prefix = "analysis:trace:index:mode:"
        self._lazy_cleanup_batch_size = max(20, lazy_cleanup_batch_size)

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

    async def _save_async(self, result: AnalysisV2Result) -> None:
        now_ms = int(time.time() * 1000)
        key = f"{self._key_prefix}{result.request_id}"
        type_key = f"{self._index_type_prefix}{result.analysis_type}"
        mode = str(result.evidence.data_coverage.get("mode", "payload"))
        mode_key = f"{self._index_mode_prefix}{mode}"
        payload = json.dumps(result.model_dump(), ensure_ascii=False)
        pipe = self._redis.pipeline(transaction=True)
        pipe.set(key, payload)
        if self._ttl_seconds > 0:
            pipe.expire(key, self._ttl_seconds)
        pipe.zadd(self._index_key, {result.request_id: now_ms})
        pipe.zadd(type_key, {result.request_id: now_ms})
        pipe.zadd(mode_key, {result.request_id: now_ms})
        pipe.zremrangebyrank(self._index_key, 0, -(self._max_items + 1))
        pipe.zremrangebyrank(type_key, 0, -(self._max_items + 1))
        pipe.zremrangebyrank(mode_key, 0, -(self._max_items + 1))
        await pipe.execute()

    async def _resolve_index_key(self, analysis_type: str | None, data_mode: str | None) -> str:
        if analysis_type and data_mode:
            type_key = f"{self._index_type_prefix}{analysis_type}"
            mode_key = f"{self._index_mode_prefix}{data_mode}"
            digest = hashlib.md5(f"{analysis_type}:{data_mode}".encode("utf-8")).hexdigest()[:12]  # noqa: S324
            merged = f"analysis:trace:index:tmp:{digest}"
            await self._redis.zinterstore(merged, {type_key: 1.0, mode_key: 1.0}, aggregate="MAX")
            await self._redis.expire(merged, 5)
            return merged
        if analysis_type:
            return f"{self._index_type_prefix}{analysis_type}"
        if data_mode:
            return f"{self._index_mode_prefix}{data_mode}"
        return self._index_key

    def save(self, result: AnalysisV2Result) -> None:
        fut = asyncio.run_coroutine_threadsafe(self._save_async(result), self._loop)
        try:
            fut.result(timeout=3.0)
        except Exception as exc:  # noqa: BLE001
            logger.error("RedisAnalysisTraceStore save failed: %s", exc)

    async def _get_async(self, request_id: str) -> Optional[AnalysisV2Result]:
        key = f"{self._key_prefix}{request_id}"
        raw = await self._redis.get(key)
        if not raw:
            return None
        try:
            obj = json.loads(raw)
            return AnalysisV2Result.model_validate(obj)
        except Exception:  # noqa: BLE001
            logger.exception("RedisAnalysisTraceStore parse failed request_id=%s", request_id)
            return None

    def get(self, request_id: str) -> Optional[AnalysisV2Result]:
        fut = asyncio.run_coroutine_threadsafe(self._get_async(request_id), self._loop)
        try:
            return fut.result(timeout=3.0)
        except Exception as exc:  # noqa: BLE001
            logger.error("RedisAnalysisTraceStore get failed: %s", exc)
            return None

    async def _list_async(
        self,
        limit: int,
        offset: int,
        *,
        score_min_ms: int | None = None,
        score_max_ms: int | None = None,
        analysis_type: str | None = None,
        data_mode: str | None = None,
    ) -> Tuple[List[AnalysisV2Result], int]:
        limit = max(1, min(limit, 1000))
        offset = max(0, offset)
        index_key = await self._resolve_index_key(analysis_type=analysis_type, data_mode=data_mode)
        min_score = score_min_ms if score_min_ms is not None else "-inf"
        max_score = score_max_ms if score_max_ms is not None else "+inf"
        total = int(await self._redis.zcount(index_key, min_score, max_score) or 0)
        ids = await self._redis.zrevrangebyscore(
            index_key,
            max_score,
            min_score,
            start=offset,
            num=limit,
        )
        if not ids:
            return [], total
        pipe = self._redis.pipeline(transaction=False)
        for rid in ids:
            pipe.get(f"{self._key_prefix}{rid}")
        raws = await pipe.execute()
        items: List[AnalysisV2Result] = []
        stale_ids: List[str] = []
        for rid, raw in zip(ids, raws or []):
            if not raw:
                stale_ids.append(str(rid))
                continue
            try:
                obj = json.loads(raw)
                items.append(AnalysisV2Result.model_validate(obj))
            except Exception:  # noqa: BLE001
                logger.exception("RedisAnalysisTraceStore list parse failed")
        if stale_ids:
            await self._lazy_cleanup_orphans(index_key=index_key, request_ids=stale_ids)
        return items, total

    async def _lazy_cleanup_orphans(self, *, index_key: str, request_ids: List[str]) -> None:
        stale = request_ids[: self._lazy_cleanup_batch_size]
        if not stale:
            return
        try:
            pipe = self._redis.pipeline(transaction=False)
            pipe.zrem(self._index_key, *stale)
            if index_key != self._index_key:
                pipe.zrem(index_key, *stale)
            await pipe.execute()
            ANALYSIS_TRACE_INDEX_CLEANUP_COUNT.labels(index_type="main").inc(len(stale))
            if index_key != self._index_key:
                ANALYSIS_TRACE_INDEX_CLEANUP_COUNT.labels(index_type="filtered").inc(len(stale))
        except Exception as exc:  # noqa: BLE001
            logger.warning("RedisAnalysisTraceStore lazy cleanup failed, index=%s, size=%s, err=%s", index_key, len(stale), exc)

    def list(
        self,
        limit: int,
        offset: int,
        *,
        score_min_ms: int | None = None,
        score_max_ms: int | None = None,
        analysis_type: str | None = None,
        data_mode: str | None = None,
    ) -> Tuple[List[AnalysisV2Result], int]:
        fut = asyncio.run_coroutine_threadsafe(
            self._list_async(
                limit,
                offset,
                score_min_ms=score_min_ms,
                score_max_ms=score_max_ms,
                analysis_type=analysis_type,
                data_mode=data_mode,
            ),
            self._loop,
        )
        try:
            return fut.result(timeout=5.0)
        except Exception as exc:  # noqa: BLE001
            logger.error("RedisAnalysisTraceStore list failed: %s", exc)
            return [], 0


class ElasticsearchAnalysisTraceStore(AnalysisTraceStore):
    """
    使用 Elasticsearch 兼容 REST API 的集群保存 trace 全文，便于长期审计与检索。

    **EasySearch**：与 RAG 向量库一致，EasySearch 兼容 ES HTTP API，本实现使用同一
    `elasticsearch` 官方客户端与 `hosts + basic_auth/http_auth + verify_certs` 连接方式；
    将 `ANALYSIS_TRACE_ES_HOSTS` 指向 EasySearch 地址即可（可与 `RAG_ES_HOSTS` 相同或独立索引）。
    """

    def __init__(
        self,
        *,
        hosts: list[str],
        index_name: str = "analysis_trace_archive",
        ttl_minutes: int = 10080,
        username: str | None = None,
        password: str | None = None,
        api_key: str | None = None,
        verify_certs: bool = False,
        request_timeout: int = 10,
    ) -> None:
        try:
            from elasticsearch import Elasticsearch  # type: ignore[import-not-found]
        except Exception as exc:  # noqa: BLE001
            raise RuntimeError(f"elasticsearch client not available: {exc}") from exc
        client_kwargs = _build_elasticsearch_client_kwargs(
            hosts=hosts,
            username=username,
            password=password,
            api_key=api_key,
            verify_certs=verify_certs,
            request_timeout=request_timeout,
        )
        self._es = Elasticsearch(**client_kwargs)
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
                    "data_mode": {"type": "keyword"},
                    "started_at_ms": {"type": "long"},
                    "saved_at_ms": {"type": "long"},
                    "expires_at_ms": {"type": "long"},
                    "doc": {"type": "object", "enabled": False},
                }
            }
        }
        self._es.indices.create(index=self._index, body=mapping, ignore=400)

    @staticmethod
    def _extract_started_at_ms(result: AnalysisV2Result) -> int:
        raw = str(result.trace.execution_summary.get("started_at", ""))
        if raw:
            try:
                if raw.endswith("Z"):
                    raw = raw[:-1] + "+00:00"
                dt = datetime.fromisoformat(raw)
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                return int(dt.timestamp() * 1000)
            except Exception:  # noqa: BLE001
                pass
        return int(time.time() * 1000)

    def save(self, result: AnalysisV2Result) -> None:
        now_ms = int(time.time() * 1000)
        expires_at_ms = now_ms + self._ttl_seconds * 1000 if self._ttl_seconds > 0 else 0
        body = {
            "request_id": result.request_id,
            "analysis_type": result.analysis_type,
            "data_mode": str(result.evidence.data_coverage.get("mode", "payload")),
            "started_at_ms": self._extract_started_at_ms(result),
            "saved_at_ms": now_ms,
            "expires_at_ms": expires_at_ms,
            "doc": result.model_dump(),
        }
        try:
            self._es.index(index=self._index, id=result.request_id, body=body, refresh=False)
        except Exception as exc:  # noqa: BLE001
            logger.error("ElasticsearchAnalysisTraceStore save failed: %s", exc)

    def _decode_hit(self, hit: dict[str, Any]) -> Optional[AnalysisV2Result]:
        src = hit.get("_source") or {}
        expires_at_ms = int(src.get("expires_at_ms") or 0)
        if expires_at_ms > 0 and expires_at_ms < int(time.time() * 1000):
            return None
        doc = src.get("doc")
        if not isinstance(doc, dict):
            return None
        try:
            return AnalysisV2Result.model_validate(doc)
        except Exception:  # noqa: BLE001
            logger.exception("ElasticsearchAnalysisTraceStore decode failed id=%s", hit.get("_id"))
            return None

    def get(self, request_id: str) -> Optional[AnalysisV2Result]:
        try:
            res = self._es.get(index=self._index, id=request_id, ignore=404)
            if not res or not res.get("found"):
                return None
            return self._decode_hit(res)
        except Exception as exc:  # noqa: BLE001
            logger.error("ElasticsearchAnalysisTraceStore get failed: %s", exc)
            return None

    def list(
        self,
        limit: int,
        offset: int,
        *,
        score_min_ms: int | None = None,
        score_max_ms: int | None = None,
        analysis_type: str | None = None,
        data_mode: str | None = None,
    ) -> Tuple[List[AnalysisV2Result], int]:
        limit = max(1, min(limit, 1000))
        offset = max(0, offset)
        must: list[dict[str, Any]] = []
        filters: list[dict[str, Any]] = []
        if analysis_type:
            filters.append({"term": {"analysis_type": analysis_type}})
        if data_mode:
            filters.append({"term": {"data_mode": data_mode}})
        if score_min_ms is not None or score_max_ms is not None:
            rg: dict[str, Any] = {}
            if score_min_ms is not None:
                rg["gte"] = score_min_ms
            if score_max_ms is not None:
                rg["lte"] = score_max_ms
            filters.append({"range": {"started_at_ms": rg}})
        now_ms = int(time.time() * 1000)
        filters.append(
            {
                "bool": {
                    "should": [
                        {"term": {"expires_at_ms": 0}},
                        {"range": {"expires_at_ms": {"gte": now_ms}}},
                    ],
                    "minimum_should_match": 1,
                }
            }
        )
        query = {"bool": {"must": must, "filter": filters}}
        try:
            res = self._es.search(
                index=self._index,
                body={
                    "query": query,
                    "from": offset,
                    "size": limit,
                    "sort": [{"started_at_ms": {"order": "desc"}}],
                },
            )
            total_obj = (res.get("hits") or {}).get("total") or 0
            if isinstance(total_obj, dict):
                total = int(total_obj.get("value", 0))
            else:
                total = int(total_obj)
            hits = (res.get("hits") or {}).get("hits") or []
            out: list[AnalysisV2Result] = []
            for hit in hits:
                x = self._decode_hit(hit)
                if x is not None:
                    out.append(x)
            return out, total
        except Exception as exc:  # noqa: BLE001
            logger.error("ElasticsearchAnalysisTraceStore list failed: %s", exc)
            return [], 0


def create_analysis_trace_store(
    *,
    backend: str | None = None,
    ttl_minutes: int | None = None,
    max_items: int | None = None,
    lazy_cleanup_batch_size: int | None = None,
    es_hosts: str | None = None,
    es_index: str | None = None,
    es_verify_certs: bool | None = None,
    es_timeout_seconds: int | None = None,
    es_username: str | None = None,
    es_password: str | None = None,
    es_api_key: str | None = None,
) -> AnalysisTraceStore:
    """按 backend 构造 trace 存储；`es` 初始化失败时回退 Redis 或内存。"""
    backend = (backend or os.getenv("ANALYSIS_TRACE_BACKEND", "redis") or "redis").lower()
    # EasySearch 与 ES 共用 REST 客户端与索引 API，配置层别名便于运维理解
    if backend in {"easysearch", "elasticsearch"}:
        backend = "es"
    ttl = max(10, ttl_minutes if ttl_minutes is not None else int(os.getenv("ANALYSIS_TRACE_TTL_MINUTES", "1440")))
    max_items = max(100, max_items if max_items is not None else int(os.getenv("ANALYSIS_TRACE_MAX_ITEMS", "10000")))
    lazy_cleanup = max(
        20,
        lazy_cleanup_batch_size
        if lazy_cleanup_batch_size is not None
        else int(os.getenv("ANALYSIS_TRACE_LAZY_CLEANUP_BATCH_SIZE", "200")),
    )
    redis_url = os.getenv("REDIS_URL")
    if backend == "es":
        hosts_raw = es_hosts or os.getenv("ANALYSIS_TRACE_ES_HOSTS") or os.getenv("RAG_ES_HOSTS") or "http://localhost:9200"
        hosts = [x.strip() for x in hosts_raw.split(",") if x.strip()]
        index_name = (es_index or os.getenv("ANALYSIS_TRACE_ES_INDEX") or "analysis_trace_archive").strip()
        verify_certs = (
            es_verify_certs
            if es_verify_certs is not None
            else (os.getenv("ANALYSIS_TRACE_ES_VERIFY_CERTS", "false").lower() == "true")
        )
        timeout = max(1, int(es_timeout_seconds or os.getenv("ANALYSIS_TRACE_ES_TIMEOUT_SECONDS", "10")))
        username = es_username or os.getenv("ANALYSIS_TRACE_ES_USERNAME") or os.getenv("RAG_ES_USERNAME") or None
        password = es_password or os.getenv("ANALYSIS_TRACE_ES_PASSWORD") or os.getenv("RAG_ES_PASSWORD") or None
        api_key = es_api_key or os.getenv("ANALYSIS_TRACE_ES_API_KEY") or os.getenv("RAG_ES_API_KEY") or None
        try:
            return ElasticsearchAnalysisTraceStore(
                hosts=hosts,
                index_name=index_name,
                ttl_minutes=ttl,
                username=username,
                password=password,
                api_key=api_key,
                verify_certs=verify_certs,
                request_timeout=timeout,
            )
        except Exception as exc:  # noqa: BLE001
            logger.error("init ElasticsearchAnalysisTraceStore failed, fallback to redis/memory: %s", exc)
    if backend == "memory" or not redis_url:
        return InMemoryAnalysisTraceStore(max_items=max_items)
    try:
        return RedisAnalysisTraceStore(
            redis_url=redis_url,
            ttl_minutes=ttl,
            max_items=max_items,
            lazy_cleanup_batch_size=lazy_cleanup,
        )
    except Exception as exc:  # noqa: BLE001
        logger.error("init RedisAnalysisTraceStore failed, fallback to memory: %s", exc)
        return InMemoryAnalysisTraceStore(max_items=max_items)
