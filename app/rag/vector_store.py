from __future__ import annotations

import json
import os
from abc import ABC, abstractmethod
from pathlib import Path
from typing import List, Optional, Sequence, Tuple

from app.core.config import get_app_config
from app.core.logging import get_logger

logger = get_logger(__name__)


class VectorStore(ABC):
    """
    向量库抽象接口。
    """

    @abstractmethod
    def add_texts(
        self,
        texts: Sequence[str],
        embeddings: Sequence[Sequence[float]],
        ids: Sequence[str] | None = None,
        namespace: str | None = None,
    ) -> List[str]:
        """
        添加文本到向量库并返回外部 id 列表（与输入 ids 对齐）。
        """
        ...

    @abstractmethod
    def similarity_search_by_vector(
        self,
        vector: Sequence[float],
        k: int = 5,
        namespace: str | None = None,
    ) -> List[Tuple[str, float]]:
        """
        返回 (text, score) 列表，score 越大表示越相似。
        """
        ...


class InMemoryVectorStore(VectorStore):
    """
    简单的内存向量库实现（用于开发/单元测试）。
    """

    def __init__(self) -> None:
        self._items: list[dict] = []  # { "text": str, "namespace": str|None, "ext_id": str }
        self._embs: list[list[float]] = []

    def add_texts(
        self,
        texts: Sequence[str],
        embeddings: Sequence[Sequence[float]],
        ids: Sequence[str] | None = None,
        namespace: str | None = None,
    ) -> List[str]:
        if len(texts) != len(embeddings):
            raise ValueError("texts and embeddings length mismatch")

        if ids is not None and len(ids) != len(texts):
            raise ValueError("ids length mismatch")

        start = len(self._items)
        ext_ids = [str(start + i) for i in range(len(texts))] if ids is None else list(ids)

        self._items.extend(
            {"text": t, "namespace": namespace, "ext_id": ext_ids[i]} for i, t in enumerate(texts)
        )
        self._embs.extend([list(e) for e in embeddings])
        return ext_ids

    def similarity_search_by_vector(
        self,
        vector: Sequence[float],
        k: int = 5,
        namespace: str | None = None,
    ) -> List[Tuple[str, float]]:
        def dot(a: Sequence[float], b: Sequence[float]) -> float:
            return float(sum(x * y for x, y in zip(a, b)))

        def norm(a: Sequence[float]) -> float:
            return float(sum(x * x for x in a) ** 0.5) or 1.0

        scores: list[Tuple[int, float]] = []
        v_norm = norm(vector)
        for idx, emb in enumerate(self._embs):
            item = self._items[idx]
            if namespace is not None and item.get("namespace") != namespace:
                continue
            score = dot(vector, emb) / (v_norm * norm(emb))
            scores.append((idx, score))

        scores.sort(key=lambda x: x[1], reverse=True)
        top = scores[:k]
        return [(self._items[i]["text"], s) for i, s in top]


