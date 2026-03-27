from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from app.core.config import get_app_config
from app.core.logging import get_logger

logger = get_logger(__name__)


class JobRepository:
    """
    任务元数据仓库：
    - ES/EasySearch 模式：写入 jobs 索引（版本化 + alias）
    - 非 ES 模式：回退到本地 JSON 文件
    """

    def __init__(self, state_dir: str = "./data/rag_jobs") -> None:
        cfg = get_app_config().rag
        self._use_es = (cfg.vector_store_type or "").lower() in {"es", "elasticsearch", "easysearch"}
        self._file_path = Path(state_dir) / "jobs_index.json"
        self._file_path.parent.mkdir(parents=True, exist_ok=True)
        self._es_cfg = cfg.es
        self._client = None
        self._index = f"{self._es_cfg.jobs_index_name}_v{self._es_cfg.jobs_index_version}"
        self._alias = self._es_cfg.jobs_index_alias
        if self._use_es:
            self._init_es()

    def _init_es(self) -> None:
        try:
            from elasticsearch import Elasticsearch  # type: ignore[import-untyped]
        except Exception as e:  # noqa: BLE001
            logger.warning("job repository fallback to file: elasticsearch missing err=%s", e)
            self._use_es = False
            return
        auth = None
        if self._es_cfg.username and self._es_cfg.password:
            auth = (self._es_cfg.username, self._es_cfg.password)
        self._client = Elasticsearch(
            hosts=self._es_cfg.hosts,
            basic_auth=auth,
            api_key=self._es_cfg.api_key,
            verify_certs=self._es_cfg.verify_certs,
            request_timeout=self._es_cfg.request_timeout,
        )
        self._ensure_index_and_alias()

    def _ensure_index_and_alias(self) -> None:
        assert self._client is not None
        if not self._client.indices.exists(index=self._index):
            mapping = {
                "mappings": {
                    "properties": {
                        "job_id": {"type": "keyword"},
                        "job_type": {"type": "keyword"},
                        "idempotency_key": {"type": "keyword"},
                        "status": {"type": "keyword"},
                        "step": {"type": "keyword"},
                        "created_at": {"type": "date"},
                        "updated_at": {"type": "date"},
                        "finished_at": {"type": "date"},
                        "error_code": {"type": "keyword"},
                        "error_message": {"type": "text"},
                        "metrics": {"type": "object", "enabled": True},
                        "operator": {"type": "keyword"},
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

    def upsert(self, job_id: str, payload: Dict[str, Any]) -> None:
        if self._use_es and self._client is not None:
            self._client.index(index=self._alias, id=job_id, document=payload, refresh=True)
            return
        state = self._load_file_state()
        state[job_id] = payload
        self._save_file_state(state)

    def get(self, job_id: str) -> Optional[Dict[str, Any]]:
        if self._use_es and self._client is not None:
            try:
                res = self._client.get(index=self._alias, id=job_id)
                return res.get("_source") or None
            except Exception:
                return None
        state = self._load_file_state()
        return state.get(job_id)

    def list(self, limit: int = 20, offset: int = 0) -> List[Dict[str, Any]]:
        if self._use_es and self._client is not None:
            body = {
                "from": max(offset, 0),
                "size": max(limit, 1),
                "sort": [{"created_at": {"order": "desc"}}],
                "query": {"match_all": {}},
            }
            res = self._client.search(index=self._alias, body=body)
            return [h.get("_source") or {} for h in res.get("hits", {}).get("hits", [])]
        state = self._load_file_state()
        values = list(state.values())
        values.sort(key=lambda x: x.get("created_at", ""), reverse=True)
        start = max(offset, 0)
        end = start + max(limit, 1)
        return values[start:end]

    def trends(self, days: int = 30, granularity: str = "day") -> List[Dict[str, Any]]:
        """
        返回最近 N 天/周的任务趋势统计，供知识库运营看板使用。

        - created_success: FULL 任务成功数量（视为新增）；
        - updated_success: UPSERT 任务成功数量（视为更新）；
        - failed: 任何类型任务的失败数量。
        """
        days = max(1, min(days, 180))
        granularity = "week" if granularity == "week" else "day"
        now = datetime.now(timezone.utc)
        since = now - timedelta(days=days)

        if self._use_es and self._client is not None:
            body = {
                "size": 10000,
                "query": {
                    "range": {
                        "created_at": {
                            "gte": since.isoformat(),
                        }
                    }
                },
                "sort": [{"created_at": {"order": "asc"}}],
            }
            res = self._client.search(index=self._alias, body=body)
            jobs = [h.get("_source") or {} for h in res.get("hits", {}).get("hits", [])]
        else:
            state = self._load_file_state()
            jobs = list(state.values())

        buckets: Dict[str, Dict[str, int]] = {}

        for job in jobs:
            created_raw = job.get("created_at")
            if not created_raw:
                continue
            try:
                created_dt = datetime.fromisoformat(str(created_raw).replace("Z", "+00:00"))
            except Exception:  # noqa: BLE001
                continue
            if created_dt < since:
                continue
            if granularity == "day":
                bucket = created_dt.date().isoformat()
            else:
                iso_year, iso_week, _ = created_dt.isocalendar()
                bucket = f"{iso_year}-W{iso_week:02d}"
            b = buckets.setdefault(bucket, {"created_success": 0, "updated_success": 0, "failed": 0})

            job_type = str(job.get("job_type") or "").upper()
            status = str(job.get("status") or "").upper()
            if status == "FAILED":
                b["failed"] += 1
            if status == "SUCCESS":
                if job_type == "FULL":
                    b["created_success"] += 1
                elif job_type == "UPSERT":
                    b["updated_success"] += 1

        result: List[Dict[str, Any]] = []
        for key in sorted(buckets.keys()):
            item = buckets[key]
            result.append(
                {
                    "bucket": key,
                    "created_success": int(item.get("created_success", 0)),
                    "updated_success": int(item.get("updated_success", 0)),
                    "failed": int(item.get("failed", 0)),
                }
            )
        return result

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

