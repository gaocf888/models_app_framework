from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict

from app.core.config import get_app_config
from app.core.logging import get_logger

logger = get_logger(__name__)


class DocumentRepository:
    """
    文档级元数据仓库（docs 索引）。
    """

    def __init__(self, state_dir: str = "./data/rag_jobs") -> None:
        cfg = get_app_config().rag
        self._use_es = (cfg.vector_store_type or "").lower() in {"es", "elasticsearch", "easysearch"}
        self._file_path = Path(state_dir) / "docs_index.json"
        self._file_path.parent.mkdir(parents=True, exist_ok=True)
        self._es_cfg = cfg.es
        self._client = None
        self._index = f"{self._es_cfg.docs_index_name}_v{self._es_cfg.docs_index_version}"
        self._alias = self._es_cfg.docs_index_alias
        if self._use_es:
            self._init_es()

    def _init_es(self) -> None:
        try:
            import elasticsearch as es_module  # type: ignore[import-untyped]
            from elasticsearch import Elasticsearch  # type: ignore[import-untyped]
        except Exception as e:  # noqa: BLE001
            logger.warning("document repository fallback to file: elasticsearch missing err=%s", e)
            self._use_es = False
            return
        auth = None
        if self._es_cfg.username and self._es_cfg.password:
            auth = (self._es_cfg.username, self._es_cfg.password)
        # 兼容 elasticsearch 7.x / 8.x：优先使用 basic_auth，不支持时回退到 http_auth
        kwargs = dict(
            hosts=self._es_cfg.hosts,
            api_key=self._es_cfg.api_key,
            verify_certs=self._es_cfg.verify_certs,
            request_timeout=self._es_cfg.request_timeout,
        )
        version = getattr(es_module, "__version__", (0, 0, 0))
        major = int(version[0]) if isinstance(version, (tuple, list)) and version else 0
        if auth is not None:
            if major >= 8:
                kwargs["basic_auth"] = auth  # type: ignore[assignment]
            else:
                kwargs["http_auth"] = auth  # type: ignore[assignment]
        self._client = Elasticsearch(**kwargs)
        self._ensure_index_and_alias()

    def _ensure_index_and_alias(self) -> None:
        assert self._client is not None
        if not self._client.indices.exists(index=self._index):
            mapping = {
                "mappings": {
                    "properties": {
                        "doc_name": {"type": "keyword"},
                        "doc_version": {"type": "keyword"},
                        "tenant_id": {"type": "keyword"},
                        "dataset_id": {"type": "keyword"},
                        "namespace": {"type": "keyword"},
                        "source_type": {"type": "keyword"},
                        "source_uri": {"type": "keyword"},
                        "chunk_count": {"type": "integer"},
                        "pipeline_version": {"type": "keyword"},
                        "status": {"type": "keyword"},
                        "created_at": {"type": "date"},
                        "updated_at": {"type": "date"},
                        "last_job_id": {"type": "keyword"},
                        "last_job_type": {"type": "keyword"},
                        "last_job_status": {"type": "keyword"},
                        "metadata": {"type": "object", "enabled": True},
                    }
                }
            }
            self._client.indices.create(index=self._index, body=mapping)
        try:
            alias_info = self._client.indices.get_alias(name=self._alias)
            old_indices = list(alias_info.keys())
        except Exception:
            old_indices = []
        actions = [{"remove": {"index": idx, "alias": self._alias}} for idx in old_indices]
        actions.append({"add": {"index": self._index, "alias": self._alias}})
        self._client.indices.update_aliases(body={"actions": actions})

    def upsert(self, doc_key: str, payload: Dict[str, Any]) -> None:
        if self._use_es and self._client is not None:
            self._client.index(index=self._alias, id=doc_key, document=payload, refresh=True)
            return
        state = self._load_file_state()
        state[doc_key] = payload
        self._save_file_state(state)

    def get(self, doc_key: str) -> Dict[str, Any] | None:
        if self._use_es and self._client is not None:
            try:
                res = self._client.get(index=self._alias, id=doc_key)
                return res.get("_source") or None
            except Exception:
                return None
        state = self._load_file_state()
        return state.get(doc_key)

    def list(
        self,
        limit: int = 20,
        offset: int = 0,
        namespace: str | None = None,
        tenant_id: str | None = None,
        dataset_id: str | None = None,
        doc_name: str | None = None,
    ) -> list[Dict[str, Any]]:
        if self._use_es and self._client is not None:
            filters: list[Dict[str, Any]] = []
            if namespace is not None:
                filters.append({"term": {"namespace": namespace}})
            if tenant_id is not None:
                filters.append({"term": {"tenant_id": tenant_id}})
            if dataset_id is not None:
                filters.append({"term": {"dataset_id": dataset_id}})
            if doc_name is not None:
                filters.append({"term": {"doc_name": doc_name}})
            query: Dict[str, Any] = {"match_all": {}} if not filters else {"bool": {"filter": filters}}
            body = {
                "from": max(offset, 0),
                "size": max(limit, 1),
                "sort": [{"updated_at": {"order": "desc"}}],
                "query": query,
            }
            res = self._client.search(index=self._alias, body=body)
            return [h.get("_source") or {} for h in res.get("hits", {}).get("hits", [])]
        state = self._load_file_state()
        values = list(state.values())
        if namespace is not None:
            values = [v for v in values if v.get("namespace") == namespace]
        if tenant_id is not None:
            values = [v for v in values if v.get("tenant_id") == tenant_id]
        if dataset_id is not None:
            values = [v for v in values if v.get("dataset_id") == dataset_id]
        if doc_name is not None:
            values = [v for v in values if v.get("doc_name") == doc_name]
        values.sort(key=lambda x: x.get("updated_at", ""), reverse=True)
        start = max(offset, 0)
        end = start + max(limit, 1)
        return values[start:end]

    def overview(
        self,
        namespace: str | None = None,
        tenant_id: str | None = None,
        dataset_id: str | None = None,
    ) -> Dict[str, Any]:
        """
        返回知识库总览统计，便于管理面做“当前知识库整体情况”展示。
        """
        if self._use_es and self._client is not None:
            filters: list[Dict[str, Any]] = []
            if namespace is not None:
                filters.append({"term": {"namespace": namespace}})
            if tenant_id is not None:
                filters.append({"term": {"tenant_id": tenant_id}})
            if dataset_id is not None:
                filters.append({"term": {"dataset_id": dataset_id}})
            query: Dict[str, Any] = {"match_all": {}} if not filters else {"bool": {"filter": filters}}
            body = {
                "size": 0,
                "query": query,
                "aggs": {
                    "by_namespace": {"terms": {"field": "namespace", "size": 100}},
                    "by_tenant": {"terms": {"field": "tenant_id", "size": 100}},
                    "by_status": {"terms": {"field": "status", "size": 20}},
                    "doc_name_count": {"cardinality": {"field": "doc_name"}},
                },
            }
            res = self._client.search(index=self._alias, body=body)
            aggs = res.get("aggregations", {})
            return {
                "total_documents": int(res.get("hits", {}).get("total", {}).get("value", 0)),
                "total_doc_names": int(aggs.get("doc_name_count", {}).get("value", 0)),
                "by_namespace": [
                    {"key": b.get("key"), "count": int(b.get("doc_count", 0))}
                    for b in (aggs.get("by_namespace", {}).get("buckets") or [])
                ],
                "by_tenant": [
                    {"key": b.get("key"), "count": int(b.get("doc_count", 0))}
                    for b in (aggs.get("by_tenant", {}).get("buckets") or [])
                ],
                "by_status": [
                    {"key": b.get("key"), "count": int(b.get("doc_count", 0))}
                    for b in (aggs.get("by_status", {}).get("buckets") or [])
                ],
            }

        values = self.list(limit=1000000, offset=0, namespace=namespace, tenant_id=tenant_id, dataset_id=dataset_id)
        by_namespace: Dict[str, int] = {}
        by_tenant: Dict[str, int] = {}
        by_status: Dict[str, int] = {}
        doc_names = set()
        for item in values:
            ns = str(item.get("namespace") or "__default__")
            by_namespace[ns] = by_namespace.get(ns, 0) + 1
            t = str(item.get("tenant_id") or "__default__")
            by_tenant[t] = by_tenant.get(t, 0) + 1
            st = str(item.get("status") or "UNKNOWN")
            by_status[st] = by_status.get(st, 0) + 1
            name = item.get("doc_name")
            if name:
                doc_names.add(str(name))
        return {
            "total_documents": len(values),
            "total_doc_names": len(doc_names),
            "by_namespace": [{"key": k, "count": v} for k, v in by_namespace.items()],
            "by_tenant": [{"key": k, "count": v} for k, v in by_tenant.items()],
            "by_status": [{"key": k, "count": v} for k, v in by_status.items()],
        }

    def _load_file_state(self) -> Dict[str, Any]:
        if not self._file_path.exists():
            return {}
        try:
            return json.loads(self._file_path.read_text(encoding="utf-8"))
        except Exception:
            return {}

    def _save_file_state(self, state: Dict[str, Any]) -> None:
        tmp = self._file_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
        tmp.replace(self._file_path)

