from __future__ import annotations

"""
Graph 重建异步任务（进程内队列 + 后台线程，无需 Redis）。
"""

import threading
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from app.core.logging import get_logger
from app.graph.admin_service import GraphAdminService

logger = get_logger(__name__)


class GraphRebuildJobStatus(str, Enum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    SUCCESS = "SUCCESS"
    FAILED = "FAILED"


@dataclass
class GraphRebuildJob:
    job_id: str
    status: GraphRebuildJobStatus
    mode: str
    namespace: str | None
    doc_names: list[str]
    created_at: str
    updated_at: str
    result: dict[str, Any] | None = None
    error_message: str | None = None
    metrics: dict[str, Any] = field(default_factory=dict)


class GraphRebuildJobRunner:
    """单例后台执行器。"""

    _instance: GraphRebuildJobRunner | None = None
    _lock = threading.Lock()

    def __init__(self) -> None:
        self._jobs: dict[str, GraphRebuildJob] = {}
        self._jobs_lock = threading.Lock()
        self._worker_lock = threading.Lock()

    @classmethod
    def get_default(cls) -> GraphRebuildJobRunner:
        with cls._lock:
            if cls._instance is None:
                cls._instance = cls()
            return cls._instance

    def submit(
        self,
        *,
        mode: str,
        namespace: str | None,
        doc_names: list[str] | None,
    ) -> GraphRebuildJob:
        from app.rag.models import utcnow_iso

        job_id = f"graph_rebuild_{uuid.uuid4().hex[:16]}"
        now = utcnow_iso()
        job = GraphRebuildJob(
            job_id=job_id,
            status=GraphRebuildJobStatus.PENDING,
            mode=mode,
            namespace=namespace,
            doc_names=list(doc_names or []),
            created_at=now,
            updated_at=now,
        )
        with self._jobs_lock:
            self._jobs[job_id] = job
        thread = threading.Thread(
            target=self._run_job,
            args=(job_id,),
            name=f"graph-rebuild-{job_id}",
            daemon=True,
        )
        thread.start()
        return job

    def get_job(self, job_id: str) -> GraphRebuildJob | None:
        with self._jobs_lock:
            return self._jobs.get(job_id)

    def list_jobs(self, limit: int = 20) -> list[GraphRebuildJob]:
        with self._jobs_lock:
            jobs = list(self._jobs.values())
        jobs.sort(key=lambda j: j.updated_at, reverse=True)
        return jobs[: max(1, limit)]

    def _run_job(self, job_id: str) -> None:
        from app.rag.models import utcnow_iso

        with self._worker_lock:
            job = self.get_job(job_id)
            if job is None:
                return
            job.status = GraphRebuildJobStatus.RUNNING
            job.updated_at = utcnow_iso()
            try:
                svc = GraphAdminService()
                result = svc.rebuild(
                    mode=job.mode,
                    namespace=job.namespace,
                    doc_names=job.doc_names or None,
                )
                job.result = result
                job.status = GraphRebuildJobStatus.SUCCESS
                job.metrics = {
                    "rebuilt_docs": result.get("rebuilt_docs", 0),
                    "rebuilt_chunks": result.get("rebuilt_chunks", 0),
                    "skipped_docs": result.get("skipped_docs", 0),
                }
            except Exception as exc:  # noqa: BLE001
                logger.exception("graph rebuild job failed job_id=%s", job_id)
                job.status = GraphRebuildJobStatus.FAILED
                job.error_message = str(exc)
            finally:
                job.updated_at = utcnow_iso()
                # 观测旁路
                try:
                    from app.observability.trace_recorder import TraceRecorder

                    st = "success" if job.status == GraphRebuildJobStatus.SUCCESS else "failed"
                    tr = TraceRecorder.start(
                        module="graph_rebuild",
                        request_id=job_id,
                        kind="job",
                        scene=str(job.mode),
                        meta={"namespace": job.namespace, "job_id": job_id},
                    )
                    tr.record_node("rebuild", status=st, error=job.error_message)
                    if job.error_message:
                        tr.add_degrade("rebuild_failed")
                    tr.finalize(status=st)  # type: ignore[arg-type]
                except Exception:  # noqa: BLE001
                    pass
