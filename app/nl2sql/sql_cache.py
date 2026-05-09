"""
NL2SQL 可执行 SQL 快照缓存（L2，进程内 LRU + TTL）。

设计见 `docs/NL2SQL缓存实现方案.md`。命中后仍由 `NL2SQLChain` 走规范化、TiDB 改写与校验，不绕过安全逻辑。
"""

from __future__ import annotations

import hashlib
import os
import re
import threading
import time
from collections import OrderedDict

from app.core.logging import get_logger

logger = get_logger(__name__)

_WS_RE = re.compile(r"\s+")

_global_cache: NL2SQLSqlCache | None = None
_global_lock = threading.Lock()


def normalize_nl2sql_question(text: str) -> str:
    """规则化问题文本，提高相同意图下的缓存命中率（无 LLM）。"""
    s = (text or "").replace("\r\n", "\n").replace("\r", "\n").strip()
    s = _WS_RE.sub(" ", s)
    return s


def compute_schema_fp_from_metadata(table_names: list[str]) -> str:
    """由当前反射表名列表生成短指纹；表集合变化则指纹变化。"""
    names = sorted({n.strip().lower() for n in table_names if (n or "").strip()})
    raw = ",".join(names)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]


def compute_nl2sql_policy_fp(*, analysis_type: str | None) -> str:
    """
    影响 SQL 合法性与白名单的环境摘要（变更后旧缓存自然 miss）。
    """
    at = (analysis_type or "").strip().lower()
    join_scoped = ""
    if at:
        join_scoped = (os.getenv(f"ANALYSIS_NL2SQL_JOIN_WHITELIST_{at.upper()}") or "").strip()
    parts = [
        os.getenv("NL2SQL_PROMPT_DEFAULT_VERSION", "v2"),
        (os.getenv("ANALYSIS_NL2SQL_TABLE_SCOPE_DEFAULT") or "").strip(),
        (os.getenv("ANALYSIS_NL2SQL_JOIN_WHITELIST") or "").strip(),
        join_scoped,
        (os.getenv("NL2SQL_ENTITY_RULES", "") or "")[:4000],
    ]
    raw = "\n".join(parts)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]


def build_nl2sql_sql_cache_key(
    *,
    user_id: str,
    analysis_type: str | None,
    plan_item_id: str | None,
    question: str,
    schema_fp: str,
    policy_fp: str,
) -> str:
    qn = normalize_nl2sql_question(question)
    raw = (
        f"{user_id}\0{(analysis_type or '').strip()}\0{(plan_item_id or '').strip()}\0"
        f"{qn}\0{schema_fp}\0{policy_fp}"
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


class NL2SQLSqlCache:
    """进程内 TTL + LRU；多 worker 之间不共享。"""

    def __init__(self, *, ttl_seconds: int, max_entries: int) -> None:
        self._ttl_seconds = max(60, int(ttl_seconds))
        self._max_entries = max(16, int(max_entries))
        self._store: OrderedDict[str, tuple[float, str]] = OrderedDict()
        self._lock = threading.Lock()

    def configure(self, *, ttl_seconds: int, max_entries: int) -> None:
        self._ttl_seconds = max(60, int(ttl_seconds))
        self._max_entries = max(16, int(max_entries))

    def _evict_expired(self, now: float) -> None:
        dead = [k for k, (exp, _) in self._store.items() if exp < now]
        for k in dead:
            del self._store[k]

    def _evict_lru(self) -> None:
        while len(self._store) > self._max_entries:
            self._store.popitem(last=False)

    def get(self, key: str) -> str | None:
        now = time.time()
        with self._lock:
            self._evict_expired(now)
            hit = self._store.get(key)
            if not hit:
                return None
            exp, sql = hit
            if exp < now:
                del self._store[key]
                return None
            self._store.move_to_end(key)
            return sql

    def set(self, key: str, sql: str) -> None:
        if not sql or not sql.strip():
            return
        now = time.time()
        exp = now + float(self._ttl_seconds)
        with self._lock:
            self._evict_expired(now)
            self._store[key] = (exp, sql.strip())
            self._store.move_to_end(key)
            self._evict_lru()

    def delete(self, key: str) -> None:
        with self._lock:
            self._store.pop(key, None)


def get_nl2sql_sql_cache(*, ttl_seconds: int, max_entries: int) -> NL2SQLSqlCache:
    """返回（或懒建）全局缓存实例，并按当前配置调整 TTL/容量。"""
    global _global_cache
    with _global_lock:
        if _global_cache is None:
            _global_cache = NL2SQLSqlCache(ttl_seconds=ttl_seconds, max_entries=max_entries)
        else:
            _global_cache.configure(ttl_seconds=ttl_seconds, max_entries=max_entries)
        return _global_cache
