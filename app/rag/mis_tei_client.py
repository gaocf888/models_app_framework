"""
昇腾 MIS-TEI（Text Embeddings Inference）HTTP 客户端。

兼容 HuggingFace TEI 风格接口：
- Embedding: POST /embed  body={"inputs": str | list[str]}
- Rerank:    POST /rerank body={"query": str, "texts": list[str]}

亦兼容部分镜像提供的 OpenAI 风格 POST /v1/embeddings（embed 回退）。
"""

from __future__ import annotations

import math
from typing import Any, Sequence

import httpx

from app.core.logging import get_logger

logger = get_logger(__name__)


def _l2_normalize(vec: list[float]) -> list[float]:
    n = math.sqrt(sum(float(x) * float(x) for x in vec)) or 1.0
    return [float(x) / n for x in vec]


def parse_embed_response(payload: Any) -> list[list[float]]:
    """将 TEI / OpenAI 风格 embed 响应规范为 list[list[float]]。"""
    if payload is None:
        raise ValueError("empty embed response")
    if isinstance(payload, list):
        if not payload:
            return []
        if isinstance(payload[0], (int, float)):
            return [[float(x) for x in payload]]
        if isinstance(payload[0], list):
            return [[float(x) for x in row] for row in payload]
        if isinstance(payload[0], dict) and "embedding" in payload[0]:
            return [[float(x) for x in row["embedding"]] for row in payload]
    if isinstance(payload, dict):
        if "embeddings" in payload:
            return parse_embed_response(payload["embeddings"])
        if "data" in payload:
            rows = payload["data"]
            if isinstance(rows, list):
                ordered = sorted(
                    rows,
                    key=lambda r: int(r.get("index", 0)) if isinstance(r, dict) else 0,
                )
                return [[float(x) for x in r["embedding"]] for r in ordered if isinstance(r, dict)]
    raise ValueError(f"unsupported embed response type={type(payload).__name__}")


def parse_rerank_scores(payload: Any, *, n_texts: int) -> list[float]:
    """
    将 TEI /rerank 响应转为与 texts 同序的 score 列表。
    TEI 常按 score 降序返回并带 index 字段。
    """
    scores = [0.0] * max(0, n_texts)
    if payload is None:
        return scores
    rows: list[Any]
    if isinstance(payload, dict):
        rows = payload.get("results") or payload.get("data") or []
    elif isinstance(payload, list):
        rows = payload
    else:
        raise ValueError(f"unsupported rerank response type={type(payload).__name__}")

    for i, row in enumerate(rows):
        if isinstance(row, (int, float)):
            if i < n_texts:
                scores[i] = float(row)
            continue
        if not isinstance(row, dict):
            continue
        idx = int(row.get("index", i))
        if 0 <= idx < n_texts:
            scores[idx] = float(row.get("score", row.get("relevance_score", 0.0)))
    return scores


class MisTeiEmbeddingClient:
    """MIS-TEI Embedding HTTP 客户端。"""

    def __init__(
        self,
        *,
        base_url: str,
        timeout_s: float = 120.0,
        batch_size: int = 32,
        normalize: bool = True,
    ) -> None:
        self._base = (base_url or "").rstrip("/")
        self._timeout = httpx.Timeout(
            connect=30.0,
            read=max(30.0, float(timeout_s)),
            write=60.0,
            pool=30.0,
        )
        self._batch_size = max(1, int(batch_size))
        self._normalize = bool(normalize)

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        if not texts:
            return []
        out: list[list[float]] = []
        for i in range(0, len(texts), self._batch_size):
            chunk = [str(t) if t is not None else "" for t in texts[i : i + self._batch_size]]
            out.extend(self._embed_once(chunk))
        if self._normalize:
            out = [_l2_normalize(v) for v in out]
        return out

    def _embed_once(self, texts: list[str]) -> list[list[float]]:
        body: dict[str, Any] = {"inputs": texts[0] if len(texts) == 1 else texts}
        url = f"{self._base}/embed"
        try:
            with httpx.Client(timeout=self._timeout) as client:
                resp = client.post(url, json=body)
                if resp.status_code == 404:
                    # 部分镜像仅暴露 OpenAI 兼容路径
                    resp = client.post(
                        f"{self._base}/v1/embeddings",
                        json={"input": texts, "model": "default"},
                    )
                resp.raise_for_status()
                return parse_embed_response(resp.json())
        except Exception as e:
            logger.error("MIS-TEI embed failed url=%s n=%s err=%s", url, len(texts), e)
            raise RuntimeError(f"MIS-TEI embed failed: {e}") from e


class MisTeiReranker:
    """与 CrossEncoder 兼容的 predict(pairs, batch_size=...) 接口。"""

    def __init__(
        self,
        *,
        base_url: str,
        timeout_s: float = 120.0,
        batch_size: int = 16,
    ) -> None:
        self._base = (base_url or "").rstrip("/")
        self._timeout = httpx.Timeout(
            connect=30.0,
            read=max(30.0, float(timeout_s)),
            write=60.0,
            pool=30.0,
        )
        self._default_batch = max(1, int(batch_size))
        self.device = "mis-tei"

    def predict(self, pairs: Sequence[Sequence[str]], batch_size: int | None = None) -> list[float]:
        if not pairs:
            return []
        bs = max(1, int(batch_size or self._default_batch))
        scores: list[float] = []
        # RAG 路径同一 query；仍按批切片 texts
        i = 0
        while i < len(pairs):
            batch = list(pairs[i : i + bs])
            query = str(batch[0][0]) if batch[0] else ""
            texts = [str(p[1]) if len(p) > 1 else "" for p in batch]
            # 若 batch 内 query 不一致，退化为逐条
            if any(str(p[0]) != query for p in batch):
                for p in batch:
                    scores.extend(self._rerank_once(str(p[0]), [str(p[1]) if len(p) > 1 else ""]))
            else:
                scores.extend(self._rerank_once(query, texts))
            i += bs
        return scores

    def _rerank_once(self, query: str, texts: list[str]) -> list[float]:
        url = f"{self._base}/rerank"
        body = {"query": query, "texts": texts}
        try:
            with httpx.Client(timeout=self._timeout) as client:
                resp = client.post(url, json=body)
                resp.raise_for_status()
                return parse_rerank_scores(resp.json(), n_texts=len(texts))
        except Exception as e:
            logger.error("MIS-TEI rerank failed url=%s n=%s err=%s", url, len(texts), e)
            raise RuntimeError(f"MIS-TEI rerank failed: {e}") from e
