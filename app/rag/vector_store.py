from __future__ import annotations

import json
import os
import re
import time
import uuid
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any, List, Sequence

from app.core.config import ElasticsearchConfig, get_app_config
from app.core.logging import get_logger

logger = get_logger(__name__)


def _query_tokens(query: str) -> list[str]:
    q = (query or "").strip().lower()
    if not q:
        return []
    raw_tokens = [t for t in re.findall(r"[a-z0-9_]{2,}|[\u4e00-\u9fff]{1,16}", q) if t]
    expanded: list[str] = []
    for tk in raw_tokens:
        expanded.append(tk)
        # 中文长词切分为 2-gram，提升 metadata 命中鲁棒性（如“查询设备台账” -> “设备”“台账”）。
        if re.fullmatch(r"[\u4e00-\u9fff]{3,16}", tk):
            for i in range(len(tk) - 1):
                expanded.append(tk[i : i + 2])
    return list(dict.fromkeys(expanded))


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
        doc_name: str | None = None,
        metadatas: Sequence[dict[str, Any] | None] | None = None,
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
    ) -> List[dict[str, Any]]:
        """
        返回命中列表，元素包括 text/score/ext_id/namespace/doc_name 等字段。
        """
        ...

    @abstractmethod
    def keyword_search(
        self,
        query: str,
        k: int = 5,
        namespace: str | None = None,
    ) -> List[dict[str, Any]]:
        """
        关键词检索（BM25/倒排等），返回命中列表。
        """
        ...

    @abstractmethod
    def metadata_search(
        self,
        query: str,
        k: int = 5,
        namespace: str | None = None,
    ) -> List[dict[str, Any]]:
        """
        元数据召回（doc_name/doc_version/tenant_id 等），返回命中列表。
        """
        ...

    @abstractmethod
    def delete_by_doc_name(self, doc_name: str, namespace: str | None = None, doc_version: str | None = None) -> int:
        """
        按文档名称（可选版本）删除已有知识，返回删除条数。
        """
        ...


