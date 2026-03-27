from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List

from app.core.config import ElasticsearchConfig
from app.core.logging import get_logger

logger = get_logger(__name__)


@dataclass
class MigrationResult:
    old_indices: List[str]
    new_index: str
    alias: str


class IndexMigrator:
    """
    ES/EasySearch 索引版本迁移器。
    """

    def __init__(self, es_cfg: ElasticsearchConfig, client: Any | None = None) -> None:
        self._cfg = es_cfg
        if client is not None:
            self._client = client
            return
        try:
            from elasticsearch import Elasticsearch  # type: ignore[import-untyped]
        except Exception as e:  # noqa: BLE001
            raise ImportError("elasticsearch client is required for index migration") from e
        auth = None
        if es_cfg.username and es_cfg.password:
            auth = (es_cfg.username, es_cfg.password)
        self._client = Elasticsearch(
            hosts=es_cfg.hosts,
            basic_auth=auth,
            api_key=es_cfg.api_key,
            verify_certs=es_cfg.verify_certs,
            request_timeout=es_cfg.request_timeout,
        )

    def ensure_index_and_alias(self, mapping: Dict[str, Any]) -> MigrationResult:
        index_name = f"{self._cfg.index_name}_v{self._cfg.index_version}"
        alias = self._cfg.index_alias
        if not self._client.indices.exists(index=index_name):
            self._client.indices.create(index=index_name, body=mapping)
            logger.info("created migration target index=%s", index_name)
        try:
            alias_info = self._client.indices.get_alias(name=alias)
            old_indices = list(alias_info.keys())
        except Exception:
            old_indices = []
        actions = []
        for old_idx in old_indices:
            actions.append({"remove": {"index": old_idx, "alias": alias}})
        actions.append({"add": {"index": index_name, "alias": alias}})
        self._client.indices.update_aliases(body={"actions": actions})
        logger.warning("alias switched alias=%s -> %s (old=%s)", alias, index_name, old_indices)
        return MigrationResult(old_indices=old_indices, new_index=index_name, alias=alias)

    def rollback_alias(self, previous_index: str) -> None:
        alias = self._cfg.index_alias
        try:
            alias_info = self._client.indices.get_alias(name=alias)
            old_indices = list(alias_info.keys())
        except Exception:
            old_indices = []
        actions = []
        for idx in old_indices:
            actions.append({"remove": {"index": idx, "alias": alias}})
        actions.append({"add": {"index": previous_index, "alias": alias}})
        self._client.indices.update_aliases(body={"actions": actions})
        logger.warning("alias rollback alias=%s -> %s", alias, previous_index)

