from __future__ import annotations

import asyncio
import json
import os
import re
import time
from typing import Any, Dict, List

from app.core.config import ChatbotConfig
from app.core.logging import get_logger

logger = get_logger(__name__)


def _cn_num_to_int(s: str) -> int | None:
    s = (s or "").strip()
    if not s:
        return None
    if s.isdigit():
        return int(s)
    m = {"零": 0, "一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}
    if s in m:
        return m[s]
    if s == "十":
        return 10
    if "十" in s:
        left, _, right = s.partition("十")
        lv = m.get(left, 1 if left == "" else -1)
        rv = m.get(right, 0 if right == "" else -1)
        if lv >= 0 and rv >= 0:
            return lv * 10 + rv
    return None


_REF_WORD_PAT = re.compile(r"(?:上面|上述|前面|前文|上一轮|上一条|上个回答|前一次|刚才)")
_REF_N_PAT = re.compile(r"第\s*([0-9一二三四五六七八九十两]+)\s*[点条项]")
_REF_TOPIC_PAT = re.compile(r"(?:上面|上述|前面|前文|上一轮|上一条|上个回答|前一次|刚才).{0,80}?([^\n，。,；;]{2,40}?)(?:中的|里[的地]|中第|第)")
_LINE_PAT = re.compile(r"^\s*(?:\(?([0-9]{1,2})\)?[\.、:：]|（([一二三四五六七八九十两]{1,3})）|([一二三四五六七八九十两]{1,3})[、\.])\s*(.+)$")


def detect_reference_index(query: str) -> int | None:
    q = query or ""
    if not _REF_WORD_PAT.search(q):
        return None
    m = _REF_N_PAT.search(q)
    if not m:
        return None
    raw = m.group(1)
    return _cn_num_to_int(raw)


def has_reference_signal(query: str) -> bool:
    q = query or ""
    if _REF_WORD_PAT.search(q):
        return True
    # 直接续问（无“上面”词）也当成引用信号
    return bool(re.search(r"(继续|展开|详细说|详细说明|进一步说明|接着说|延伸一下|再讲讲).{0,12}(上文|上述|前面|这个|该点|这一点|这条)?", q))


def extract_reference_topic(query: str) -> str:
    q = query or ""
    m = _REF_TOPIC_PAT.search(q)
    if not m:
        return ""
    return (m.group(1) or "").strip()


def extract_outline_items(answer: str, max_items: int = 20) -> List[Dict[str, Any]]:
    text = (answer or "").strip()
    if not text:
        return []
    out: List[Dict[str, Any]] = []
    lines = [x.strip() for x in text.splitlines() if x.strip()]
    for ln in lines:
        m = _LINE_PAT.match(ln)
        if not m:
            continue
        idx = None
        if m.group(1):
            idx = int(m.group(1))
        elif m.group(2):
            idx = _cn_num_to_int(m.group(2))
        elif m.group(3):
            idx = _cn_num_to_int(m.group(3))
        body = (m.group(4) or "").strip()
        if idx is None or not body:
            continue
        out.append({"idx": idx, "title": body[:64], "gist": body[:280], "raw": body})
        if len(out) >= max_items:
            return out

    if out:
        return out

    # 兜底：无显式编号时按句切分形成弱索引
    chunks = [x.strip() for x in re.split(r"[。！？;\n]+", text) if x.strip()]
    for i, c in enumerate(chunks[: max_items], start=1):
        out.append({"idx": i, "title": c[:64], "gist": c[:280], "raw": c})
    return out


class ChatbotOutlineStore:
    def __init__(self, cfg: ChatbotConfig) -> None:
        self._cfg = cfg
        self._enabled = bool(cfg.outline_enabled)
        self._ttl_seconds = max(60, int(os.getenv("CONV_SESSION_TTL_MINUTES", "60")) * 60)
        self._max_keep = max(1, int(os.getenv("CONV_MAX_HISTORY_MESSAGES", "50")))
        self._redis = None
        self._es = None
        self._es_index = (cfg.outline_es_index or "conversation_outline_v1").strip()
        self._init_redis()
        self._init_es()

    def _init_redis(self) -> None:
        if not self._enabled:
            return
        redis_url = (os.getenv("REDIS_URL") or "").strip()
        if not redis_url:
            return
        try:
            from redis import asyncio as aioredis  # type: ignore[import-not-found]

            self._redis = aioredis.from_url(redis_url, encoding="utf-8", decode_responses=True)
        except Exception as exc:  # noqa: BLE001
            logger.warning("outline redis init failed: %s", exc)
            self._redis = None

    def _init_es(self) -> None:
        if not self._enabled or not self._cfg.outline_es_enabled:
            return
        if not (os.getenv("CONV_ARCHIVE_ENABLED", "false").lower() == "true"):
            return
        try:
            from elasticsearch import Elasticsearch  # type: ignore[import-not-found]
        except Exception:
            return
        hosts_raw = os.getenv("CONV_ARCHIVE_ES_HOSTS") or os.getenv("RAG_ES_HOSTS") or "http://localhost:9200"
        hosts = [x.strip() for x in hosts_raw.split(",") if x.strip()]
        username = os.getenv("CONV_ARCHIVE_ES_USERNAME") or os.getenv("RAG_ES_USERNAME") or None
        password = os.getenv("CONV_ARCHIVE_ES_PASSWORD") or os.getenv("RAG_ES_PASSWORD") or None
        api_key = os.getenv("CONV_ARCHIVE_ES_API_KEY") or os.getenv("RAG_ES_API_KEY") or None
        verify_certs = os.getenv("CONV_ARCHIVE_ES_VERIFY_CERTS", os.getenv("RAG_ES_VERIFY_CERTS", "false")).lower() == "true"
        timeout = max(1, int(os.getenv("CONV_ARCHIVE_ES_TIMEOUT_SECONDS", "10")))
        kwargs: Dict[str, Any] = dict(hosts=hosts, verify_certs=verify_certs, request_timeout=timeout)
        if api_key:
            kwargs["api_key"] = api_key
        elif username and password:
            kwargs["basic_auth"] = (username, password)
        try:
            self._es = Elasticsearch(**kwargs)
            self._es.indices.create(
                index=self._es_index,
                body={
                    "mappings": {
                        "properties": {
                            "user_id": {"type": "keyword"},
                            "session_id": {"type": "keyword"},
                            "assistant_message_id": {"type": "keyword"},
                            "items": {"type": "object", "enabled": False},
                            "created_at_ms": {"type": "long"},
                        }
                    }
                },
                ignore=400,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("outline es init failed: %s", exc)
            self._es = None

    @property
    def enabled(self) -> bool:
        return self._enabled

    async def save_outline(
        self,
        *,
        user_id: str,
        session_id: str,
        assistant_message_id: str,
        answer_text: str,
    ) -> None:
        if not self._enabled:
            return
        items = extract_outline_items(answer_text)
        if not items:
            return
        now_ms = int(time.time() * 1000)
        doc = {
            "user_id": user_id,
            "session_id": session_id,
            "assistant_message_id": assistant_message_id,
            "items": items,
            "created_at_ms": now_ms,
        }
        if self._redis is not None:
            key = f"conv:outline:{user_id}:{session_id}"
            try:
                payload = json.dumps(doc, ensure_ascii=False)
                await self._redis.rpush(key, payload)
                await self._redis.ltrim(key, -self._max_keep, -1)
                await self._redis.expire(key, self._ttl_seconds)
            except Exception as exc:  # noqa: BLE001
                logger.warning("save outline redis failed: %s", exc)
        if self._es is not None:
            try:
                await asyncio.to_thread(
                    self._es.index,
                    index=self._es_index,
                    id=assistant_message_id,
                    body=doc,
                    refresh=False,
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning("save outline es failed: %s", exc)

    async def resolve_reference(self, *, user_id: str, session_id: str, query: str) -> Dict[str, Any] | None:
        if not self._enabled or not self._cfg.reference_resolve_enabled:
            return None
        idx = detect_reference_index(query)
        ref_signal = has_reference_signal(query)
        if (not idx or idx <= 0) and (not ref_signal):
            return None
        if self._redis is None:
            return None
        key = f"conv:outline:{user_id}:{session_id}"
        try:
            lookback = max(1, int(self._cfg.reference_lookback_turns))
            raw = await self._redis.lrange(key, -lookback, -1)
        except Exception:
            return None
        if not raw:
            return None
        docs: List[Dict[str, Any]] = []
        for s in raw:
            try:
                docs.append(json.loads(s))
            except Exception:
                continue
        if not docs:
            return None

        topic = extract_reference_topic(query).lower()
        ordered_docs = list(reversed(docs))
        if topic:
            scored: List[tuple[int, Dict[str, Any]]] = []
            for d in ordered_docs:
                items = d.get("items") or []
                joined = " ".join(str(it.get("title") or "") + " " + str(it.get("gist") or "") for it in items).lower()
                score = 1 if topic and topic in joined else 0
                scored.append((score, d))
            ordered_docs = [x[1] for x in sorted(scored, key=lambda t: t[0], reverse=True)]

        if idx and idx > 0:
            for doc in ordered_docs:
                items = doc.get("items") or []
                for it in items:
                    if int(it.get("idx") or -1) == idx:
                        gist = str(it.get("gist") or "").strip()
                        if not gist:
                            continue
                        return {
                            "index": idx,
                            "gist": gist,
                            "assistant_message_id": str(doc.get("assistant_message_id") or ""),
                        }

        # 无明确第N点时，返回最近一轮要点摘要，支撑“继续上面内容”类续问
        if ref_signal:
            doc = ordered_docs[0]
            items = doc.get("items") or []
            if not items:
                return None
            top = items[: min(3, len(items))]
            gist = "；".join(f"第{int(it.get('idx') or i+1)}点：{str(it.get('gist') or '').strip()}" for i, it in enumerate(top))
            gist = gist.strip("； ").strip()
            if not gist:
                return None
            return {
                "index": 0,
                "gist": gist,
                "assistant_message_id": str(doc.get("assistant_message_id") or ""),
            }
        return None