class InMemoryVectorStore(VectorStore):
    """
    简单的内存向量库实现（用于开发/单元测试）。
    """

    def __init__(self) -> None:
        self._items: list[dict[str, Any]] = []
        self._embs: list[list[float]] = []

    def add_texts(
        self,
        texts: Sequence[str],
        embeddings: Sequence[Sequence[float]],
        ids: Sequence[str] | None = None,
        namespace: str | None = None,
        doc_name: str | None = None,
        metadatas: Sequence[dict[str, Any] | None] | None = None,
    ) -> List[str]:
        if len(texts) != len(embeddings):
            raise ValueError("texts and embeddings length mismatch")

        if ids is not None and len(ids) != len(texts):
            raise ValueError("ids length mismatch")

        if metadatas is not None and len(metadatas) != len(texts):
            raise ValueError("metadatas length mismatch")

        ext_ids = [str(uuid.uuid4()) for _ in range(len(texts))] if ids is None else list(ids)

        for i, text in enumerate(texts):
            self._items.append(
                {
                    "text": text,
                    "namespace": namespace,
                    "doc_name": doc_name,
                    "ext_id": ext_ids[i],
                    "metadata": (metadatas[i] if metadatas is not None else None) or {},
                }
            )
        self._embs.extend([list(e) for e in embeddings])
        return ext_ids

    def similarity_search_by_vector(
        self,
        vector: Sequence[float],
        k: int = 5,
        namespace: str | None = None,
    ) -> List[dict[str, Any]]:
        def dot(a: Sequence[float], b: Sequence[float]) -> float:
            return float(sum(x * y for x, y in zip(a, b)))

        def norm(a: Sequence[float]) -> float:
            return float(sum(x * x for x in a) ** 0.5) or 1.0

        scores: list[tuple[int, float]] = []
        v_norm = norm(vector)
        for idx, emb in enumerate(self._embs):
            item = self._items[idx]
            if namespace is not None and item.get("namespace") != namespace:
                continue
            score = dot(vector, emb) / (v_norm * norm(emb))
            scores.append((idx, score))

        scores.sort(key=lambda x: x[1], reverse=True)
        top = scores[:k]
        return [
            {
                "text": self._items[i]["text"],
                "score": float(s),
                "ext_id": self._items[i]["ext_id"],
                "namespace": self._items[i].get("namespace"),
                "doc_name": self._items[i].get("doc_name"),
                "metadata": self._items[i].get("metadata") or {},
            }
            for i, s in top
        ]

    def keyword_search(
        self,
        query: str,
        k: int = 5,
        namespace: str | None = None,
    ) -> List[dict[str, Any]]:
        tokens = _query_tokens(query)
        if not tokens:
            return []

        scored: list[tuple[int, float]] = []
        for idx, item in enumerate(self._items):
            if namespace is not None and item.get("namespace") != namespace:
                continue
            text = (item.get("text") or "").lower()
            score = 0.0
            for tk in tokens:
                score += float(text.count(tk))
            if score > 0:
                scored.append((idx, score))
        scored.sort(key=lambda x: x[1], reverse=True)
        top = scored[:k]
        return [
            {
                "text": self._items[i]["text"],
                "score": float(s),
                "ext_id": self._items[i]["ext_id"],
                "namespace": self._items[i].get("namespace"),
                "doc_name": self._items[i].get("doc_name"),
                "metadata": self._items[i].get("metadata") or {},
            }
            for i, s in top
        ]

    def metadata_search(
        self,
        query: str,
        k: int = 5,
        namespace: str | None = None,
    ) -> List[dict[str, Any]]:
        tokens = _query_tokens(query)
        if not tokens:
            return []
        scored: list[tuple[int, float]] = []
        for idx, item in enumerate(self._items):
            if namespace is not None and item.get("namespace") != namespace:
                continue
            doc_name = str(item.get("doc_name") or "").lower()
            meta = item.get("metadata") or {}
            meta_blob = " ".join(str(v).lower() for v in meta.values())
            score = 0.0
            for tk in tokens:
                if tk and tk in doc_name:
                    score += 2.0
                if tk and tk in meta_blob:
                    score += 1.0
            if score > 0:
                scored.append((idx, score))
        scored.sort(key=lambda x: x[1], reverse=True)
        top = scored[:k]
        return [
            {
                "text": self._items[i]["text"],
                "score": float(s),
                "ext_id": self._items[i]["ext_id"],
                "namespace": self._items[i].get("namespace"),
                "doc_name": self._items[i].get("doc_name"),
                "metadata": self._items[i].get("metadata") or {},
            }
            for i, s in top
        ]

    def delete_by_doc_name(self, doc_name: str, namespace: str | None = None, doc_version: str | None = None) -> int:
        keep_items: list[dict[str, Any]] = []
        keep_embs: list[list[float]] = []
        deleted = 0
        for idx, item in enumerate(self._items):
            same_name = item.get("doc_name") == doc_name
            same_ns = namespace is None or item.get("namespace") == namespace
            meta = item.get("metadata") or {}
            same_ver = doc_version is None or str(meta.get("doc_version") or "") == str(doc_version)
            if same_name and same_ns and same_ver:
                deleted += 1
                continue
            keep_items.append(item)
            keep_embs.append(self._embs[idx])
        self._items = keep_items
        self._embs = keep_embs
        return deleted


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
        self._items: dict[int, dict] = {}

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
        items_by_id = meta.get("items_by_id")
        if isinstance(items_by_id, dict):
            self._items = {int(k): dict(v) for k, v in items_by_id.items()}
            self._next_internal_id = int(meta.get("next_internal_id", max(self._items.keys(), default=-1) + 1))
        else:
            # 兼容历史版本：items 为 list，internal_id 与下标一致
            old_items = list(meta.get("items", []))
            self._items = {idx: dict(item) for idx, item in enumerate(old_items)}
            self._next_internal_id = len(old_items)
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
        meta = {
            "dim": self._dim,
            "items_by_id": {str(k): v for k, v in self._items.items()},
            "next_internal_id": self._next_internal_id,
        }
        tmp_path = self._meta_path.with_suffix(".tmp")
        tmp_path.write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")
        os.replace(str(tmp_path), str(self._meta_path))

    def add_texts(
        self,
        texts: Sequence[str],
        embeddings: Sequence[Sequence[float]],
        ids: Sequence[str] | None = None,
        namespace: str | None = None,
        doc_name: str | None = None,
        metadatas: Sequence[dict[str, Any] | None] | None = None,
    ) -> List[str]:
        if len(texts) != len(embeddings):
            raise ValueError("texts and embeddings length mismatch")

        if ids is not None and len(ids) != len(texts):
            raise ValueError("ids length mismatch")
        if metadatas is not None and len(metadatas) != len(texts):
            raise ValueError("metadatas length mismatch")

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

        internal_ids = np.arange(self._next_internal_id, self._next_internal_id + len(texts), dtype="int64")
        ext_ids = [str(uuid.uuid4()) for _ in range(len(texts))] if ids is None else list(ids)

        # 将向量与元数据追加到索引
        self._index.add_with_ids(emb_arr, internal_ids)
        for i, text in enumerate(texts):
            iid = int(internal_ids[i])
            self._items[iid] = (
                {
                    "text": text,
                    "namespace": namespace,
                    "doc_name": doc_name,
                    "ext_id": ext_ids[i],
                    "metadata": (metadatas[i] if metadatas is not None else None) or {},
                }
            )
        self._next_internal_id += len(texts)

        # 每次写入后持久化，保证服务重启后仍可检索
        self._persist()
        return ext_ids

    def similarity_search_by_vector(
        self,
        vector: Sequence[float],
        k: int = 5,
        namespace: str | None = None,
    ) -> List[dict[str, Any]]:
        if self._index is None or self._dim is None or len(self._items) == 0:
            return []

        import numpy as np

        q = np.asarray(vector, dtype="float32").reshape(1, -1)
        if q.shape[1] != self._dim:
            raise ValueError(f"query dim mismatch: got={q.shape[1]} expected={self._dim}")

        # 无 namespace 过滤：直接返回 FAISS top-k
        if namespace is None:
            scores, ids = self._index.search(q, k)
            results: list[dict[str, Any]] = []
            for score, internal_id in zip(scores[0].tolist(), ids[0].tolist()):
                if internal_id < 0:
                    continue
                item = self._items.get(int(internal_id))
                if item is None:
                    continue
                results.append(
                    {
                        "text": item["text"],
                        "score": float(score),
                        "ext_id": item.get("ext_id"),
                        "namespace": item.get("namespace"),
                        "doc_name": item.get("doc_name"),
                        "metadata": item.get("metadata") or {},
                    }
                )
            return results

        # namespace 过滤：增加搜索范围后在结果中筛选
        search_k = min(max(k * 5, k), len(self._items))
        scores, ids = self._index.search(q, search_k)
        results = []
        for score, internal_id in zip(scores[0].tolist(), ids[0].tolist()):
            if internal_id < 0:
                continue
            item = self._items.get(int(internal_id))
            if item is None:
                continue
            if item.get("namespace") != namespace:
                continue
            results.append(
                {
                    "text": item["text"],
                    "score": float(score),
                    "ext_id": item.get("ext_id"),
                    "namespace": item.get("namespace"),
                    "doc_name": item.get("doc_name"),
                    "metadata": item.get("metadata") or {},
                }
            )
            if len(results) >= k:
                break
        return results

    def keyword_search(
        self,
        query: str,
        k: int = 5,
        namespace: str | None = None,
    ) -> List[dict[str, Any]]:
        # 本地 FAISS 模式下退化为内存关键词匹配（生产建议用 ES/EasySearch）
        tokens = _query_tokens(query)
        if not tokens:
            return []
        scored: list[tuple[int, float]] = []
        for internal_id, item in self._items.items():
            if namespace is not None and item.get("namespace") != namespace:
                continue
            text = (item.get("text") or "").lower()
            score = 0.0
            for tk in tokens:
                score += float(text.count(tk))
            if score > 0:
                scored.append((int(internal_id), score))
        scored.sort(key=lambda x: x[1], reverse=True)
        top = scored[:k]
        return [
            {
                "text": self._items[i]["text"],
                "score": float(s),
                "ext_id": self._items[i].get("ext_id"),
                "namespace": self._items[i].get("namespace"),
                "doc_name": self._items[i].get("doc_name"),
                "metadata": self._items[i].get("metadata") or {},
            }
            for i, s in top
        ]

    def metadata_search(
        self,
        query: str,
        k: int = 5,
        namespace: str | None = None,
    ) -> List[dict[str, Any]]:
        tokens = _query_tokens(query)
        if not tokens:
            return []
        scored: list[tuple[int, float]] = []
        for internal_id, item in self._items.items():
            if namespace is not None and item.get("namespace") != namespace:
                continue
            doc_name = str(item.get("doc_name") or "").lower()
            meta = item.get("metadata") or {}
            meta_blob = " ".join(str(v).lower() for v in meta.values())
            score = 0.0
            for tk in tokens:
                if tk and tk in doc_name:
                    score += 2.0
                if tk and tk in meta_blob:
                    score += 1.0
            if score > 0:
                scored.append((int(internal_id), score))
        scored.sort(key=lambda x: x[1], reverse=True)
        top = scored[:k]
        return [
            {
                "text": self._items[i]["text"],
                "score": float(s),
                "ext_id": self._items[i].get("ext_id"),
                "namespace": self._items[i].get("namespace"),
                "doc_name": self._items[i].get("doc_name"),
                "metadata": self._items[i].get("metadata") or {},
            }
            for i, s in top
            if i in self._items
        ]

    def delete_by_doc_name(self, doc_name: str, namespace: str | None = None, doc_version: str | None = None) -> int:
        if self._index is None or not self._items:
            return 0
        import numpy as np

        delete_ids: list[int] = []
        for internal_id, item in self._items.items():
            same_name = item.get("doc_name") == doc_name
            same_ns = namespace is None or item.get("namespace") == namespace
            meta = item.get("metadata") or {}
            same_ver = doc_version is None or str(meta.get("doc_version") or "") == str(doc_version)
            if same_name and same_ns and same_ver:
                delete_ids.append(int(internal_id))

        if not delete_ids:
            return 0

        faiss = self._load_faiss()
        id_selector = faiss.IDSelectorBatch(np.asarray(delete_ids, dtype="int64"))
        self._index.remove_ids(id_selector)

        for internal_id in delete_ids:
            self._items.pop(int(internal_id), None)
        self._persist()
        return len(delete_ids)


