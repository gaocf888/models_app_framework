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
_CN_PERIOD_RUN = re.compile(r"。{2,}")

# 与 `analysis_graph_runner` 注入的规划前 RAG 附录前缀一致；缓存键须去掉其后可变检索正文，避免永不命中。
_PLAN_CONTEXT_GUIDE_MARKERS: tuple[str, ...] = (
    "请结合以下规则线索",
    "请结合以下规则",
)

_global_cache: NL2SQLSqlCache | None = None
_global_l1_cache: NL2SQLSqlCache | None = None
_global_lock = threading.Lock()


def normalize_nl2sql_question(text: str) -> str:
    """规则化问题文本，提高相同意图下的缓存命中率（无 LLM）。"""
    s = (text or "").replace("\r\n", "\n").replace("\r", "\n").strip()
    s = _WS_RE.sub(" ", s)
    return s


def strip_plan_context_guide_suffix(text: str) -> str:
    """
    去掉综合分析 `plan_context_rag` 拼在问句后的可变附录（自首个「请结合以下规则线索」类标记起截断）。

    L2/L1 缓存键基于此前缀之前的正文（用户原句 + 计划子任务模板句 + scope 守卫），避免因 ES/RAG 写入漂移导致永 miss。
    """
    s = (text or "").strip()
    if not s:
        return s
    cut = len(s)
    for m in _PLAN_CONTEXT_GUIDE_MARKERS:
        i = s.find(m)
        if i >= 0:
            cut = min(cut, i)
    out = s[:cut].strip()
    # `…守卫。。请结合…` 截断后易残留重复句读，与无 RAG 的 task.question 对齐以便命中缓存
    out = _CN_PERIOD_RUN.sub("。", out)
    return out


def compute_schema_fp_from_metadata(table_names: list[str]) -> str:
    """由当前反射表名列表生成短指纹；表集合变化则指纹变化。"""
    names = sorted({n.strip().lower() for n in table_names if (n or "").strip()})
    raw = ",".join(names)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]


def compute_nl2sql_data_source_fp(*, host: str, port: int, database: str) -> str:
    """
    业务库「数据源」指纹：与 user 无关，用于在相同 DB 上复用 L2 缓存。
    使用 host + port + database，避免将整段 DB_URL（含凭据）纳入 key 材料。
    """
    h = (host or "").strip().lower()
    d = (database or "").strip().lower()
    p = int(port) if port is not None else 0
    raw = f"{h}\0{p}\0{d}"
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
        os.getenv("NL2SQL_PROMPT_DEFAULT_VERSION", "") or "",
        (os.getenv("ANALYSIS_NL2SQL_TABLE_SCOPE_DEFAULT") or "").strip(),
        (os.getenv("ANALYSIS_NL2SQL_JOIN_WHITELIST") or "").strip(),
        join_scoped,
        (os.getenv("NL2SQL_ENTITY_RULES", "") or "")[:4000],
    ]
    try:
        from app.nl2sql.intent_config import (
            business_domain,
            schema_link_catalog_mode,
            semantic_link_enabled,
            table_allowlist_fingerprint,
        )
        from app.nl2sql.nl2sql_business_profile import get_nl2sql_business_profile

        profile = get_nl2sql_business_profile()
        prompt_ver = (os.getenv("NL2SQL_PROMPT_DEFAULT_VERSION") or "").strip()
        if not prompt_ver and profile:
            prompt_ver = profile.prompt_default_version or "v2"
        if not prompt_ver:
            prompt_ver = "v2"
        parts[0] = prompt_ver

        semantic_version = ""
        if semantic_link_enabled() and profile:
            try:
                from app.nl2sql.semantic_layer import semantic_version_fingerprint

                semantic_version = semantic_version_fingerprint()
            except Exception:  # noqa: BLE001
                semantic_version = ""

        parts.extend(
            [
                business_domain() or "",
                str(semantic_link_enabled()),
                schema_link_catalog_mode(),
                table_allowlist_fingerprint(),
                semantic_version,
            ]
        )
    except Exception:  # noqa: BLE001
        pass
    raw = "\n".join(parts)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]


def build_nl2sql_sql_cache_key(
    *,
    data_source_fp: str,
    analysis_type: str | None,
    plan_item_id: str | None,
    question: str,
    schema_fp: str,
    policy_fp: str,
) -> str:
    qn = normalize_nl2sql_question(strip_plan_context_guide_suffix(question))
    raw = (
        f"{data_source_fp}\0{(analysis_type or '').strip()}\0{(plan_item_id or '').strip()}\0"
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


def get_nl2sql_l1_cache(*, ttl_seconds: int, max_entries: int) -> NL2SQLSqlCache:
    """L1 骨架（JSON 字符串）专用 LRU；语义与 L2 相同，存储分区独立。"""
    global _global_l1_cache
    with _global_lock:
        if _global_l1_cache is None:
            _global_l1_cache = NL2SQLSqlCache(ttl_seconds=ttl_seconds, max_entries=max_entries)
        else:
            _global_l1_cache.configure(ttl_seconds=ttl_seconds, max_entries=max_entries)
        return _global_l1_cache
