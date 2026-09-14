"""意图漏斗 L2：按 domain 加载原型问句并做嵌入相似度匹配。"""

from __future__ import annotations

import math
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

import yaml

from app.core.logging import get_logger
from app.llm.graphs.chatbot_business_profile import get_chatbot_business_profile, normalize_chatbot_domain
from app.rag.embedding_service import EmbeddingService
from app.rag.service_registry import get_embedding_service

logger = get_logger(__name__)

_REPO_ROOT = Path(__file__).resolve().parents[3]
_BUSINESS_ROOT = _REPO_ROOT / "configs" / "chatbot_business"

_VALID_LABELS = frozenset({"kb_qa", "data_query", "clarify", "hybrid_qa"})

# 代码内默认阈值（不为嵌入/LLM 新增模型类配置）
DEFAULT_SIM_ACCEPT = 0.72
DEFAULT_SIM_MARGIN = 0.05


@dataclass(frozen=True)
class PrototypeMatchResult:
    label: str | None
    best_score: float
    second_score: float
    accepted: bool
    reason: str
    scores: dict[str, float]


@dataclass
class _PrototypeCache:
    domain: str
    path: str
    mtime_ns: int
    items: list[tuple[str, str, list[float]]]  # label, text, embedding


_cache_lock = threading.Lock()
_cache: _PrototypeCache | None = None


def _prototypes_path(domain: str) -> Path:
    return _BUSINESS_ROOT / domain / "intent_prototypes.yaml"


def load_intent_prototype_texts(domain: str | None = None) -> dict[str, tuple[str, ...]]:
    """读取 domain 包原型问句；文件缺失时返回空。"""
    dom = normalize_chatbot_domain(domain or get_chatbot_business_profile().domain)
    path = _prototypes_path(dom)
    if not path.is_file():
        return {}
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        return {}
    labels_raw = raw.get("labels")
    if not isinstance(labels_raw, dict):
        return {}
    out: dict[str, tuple[str, ...]] = {}
    for key, val in labels_raw.items():
        label = str(key or "").strip()
        if label not in _VALID_LABELS:
            continue
        texts: list[str] = []
        if isinstance(val, (list, tuple)):
            for x in val:
                s = str(x or "").strip()
                if s:
                    texts.append(s)
        elif isinstance(val, str) and val.strip():
            texts.append(val.strip())
        if texts:
            out[label] = tuple(texts)
    return out


def clear_intent_prototype_cache() -> None:
    global _cache
    with _cache_lock:
        _cache = None


def _cosine(a: list[float], b: list[float]) -> float:
    n = min(len(a), len(b))
    if n <= 0:
        return 0.0
    dot = 0.0
    na = 0.0
    nb = 0.0
    for i in range(n):
        x = float(a[i])
        y = float(b[i])
        dot += x * y
        na += x * x
        nb += y * y
    if na <= 1e-12 or nb <= 1e-12:
        return 0.0
    return dot / (math.sqrt(na) * math.sqrt(nb))


def _ensure_cache(
    domain: str,
    embedding_service: EmbeddingService,
) -> _PrototypeCache | None:
    global _cache
    path = _prototypes_path(domain)
    if not path.is_file():
        return None
    mtime_ns = path.stat().st_mtime_ns
    with _cache_lock:
        if (
            _cache is not None
            and _cache.domain == domain
            and _cache.path == str(path)
            and _cache.mtime_ns == mtime_ns
        ):
            return _cache

    texts_by_label = load_intent_prototype_texts(domain)
    flat: list[tuple[str, str]] = []
    for label, texts in texts_by_label.items():
        for t in texts:
            flat.append((label, t))
    if not flat:
        return None

    embs = embedding_service.embed_texts([t for _, t in flat])
    items = [(lab, txt, list(emb)) for (lab, txt), emb in zip(flat, embs)]
    fresh = _PrototypeCache(domain=domain, path=str(path), mtime_ns=mtime_ns, items=items)
    with _cache_lock:
        _cache = fresh
    logger.info(
        "chatbot.intent_prototypes cached domain=%s n=%d path=%s",
        domain,
        len(items),
        path,
    )
    return fresh


def match_intent_by_prototypes(
    query: str,
    *,
    domain: str | None = None,
    embedding_service: EmbeddingService | None = None,
    sim_accept: float = DEFAULT_SIM_ACCEPT,
    sim_margin: float = DEFAULT_SIM_MARGIN,
    embed_query: Callable[[str], list[float]] | None = None,
) -> PrototypeMatchResult:
    """
    问句 vs 原型库余弦相似度；按 label 取 max 分。

    ``embed_query`` 供单测注入，避免真实 Embedding 服务。
    """
    q = (query or "").strip()
    empty_scores: dict[str, float] = {}
    if not q:
        return PrototypeMatchResult(None, 0.0, 0.0, False, "empty_query", empty_scores)

    dom = normalize_chatbot_domain(domain or get_chatbot_business_profile().domain)
    try:
        if embed_query is not None:
            # 测试路径：现场对原型文本再 embed（调用方应返回稳定向量）
            texts_by_label = load_intent_prototype_texts(dom)
            if not texts_by_label:
                return PrototypeMatchResult(None, 0.0, 0.0, False, "no_prototypes", empty_scores)
            q_emb = embed_query(q)
            label_scores: dict[str, float] = {}
            for label, texts in texts_by_label.items():
                best = 0.0
                for t in texts:
                    best = max(best, _cosine(q_emb, embed_query(t)))
                label_scores[label] = best
        else:
            svc = embedding_service or get_embedding_service()
            cached = _ensure_cache(dom, svc)
            if cached is None or not cached.items:
                return PrototypeMatchResult(None, 0.0, 0.0, False, "no_prototypes", empty_scores)
            q_emb = svc.embed_text(q)
            label_scores = {}
            for label, _txt, emb in cached.items:
                s = _cosine(q_emb, emb)
                prev = label_scores.get(label, 0.0)
                if s > prev:
                    label_scores[label] = s
    except Exception as e:  # noqa: BLE001
        logger.warning("chatbot.intent_prototypes match failed err=%s", e)
        return PrototypeMatchResult(None, 0.0, 0.0, False, f"embed_error:{type(e).__name__}", empty_scores)

    ranked = sorted(label_scores.items(), key=lambda kv: kv[1], reverse=True)
    if not ranked:
        return PrototypeMatchResult(None, 0.0, 0.0, False, "no_scores", empty_scores)

    best_label, best_score = ranked[0]
    second_score = ranked[1][1] if len(ranked) > 1 else 0.0
    accept = best_score >= float(sim_accept) and (best_score - second_score) >= float(sim_margin)
    reason = (
        f"l2_accept|{best_label}|sim={best_score:.3f}|gap={best_score - second_score:.3f}"
        if accept
        else f"l2_reject|top={best_label}|sim={best_score:.3f}|gap={best_score - second_score:.3f}"
    )
    return PrototypeMatchResult(
        label=best_label if accept else best_label,
        best_score=best_score,
        second_score=second_score,
        accepted=accept,
        reason=reason,
        scores=dict(label_scores),
    )


def load_intent_prototypes_raw_for_tests() -> dict[str, Any]:
    """测试辅助。"""
    return dict(load_intent_prototype_texts())