class ElasticsearchVectorStore(VectorStore):
    def __init__(self, cfg: ElasticsearchConfig) -> None:
        self._cfg = cfg
        self._client = self._create_client(cfg)
        # 物理索引名采用 version 命名，避免 mapping 变更时直接修改线上索引。
        # 例如：rag_knowledge_base_v1 / rag_knowledge_base_v2
        self._index = f"{cfg.index_name}_v{cfg.index_version}"
        # 逻辑访问入口统一走 alias，业务层无需感知物理索引切换。
        # migration 时仅切换 alias 指向即可。
        self._alias = cfg.index_alias
        self._vector_field = cfg.vector_field
        self._dim: int | None = None
        self._max_retries: int = 3

    @staticmethod
    def _create_client(cfg: ElasticsearchConfig):
        try:
            from elasticsearch import Elasticsearch  # type: ignore[import-untyped]
        except Exception as e:  # noqa: BLE001
            raise ImportError(
                "elasticsearch client is required for ES/EasySearch vector store. "
                "Install with: pip install -r requirements-大模型应用.txt"
            ) from e
        auth = None
        if cfg.username and cfg.password:
            auth = (cfg.username, cfg.password)
        return Elasticsearch(
            hosts=cfg.hosts,
            basic_auth=auth,
            api_key=cfg.api_key,
            verify_certs=cfg.verify_certs,
            request_timeout=cfg.request_timeout,
        )

    def _ensure_index(self, dim: int) -> None:
        if self._with_retry(lambda: self._client.indices.exists(index=self._index)):
            if self._dim is None:
                self._dim = dim
            if self._cfg.auto_migrate_on_start:
                self._ensure_alias_points_to_current_index()
            return
        mapping = {
            "settings": {"analysis": {"analyzer": {"default": {"type": "standard"}}}},
            "mappings": {
                "properties": {
                    "text": {"type": "text"},
                    "namespace": {"type": "keyword"},
                    "doc_name": {"type": "keyword"},
                    "ext_id": {"type": "keyword"},
                    "metadata": {"type": "object", "enabled": True},
                    self._vector_field: {
                        "type": "dense_vector",
                        "dims": dim,
                        "index": True,
                        "similarity": "cosine",
                    },
                }
            },
        }
        self._with_retry(lambda: self._client.indices.create(index=self._index, body=mapping))
        self._dim = dim
        if self._cfg.auto_migrate_on_start:
            self._ensure_alias_points_to_current_index()
        logger.info("created ES index %s and ensured alias %s", self._index, self._alias)

    def _ensure_alias_points_to_current_index(self) -> None:
        """
        轻量 migration 策略（生产可用最小集）：
        1. 读取 alias 当前绑定的物理索引；
        2. 若 alias 未绑定当前版本索引，则将 alias 切换到当前版本索引；
        3. 不删除旧索引，避免误删历史数据，迁移数据可由离线任务回灌。
        """
        try:
            alias_info = self._with_retry(lambda: self._client.indices.get_alias(name=self._alias))
            bound_indices = list(alias_info.keys())
        except Exception:
            bound_indices = []

        if bound_indices == [self._index]:
            return

        actions: list[dict[str, Any]] = []
        for old_idx in bound_indices:
            actions.append({"remove": {"index": old_idx, "alias": self._alias}})
        actions.append({"add": {"index": self._index, "alias": self._alias}})
        self._with_retry(lambda: self._client.indices.update_aliases(body={"actions": actions}))
        logger.warning(
            "ES alias switched for migration: alias=%s -> index=%s (previous=%s). "
            "If data backfill is needed, run re-ingestion/reindex job.",
            self._alias,
            self._index,
            bound_indices,
        )

    def add_texts(
        self,
        texts: Sequence[str],
        embeddings: Sequence[Sequence[float]],
        ids: Sequence[str] | None = None,
        namespace: str | None = None,
        doc_name: str | None = None,
        metadatas: Sequence[dict[str, Any] | None] | None = None,
    ) -> List[str]:
        if len(texts) != len(embeddings):
            raise ValueError("texts and embeddings length mismatch")
        if ids is not None and len(ids) != len(texts):
            raise ValueError("ids length mismatch")
        if metadatas is not None and len(metadatas) != len(texts):
            raise ValueError("metadatas length mismatch")
        if not texts:
            return []
        dim = len(embeddings[0])
        self._ensure_index(dim)
        ext_ids = [str(uuid.uuid4()) for _ in range(len(texts))] if ids is None else list(ids)
        try:
            from elasticsearch.helpers import bulk  # type: ignore[import-untyped]
        except Exception as e:  # noqa: BLE001
            raise ImportError("elasticsearch.helpers.bulk unavailable") from e
        actions = []
        for i, text in enumerate(texts):
            actions.append(
                {
                    "_op_type": "index",
                    "_index": self._index,
                    "_id": ext_ids[i],
                    "_source": {
                        "text": text,
                        "namespace": namespace,
                        "doc_name": doc_name,
                        "ext_id": ext_ids[i],
                        "metadata": (metadatas[i] if metadatas is not None else None) or {},
                        self._vector_field: list(embeddings[i]),
                    },
                }
            )
        self._with_retry(lambda: bulk(self._client, actions, refresh=True))
        return ext_ids

    def similarity_search_by_vector(
        self,
        vector: Sequence[float],
        k: int = 5,
        namespace: str | None = None,
    ) -> List[dict[str, Any]]:
        if not self._with_retry(lambda: self._client.indices.exists(index=self._alias)):
            return []
        filters: list[dict[str, Any]] = []
        if namespace is not None:
            filters.append({"term": {"namespace": namespace}})
        body = {
            "size": k,
            "query": {
                "script_score": {
                    "query": {"bool": {"filter": filters}} if filters else {"match_all": {}},
                    "script": {
                        "source": f"cosineSimilarity(params.qv, '{self._vector_field}') + 1.0",
                        "params": {"qv": list(vector)},
                    },
                }
            },
        }
        resp = self._with_retry(lambda: self._client.search(index=self._alias, body=body))
        return [self._hit_to_result(hit) for hit in resp.get("hits", {}).get("hits", [])]

    def keyword_search(
        self,
        query: str,
        k: int = 5,
        namespace: str | None = None,
    ) -> List[dict[str, Any]]:
        if not self._with_retry(lambda: self._client.indices.exists(index=self._alias)):
            return []
        bool_query: dict[str, Any] = {"must": [{"match": {"text": query}}]}
        if namespace is not None:
            bool_query["filter"] = [{"term": {"namespace": namespace}}]
        body = {"size": k, "query": {"bool": bool_query}}
        resp = self._with_retry(lambda: self._client.search(index=self._alias, body=body))
        return [self._hit_to_result(hit) for hit in resp.get("hits", {}).get("hits", [])]

    def metadata_search(
        self,
        query: str,
        k: int = 5,
        namespace: str | None = None,
    ) -> List[dict[str, Any]]:
        if not self._with_retry(lambda: self._client.indices.exists(index=self._alias)):
            return []
        bool_query: dict[str, Any] = {
            "should": [
                {"match": {"doc_name": query}},
                {"match": {"metadata.doc_version": query}},
                {"match": {"metadata.tenant_id": query}},
            ],
            "minimum_should_match": 1,
        }
        if namespace is not None:
            bool_query["filter"] = [{"term": {"namespace": namespace}}]
        body = {"size": k, "query": {"bool": bool_query}}
        resp = self._with_retry(lambda: self._client.search(index=self._alias, body=body))
        return [self._hit_to_result(hit) for hit in resp.get("hits", {}).get("hits", [])]

    def delete_by_doc_name(self, doc_name: str, namespace: str | None = None, doc_version: str | None = None) -> int:
        if not self._with_retry(lambda: self._client.indices.exists(index=self._alias)):
            return 0
        must: list[dict[str, Any]] = [{"term": {"doc_name": doc_name}}]
        if namespace is not None:
            must.append({"term": {"namespace": namespace}})
        if doc_version is not None:
            must.append({"term": {"metadata.doc_version": str(doc_version)}})
        body = {"query": {"bool": {"must": must}}}
        resp = self._with_retry(
            lambda: self._client.delete_by_query(
                index=self._alias,
                # 删除走 alias，确保始终作用在当前线上索引版本。
                body=body,
                refresh=True,
                conflicts="proceed",
            )
        )
        return int(resp.get("deleted", 0))

    def _with_retry(self, fn):
        last_err = None
        for attempt in range(1, self._max_retries + 1):
            try:
                return fn()
            except Exception as e:  # noqa: BLE001
                last_err = e
                if attempt >= self._max_retries:
                    break
                sleep_s = 0.2 * attempt
                logger.warning(
                    "ElasticsearchVectorStore operation failed, retrying attempt=%s/%s err=%s",
                    attempt,
                    self._max_retries,
                    e,
                )
                time.sleep(sleep_s)
        raise RuntimeError(f"Elasticsearch operation failed after retries: {last_err}") from last_err

    @staticmethod
    def _hit_to_result(hit: dict[str, Any]) -> dict[str, Any]:
        src = hit.get("_source", {})
        return {
            "text": src.get("text", ""),
            "score": float(hit.get("_score", 0.0)),
            "ext_id": src.get("ext_id") or hit.get("_id"),
            "namespace": src.get("namespace"),
            "doc_name": src.get("doc_name"),
            "metadata": src.get("metadata") or {},
        }


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
        elif store_type in {"es", "elasticsearch", "easysearch"}:
            self._default_store = ElasticsearchVectorStore(cfg=cfg.es)
        elif store_type in {"memory", "inmemory"}:
            self._default_store = InMemoryVectorStore()
        else:
            raise ValueError(f"Unsupported vector_store_type: {cfg.vector_store_type}")

    def get_default_store(self) -> VectorStore:
        return self._default_store