class FaissVectorStore(VectorStore):
    """
    基于 FAISS 的持久化向量库实现。

    持久化内容：
    - `faiss.index`：FAISS 索引（add_with_ids 使用 IndexIDMap2）
    - `faiss_meta.json`：文本/namespace/外部 id 等元数据
    """

    def __init__(self, index_dir: str) -> None:
        self._dir = Path(index_dir)
        self._dir.mkdir(parents=True, exist_ok=True)
        self._index_path = self._dir / "faiss.index"
        self._meta_path = self._dir / "faiss_meta.json"

        self._dim: int | None = None
        self._index = None
        self._items: list[dict] = []  # { "text": str, "namespace": str|None, "ext_id": str }

        self._load_if_exists()

    def _load_if_exists(self) -> None:
        if not self._index_path.exists() or not self._meta_path.exists():
            return

        try:
            import faiss  # type: ignore
        except ImportError as e:
            raise ImportError(
                "faiss-cpu is required for FaissVectorStore. "
                "Install with: pip install -r requirements-大模型应用.txt"
            ) from e

        meta = json.loads(self._meta_path.read_text(encoding="utf-8"))
        self._items = list(meta.get("items", []))
        self._dim = int(meta["dim"])
        self._index = faiss.read_index(str(self._index_path))

        # 简单一致性校验：若不一致，优先以 meta 为准
        if len(self._items) == 0:
            logger.warning("FaissVectorStore meta is empty: %s", self._meta_path)

        logger.info(
            "FaissVectorStore loaded: dim=%s items=%s dir=%s",
            self._dim,
            len(self._items),
            str(self._dir),
        )

    @staticmethod
    def _load_faiss():
        try:
            import faiss  # type: ignore
        except ImportError as e:
            raise ImportError(
                "faiss-cpu is required for FaissVectorStore. "
                "Install with: pip install -r requirements-大模型应用.txt"
            ) from e
        return faiss

    def _create_index(self, dim: int) -> None:
        faiss = self._load_faiss()
        # 使用 inner product（EmbeddingService 已 normalize_embeddings=True，则等价余弦相似度）
        base = faiss.IndexFlatIP(dim)
        self._index = faiss.IndexIDMap2(base)
        self._dim = dim

    def _persist(self) -> None:
        if self._index is None or self._dim is None:
            return
        faiss = self._load_faiss()
        faiss.write_index(self._index, str(self._index_path))
        meta = {"dim": self._dim, "items": self._items}
        tmp_path = self._meta_path.with_suffix(".tmp")
        tmp_path.write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")
        os.replace(str(tmp_path), str(self._meta_path))

    def add_texts(
        self,
        texts: Sequence[str],
        embeddings: Sequence[Sequence[float]],
        ids: Sequence[str] | None = None,
        namespace: str | None = None,
    ) -> List[str]:
        if len(texts) != len(embeddings):
            raise ValueError("texts and embeddings length mismatch")

        if ids is not None and len(ids) != len(texts):
            raise ValueError("ids length mismatch")

        if len(texts) == 0:
            return []

        import numpy as np

        emb_arr = np.asarray(embeddings, dtype="float32")
        if emb_arr.ndim != 2:
            raise ValueError("embeddings must be a 2D array-like (n, dim)")

        dim = int(emb_arr.shape[1])
        if self._index is None:
            self._create_index(dim)
        elif self._dim is not None and dim != self._dim:
            raise ValueError(f"embedding dim mismatch: got={dim} expected={self._dim}")

        faiss = self._load_faiss()
        assert self._index is not None

        start = len(self._items)
        internal_ids = np.arange(start, start + len(texts), dtype="int64")
        ext_ids = [str(start + i) for i in range(len(texts))] if ids is None else list(ids)

        # 将向量与元数据追加到索引
        self._index.add_with_ids(emb_arr, internal_ids)
        self._items.extend(
            {"text": t, "namespace": namespace, "ext_id": ext_ids[i]} for i, t in enumerate(texts)
        )

        # 每次写入后持久化，保证服务重启后仍可检索
        self._persist()
        return ext_ids

    def similarity_search_by_vector(
        self,
        vector: Sequence[float],
        k: int = 5,
        namespace: str | None = None,
    ) -> List[Tuple[str, float]]:
        if self._index is None or self._dim is None or len(self._items) == 0:
            return []

        import numpy as np

        q = np.asarray(vector, dtype="float32").reshape(1, -1)
        if q.shape[1] != self._dim:
            raise ValueError(f"query dim mismatch: got={q.shape[1]} expected={self._dim}")

        # 无 namespace 过滤：直接返回 FAISS top-k
        if namespace is None:
            scores, ids = self._index.search(q, k)
            results: list[Tuple[str, float]] = []
            for score, internal_id in zip(scores[0].tolist(), ids[0].tolist()):
                if internal_id < 0:
                    continue
                item = self._items[internal_id]
                results.append((item["text"], float(score)))
            return results

        # namespace 过滤：增加搜索范围后在结果中筛选
        search_k = min(max(k * 5, k), len(self._items))
        scores, ids = self._index.search(q, search_k)
        results = []
        for score, internal_id in zip(scores[0].tolist(), ids[0].tolist()):
            if internal_id < 0:
                continue
            item = self._items[internal_id]
            if item.get("namespace") != namespace:
                continue
            results.append((item["text"], float(score)))
            if len(results) >= k:
                break
        return results


class VectorStoreProvider:
    """
    向量库提供者，根据配置返回具体的向量库实例。
    """

    def __init__(self) -> None:
        cfg = get_app_config().rag
        store_type = (cfg.vector_store_type or "").lower()

        if store_type == "faiss":
            index_dir = getattr(cfg, "faiss_index_dir", "./data/faiss")
            self._default_store: VectorStore = FaissVectorStore(index_dir=index_dir)
        elif store_type in {"memory", "inmemory"}:
            self._default_store = InMemoryVectorStore()
        else:
            raise ValueError(f"Unsupported vector_store_type: {cfg.vector_store_type}")

    def get_default_store(self) -> VectorStore:
        return self._default_store

