from __future__ import annotations

import hashlib
import json
import os
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from app.conversation.session_catalog import display_title
from app.core.logging import get_logger

logger = get_logger(__name__)

_archive_lock = threading.Lock()
_archive_singleton: Optional["ConversationArchiveStore"] = None


def _build_elasticsearch_client_kwargs(
    *,
    hosts: list[str],
    username: str | None,
    password: str | None,
    api_key: str | None,
    verify_certs: bool,
    request_timeout: int,
) -> dict[str, Any]:
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
    if auth is not None and not api_key:
        if major >= 8:
            kwargs["basic_auth"] = auth  # type: ignore[assignment]
        else:
            kwargs["http_auth"] = auth  # type: ignore[assignment]
    return kwargs


class ConversationArchiveStore:
    """
    会话冷层归档（EasySearch）+ 可选对象存储备份。

    - 冷层消息索引：conversation_messages_v1（可配置）；
    - 冷层会话索引：conversation_sessions_v1（可配置，便于会话目录回查）；
    - 对象存储备份：支持 local 文件目录，或 S3 兼容对象存储（boto3 可选依赖）。
    """

    def __init__(self) -> None:
        self._enabled = os.getenv("CONV_ARCHIVE_ENABLED", "false").lower() == "true"
        self._fallback = os.getenv("CONV_QUERY_FALLBACK_COLD", "true").lower() == "true"
        self._msg_index = os.getenv("CONV_ARCHIVE_ES_INDEX", "conversation_messages_v1").strip() or "conversation_messages_v1"
        self._session_index = (
            os.getenv("CONV_ARCHIVE_ES_SESSIONS_INDEX", "conversation_sessions_v1").strip() or "conversation_sessions_v1"
        )
        self._max_query_limit = max(1, min(5000, int(os.getenv("CONV_ARCHIVE_QUERY_MAX_LIMIT", "2000"))))
        self._object_enabled = os.getenv("CONV_ARCHIVE_OBJECT_ENABLED", "false").lower() == "true"
        self._object_backend = (os.getenv("CONV_ARCHIVE_OBJECT_BACKEND", "local") or "local").strip().lower()
        self._object_local_dir = Path(os.getenv("CONV_ARCHIVE_OBJECT_LOCAL_DIR", "./data/conversation_archive"))

        self._es = None
        if self._enabled:
            self._init_es()
        self._s3_client = None
        if self._enabled and self._object_enabled and self._object_backend == "s3":
            self._init_s3()

    @property
    def enabled(self) -> bool:
        return self._enabled and self._es is not None

    @property
    def fallback_enabled(self) -> bool:
        return self.enabled and self._fallback

    def _init_es(self) -> None:
        hosts_raw = os.getenv("CONV_ARCHIVE_ES_HOSTS") or os.getenv("RAG_ES_HOSTS") or "http://localhost:9200"
        hosts = [x.strip() for x in hosts_raw.split(",") if x.strip()]
        username = os.getenv("CONV_ARCHIVE_ES_USERNAME") or os.getenv("RAG_ES_USERNAME") or None
        password = os.getenv("CONV_ARCHIVE_ES_PASSWORD") or os.getenv("RAG_ES_PASSWORD") or None
        api_key = os.getenv("CONV_ARCHIVE_ES_API_KEY") or os.getenv("RAG_ES_API_KEY") or None
        verify_certs = os.getenv("CONV_ARCHIVE_ES_VERIFY_CERTS", os.getenv("RAG_ES_VERIFY_CERTS", "false")).lower() == "true"
        timeout = max(1, int(os.getenv("CONV_ARCHIVE_ES_TIMEOUT_SECONDS", "10")))
        try:
            from elasticsearch import Elasticsearch  # type: ignore[import-not-found]

            kwargs = _build_elasticsearch_client_kwargs(
                hosts=hosts,
                username=username,
                password=password,
                api_key=api_key,
                verify_certs=verify_certs,
                request_timeout=timeout,
            )
            self._es = Elasticsearch(**kwargs)
            self._ensure_indexes()
        except Exception as exc:  # noqa: BLE001
            logger.error("ConversationArchiveStore init ES failed, archive disabled: %s", exc)
            self._es = None

    def _init_s3(self) -> None:
        try:
            import boto3  # type: ignore[import-not-found]
        except Exception as exc:  # noqa: BLE001
            logger.error("ConversationArchiveStore s3 backup disabled: boto3 unavailable: %s", exc)
            return
        endpoint = os.getenv("CONV_ARCHIVE_OBJECT_S3_ENDPOINT")
        access_key = os.getenv("CONV_ARCHIVE_OBJECT_S3_ACCESS_KEY")
        secret_key = os.getenv("CONV_ARCHIVE_OBJECT_S3_SECRET_KEY")
        region = os.getenv("CONV_ARCHIVE_OBJECT_S3_REGION") or "us-east-1"
        try:
            self._s3_client = boto3.client(
                "s3",
                endpoint_url=endpoint or None,
                aws_access_key_id=access_key or None,
                aws_secret_access_key=secret_key or None,
                region_name=region,
            )
        except Exception as exc:  # noqa: BLE001
            logger.error("ConversationArchiveStore s3 backup init failed: %s", exc)
            self._s3_client = None

    def _ensure_indexes(self) -> None:
        if self._es is None:
            return
        msg_mapping = {
            "mappings": {
                "properties": {
                    "message_id": {"type": "keyword"},
                    "user_id": {"type": "keyword"},
                    "session_id": {"type": "keyword"},
                    "role": {"type": "keyword"},
                    "content": {"type": "text"},
                    "content_kw": {"type": "keyword", "ignore_above": 8191},
                    "ts_ms": {"type": "long"},
                    "title_snapshot": {"type": "keyword"},
                    "title_source_snapshot": {"type": "keyword"},
                    "meta": {"type": "object", "enabled": False},
                    "archived_at_ms": {"type": "long"},
                }
            }
        }
        sess_mapping = {
            "mappings": {
                "properties": {
                    "session_doc_id": {"type": "keyword"},
                    "user_id": {"type": "keyword"},
                    "session_id": {"type": "keyword"},
                    "last_activity_at_ms": {"type": "long"},
                    "title": {"type": "keyword"},
                    "title_source": {"type": "keyword"},
                    "message_count": {"type": "long"},
                    "updated_at_ms": {"type": "long"},
                }
            }
        }
        self._es.indices.create(index=self._msg_index, body=msg_mapping, ignore=400)
        self._es.indices.create(index=self._session_index, body=sess_mapping, ignore=400)

    @staticmethod
    def _to_ms(ts: float | int | None) -> int:
        if ts is None:
            return int(time.time() * 1000)
        t = float(ts)
        if t > 10_000_000_000:
            return int(t)
        return int(t * 1000)

    @staticmethod
    def _message_id(user_id: str, session_id: str, role: str, content: str, ts_ms: int) -> str:
        raw = f"{user_id}|{session_id}|{role}|{ts_ms}|{content}"
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    @staticmethod
    def _session_doc_id(user_id: str, session_id: str) -> str:
        return hashlib.sha256(f"{user_id}|{session_id}".encode("utf-8")).hexdigest()

    def archive_message(
        self,
        *,
        user_id: str,
        session_id: str,
        role: str,
        content: str,
        ts: float | int | None,
        title: str = "",
        title_source: str = "off",
        meta: Optional[Dict[str, Any]] = None,
    ) -> None:
        if not self.enabled:
            return
        ts_ms = self._to_ms(ts)
        now_ms = int(time.time() * 1000)
        msg_id = self._message_id(user_id, session_id, role, content, ts_ms)
        body = {
            "message_id": msg_id,
            "user_id": user_id,
            "session_id": session_id,
            "role": role,
            "content": content,
            "content_kw": content[:8191],
            "ts_ms": ts_ms,
            "title_snapshot": title,
            "title_source_snapshot": title_source,
            "meta": meta or {},
            "archived_at_ms": now_ms,
        }
        sess_id = self._session_doc_id(user_id, session_id)
        sess_doc = {
            "session_doc_id": sess_id,
            "user_id": user_id,
            "session_id": session_id,
            "last_activity_at_ms": ts_ms,
            "title": title,
            "title_source": title_source,
            "updated_at_ms": now_ms,
        }
        try:
            self._es.index(index=self._msg_index, id=msg_id, body=body, refresh=False)
            script = {
                "source": (
                    "ctx._source.last_activity_at_ms = params.last;"
                    "ctx._source.updated_at_ms = params.now;"
                    "ctx._source.title = params.title;"
                    "ctx._source.title_source = params.title_source;"
                    "if (ctx._source.message_count == null) {ctx._source.message_count = 0;} "
                    "ctx._source.message_count += 1;"
                ),
                "lang": "painless",
                "params": {
                    "last": ts_ms,
                    "now": now_ms,
                    "title": title,
                    "title_source": title_source,
                },
            }
            self._es.update(
                index=self._session_index,
                id=sess_id,
                body={
                    "script": script,
                    "upsert": {
                        **sess_doc,
                        "message_count": 1,
                    },
                },
                retry_on_conflict=3,
            )
            self._backup_object(body)
        except Exception as exc:  # noqa: BLE001
            logger.error("ConversationArchiveStore archive_message failed: %s", exc)

    def update_session_title(
        self,
        *,
        user_id: str,
        session_id: str,
        title: str,
        title_source: str = "user",
        require_existing: bool = False,
    ) -> bool:
        if not self.enabled:
            return False
        sess_id = self._session_doc_id(user_id, session_id)
        now_ms = int(time.time() * 1000)
        try:
            if require_existing:
                exists = False
                msg_probe = self._es.search(
                    index=self._msg_index,
                    body={
                        "query": {
                            "bool": {
                                "filter": [
                                    {"term": {"user_id": user_id}},
                                    {"term": {"session_id": session_id}},
                                ]
                            }
                        },
                        "size": 1,
                    },
                )
                msg_hits = (msg_probe.get("hits") or {}).get("hits") or []
                if msg_hits:
                    exists = True
                if not exists:
                    sess_probe = self._es.get(index=self._session_index, id=sess_id, ignore=404)
                    exists = bool(sess_probe and sess_probe.get("found"))
                if not exists:
                    return False
            self._es.update(
                index=self._session_index,
                id=sess_id,
                body={
                    "script": {
                        "source": (
                            "ctx._source.title = params.title;"
                            "ctx._source.title_source = params.title_source;"
                            "ctx._source.updated_at_ms = params.now;"
                        ),
                        "lang": "painless",
                        "params": {"title": title, "title_source": title_source, "now": now_ms},
                    },
                    "upsert": {
                        "session_doc_id": sess_id,
                        "user_id": user_id,
                        "session_id": session_id,
                        "last_activity_at_ms": now_ms,
                        "title": title,
                        "title_source": title_source,
                        "message_count": 0,
                        "updated_at_ms": now_ms,
                    },
                },
                retry_on_conflict=3,
            )
            return True
        except Exception as exc:  # noqa: BLE001
            logger.error("ConversationArchiveStore update_session_title failed: %s", exc)
            return False

    def list_sessions(self, *, user_id: str, limit: int, offset: int, order_desc: bool = True) -> Tuple[List[Dict[str, Any]], int]:
        if not self.fallback_enabled:
            return [], 0
        limit = max(1, min(limit, self._max_query_limit))
        offset = max(0, offset)
        order = "desc" if order_desc else "asc"
        try:
            res = self._es.search(
                index=self._session_index,
                body={
                    "query": {"term": {"user_id": user_id}},
                    "from": offset,
                    "size": limit,
                    "sort": [{"last_activity_at_ms": {"order": order}}],
                },
            )
            total_obj = (res.get("hits") or {}).get("total") or 0
            total = int(total_obj.get("value", 0)) if isinstance(total_obj, dict) else int(total_obj)
            hits = (res.get("hits") or {}).get("hits") or []
            out: List[Dict[str, Any]] = []
            for h in hits:
                src = h.get("_source") or {}
                sid = str(src.get("session_id") or "")
                title = str(src.get("title") or "")
                title_source = str(src.get("title_source") or "off")
                disp = display_title({"title": title, "title_source": title_source}, sid)
                out.append(
                    {
                        "session_id": sid,
                        "title": disp,
                        "title_source": title_source,
                        "last_activity_at": int(src.get("last_activity_at_ms") or 0),
                        "message_count": int(src.get("message_count") or 0),
                    }
                )
            return out, total
        except Exception as exc:  # noqa: BLE001
            logger.error("ConversationArchiveStore list_sessions failed: %s", exc)
            return [], 0

    def list_messages(self, *, user_id: str, session_id: str, limit: int | None = None) -> List[Dict[str, Any]]:
        if not self.fallback_enabled:
            return []
        size = self._max_query_limit if limit is None else max(1, min(limit, self._max_query_limit))
        try:
            res = self._es.search(
                index=self._msg_index,
                body={
                    "query": {
                        "bool": {
                            "filter": [
                                {"term": {"user_id": user_id}},
                                {"term": {"session_id": session_id}},
                            ]
                        }
                    },
                    "size": size,
                    "sort": [{"ts_ms": {"order": "asc"}}],
                },
            )
            hits = (res.get("hits") or {}).get("hits") or []
            out: List[Dict[str, Any]] = []
            for h in hits:
                src = h.get("_source") or {}
                out.append(
                    {
                        "role": str(src.get("role") or ""),
                        "content": str(src.get("content") or ""),
                        "ts": float(src.get("ts_ms", 0)) / 1000.0 if src.get("ts_ms") is not None else None,
                    }
                )
            return out
        except Exception as exc:  # noqa: BLE001
            logger.error("ConversationArchiveStore list_messages failed: %s", exc)
            return []

    def get_session_title_snapshot(self, *, user_id: str, session_id: str) -> Dict[str, str]:
        if not self.fallback_enabled:
            return {}
        sess_id = self._session_doc_id(user_id, session_id)
        try:
            res = self._es.get(index=self._session_index, id=sess_id, ignore=404)
            if not res or not res.get("found"):
                return {}
            src = res.get("_source") or {}
            sid = str(src.get("session_id") or session_id)
            title = str(src.get("title") or "")
            title_source = str(src.get("title_source") or "off")
            disp = display_title({"title": title, "title_source": title_source}, sid)
            return {"title": disp, "title_source": title_source}
        except Exception as exc:  # noqa: BLE001
            logger.error("ConversationArchiveStore get_session_title_snapshot failed: %s", exc)
            return {}

    def delete_session(self, *, user_id: str, session_id: str) -> None:
        """
        删除冷层中的会话数据（消息索引 + 会话汇总索引）。

        说明：
        - 该操作用于与热层删除保持一致，避免 `/chatbot/sessions*` 被冷层回查“补回”；
        - 对象存储备份是离线容灾副本，默认不做联动删除。
        """
        if not self.enabled:
            return
        sess_id = self._session_doc_id(user_id, session_id)
        try:
            self._es.delete_by_query(
                index=self._msg_index,
                body={
                    "query": {
                        "bool": {
                            "filter": [
                                {"term": {"user_id": user_id}},
                                {"term": {"session_id": session_id}},
                            ]
                        }
                    }
                },
                conflicts="proceed",
                refresh=True,
            )
            self._es.delete(index=self._session_index, id=sess_id, ignore=404, refresh=True)
        except Exception as exc:  # noqa: BLE001
            logger.error("ConversationArchiveStore delete_session failed: %s", exc)

    def _backup_object(self, msg_doc: Dict[str, Any]) -> None:
        if not self._object_enabled:
            return
        try:
            if self._object_backend == "local":
                self._backup_local(msg_doc)
                return
            if self._object_backend == "s3":
                self._backup_s3(msg_doc)
                return
        except Exception as exc:  # noqa: BLE001
            logger.warning("ConversationArchiveStore object backup failed: %s", exc)

    def _backup_local(self, msg_doc: Dict[str, Any]) -> None:
        ts_ms = int(msg_doc.get("ts_ms") or int(time.time() * 1000))
        dt = datetime.fromtimestamp(ts_ms / 1000.0, tz=timezone.utc)
        p = self._object_local_dir / f"dt={dt.strftime('%Y-%m-%d')}" / f"user={msg_doc.get('user_id')}"
        p.mkdir(parents=True, exist_ok=True)
        fpath = p / f"{msg_doc.get('session_id')}.jsonl"
        line = json.dumps(msg_doc, ensure_ascii=False)
        with fpath.open("a", encoding="utf-8") as f:
            f.write(line + "\n")

    def _backup_s3(self, msg_doc: Dict[str, Any]) -> None:
        if self._s3_client is None:
            return
        bucket = os.getenv("CONV_ARCHIVE_OBJECT_S3_BUCKET", "").strip()
        if not bucket:
            return
        ts_ms = int(msg_doc.get("ts_ms") or int(time.time() * 1000))
        dt = datetime.fromtimestamp(ts_ms / 1000.0, tz=timezone.utc)
        prefix = (os.getenv("CONV_ARCHIVE_OBJECT_S3_PREFIX", "conversation-archive") or "conversation-archive").strip("/")
        key = (
            f"{prefix}/dt={dt.strftime('%Y-%m-%d')}/user={msg_doc.get('user_id')}/"
            f"session={msg_doc.get('session_id')}/{msg_doc.get('message_id')}.json"
        )
        body = json.dumps(msg_doc, ensure_ascii=False).encode("utf-8")
        self._s3_client.put_object(Bucket=bucket, Key=key, Body=body, ContentType="application/json")


def get_archive_store() -> ConversationArchiveStore:
    global _archive_singleton
    if _archive_singleton is not None:
        return _archive_singleton
    with _archive_lock:
        if _archive_singleton is not None:
            return _archive_singleton
        _archive_singleton = ConversationArchiveStore()
        return _archive_singleton

