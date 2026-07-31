from __future__ import annotations

import asyncio
import json
import os
import threading
import uuid
from pathlib import Path
from typing import Any

from app.core.config import get_app_config
from app.core.logging import get_logger
from app.core.metrics import (
    INSPECT_EXTRACT_V0_LLM_LATENCY,
    INSPECT_EXTRACT_V0_PARSE_LATENCY,
    INSPECT_EXTRACT_V0_RECORD_COUNT,
    INSPECT_EXTRACT_V0_REQUEST_COUNT,
)
from app.inspection_extract_v0.graph.pipeline import run_inspection_extract_v0_graph
from app.inspection_extract_v0.irt_table_chunks import count_llm_table_chunks
from app.models.inspection_extract import InspectionRecord, InspectionSummary
from app.models.inspection_extract_v0 import (
    InspectionExtractV0AsyncSubmitResponse,
    InspectionExtractV0CancelResponse,
    InspectionExtractV0ChunkListItem,
    InspectionExtractV0ChunkListResponse,
    InspectionExtractV0ChunkRecordsResponse,
    InspectionExtractV0JobMetrics,
    InspectionExtractV0JobStatusResponse,
    InspectionExtractV0Request,
    InspectionExtractV0Response,
    InspectionExtractV0StageLatencyMs,
    InspectionExtractV0Trace,
    InspectionExtractV0UploadResponse,
)
from app.rag.models import utcnow_iso
from app.rag.ingestion_job_queue import IngestionJobQueue
from app.services.inspection_extract_llm_orchestrator import InspectionExtractJobCancelled
from app.services.inspection_extract_service import (
    INSPECTION_JOB_CANCEL_MARKER,
    InspectionExtractService,
    _atomic_write_json,
    _max_chunk_index,
    _read_meta,
)

logger = get_logger(__name__)

V0_REQUEST_FILENAME = "request_v0.json"


class InspectionExtractV0Service:
    """检修 V0：同步提取 + 异步任务（独立 Redis 前缀）。"""

    def __init__(self) -> None:
        self._cfg = get_app_config().inspection_extract_v0
        self._upload = InspectionExtractService()
        self._job_sched: InspectionExtractV0JobScheduler | None = None

    @property
    def job_scheduler(self) -> "InspectionExtractV0JobScheduler":
        if self._job_sched is None:
            self._job_sched = InspectionExtractV0JobScheduler(self)
        return self._job_sched

    async def upload_file(self, *, file_name: str, content: bytes, content_type: str | None = None) -> InspectionExtractV0UploadResponse:
        return await self._upload.upload_file(file_name=file_name, content=content, content_type=content_type)

    def _graph_result_to_response(self, fin: dict[str, Any], *, req: InspectionExtractV0Request) -> InspectionExtractV0Response:
        rows = fin.get("validated_records") or []
        records: list[InspectionRecord] = []
        for row in rows:
            if isinstance(row, dict):
                records.append(InspectionRecord.model_validate(row))
        summary = InspectionSummary.model_validate(fin.get("validated_summary") or {"total": 0, "warnings": []})
        sm = fin.get("stage_ms") or {}
        parse_wall = int(sm.get("preprocess", 0)) + int(sm.get("layout_ocr", 0)) + int(sm.get("build_irt", 0))
        llm_ms = int(sm.get("llm", 0))
        post_ms = int(sm.get("postprocess", 0))
        v0cfg = get_app_config().inspection_extract_v0
        model = v0cfg.model_name or get_app_config().llm.default_model
        pv = (req.prompt_version or v0cfg.prompt_version or "v1").strip() or "v1"
        trace = InspectionExtractV0Trace(
            parse_route=str(fin.get("parse_route") or ""),
            llm_model=model,
            prompt_version=f"inspection_extract_v0:{pv}",
            parse_latency_ms=parse_wall,
            llm_latency_ms=llm_ms,
            ocr_engine=fin.get("ocr_engine"),
            layout_engine=fin.get("layout_engine"),
            layout_api_version=fin.get("layout_api_version"),
            stage_latency_ms=InspectionExtractV0StageLatencyMs(
                preprocess=int(sm.get("preprocess", 0)) or None,
                layout_ocr=int(sm.get("layout_ocr", 0)) or None,
                build_irt=int(sm.get("build_irt", 0)) or None,
                llm=llm_ms or None,
                postprocess=post_ms or None,
            ),
            low_confidence=bool(fin.get("low_confidence")),
            review_flags=list(fin.get("review_flags") or []),
        )
        return InspectionExtractV0Response(ok=True, records=records, summary=summary, trace=trace)

    async def extract_from_document(self, req: InspectionExtractV0Request) -> InspectionExtractV0Response:
        INSPECT_EXTRACT_V0_REQUEST_COUNT.labels(status="started").inc()
        import tempfile

        try:
            with tempfile.TemporaryDirectory(prefix="inspect_v0_sync_") as td:
                job_dir = Path(td)
                fin = await run_inspection_extract_v0_graph(
                    job_id=uuid.uuid4().hex,
                    job_dir=job_dir,
                    request=req,
                    should_cancel=lambda: False,
                )
            resp = self._graph_result_to_response(fin, req=req)
            sm = fin.get("stage_ms") or {}
            parse_wall = (int(sm.get("preprocess", 0)) + int(sm.get("layout_ocr", 0)) + int(sm.get("build_irt", 0))) / 1000.0
            INSPECT_EXTRACT_V0_PARSE_LATENCY.observe(parse_wall)
            INSPECT_EXTRACT_V0_LLM_LATENCY.observe(int(sm.get("llm", 0)) / 1000.0)
            INSPECT_EXTRACT_V0_RECORD_COUNT.inc(len(resp.records))
            INSPECT_EXTRACT_V0_REQUEST_COUNT.labels(status="success").inc()
            return resp
        except Exception:
            INSPECT_EXTRACT_V0_REQUEST_COUNT.labels(status="failed").inc()
            raise

    def submit_async_job(self, req: InspectionExtractV0Request) -> InspectionExtractV0AsyncSubmitResponse:
        job_id = self.job_scheduler.submit_new_job(req)
        return InspectionExtractV0AsyncSubmitResponse(
            ok=True,
            job_id=job_id,
            job_status_path=f"/inspection-extract-v0/jobs/{job_id}",
        )

    def recover_async_jobs_on_startup(self) -> None:
        self.job_scheduler.recover_pending_on_startup()

    def get_job_status(self, job_id: str) -> InspectionExtractV0JobStatusResponse | None:
        return self.job_scheduler.get_public_status(job_id)

    def list_job_chunks(self, job_id: str) -> InspectionExtractV0ChunkListResponse | None:
        return self.job_scheduler.list_chunks(job_id)

    def get_job_chunk_records(self, job_id: str, work_idx: int) -> InspectionExtractV0ChunkRecordsResponse | None:
        return self.job_scheduler.get_chunk_payload(job_id, work_idx)

    def cancel_async_job(self, job_id: str) -> InspectionExtractV0CancelResponse:
        return self.job_scheduler.cancel_job(job_id)


class InspectionExtractV0JobScheduler:
    """V0 异步：Redis 前缀 `inspection:extract:v0:jobs`，与现网队列隔离。"""

    def __init__(self, service: InspectionExtractV0Service) -> None:
        self._svc = service
        root = Path(get_app_config().inspection_extract.async_jobs_state_dir)
        self._root = root
        self._root.mkdir(parents=True, exist_ok=True)
        redis_url = os.getenv("REDIS_URL") or None
        self._queue = IngestionJobQueue(redis_url=redis_url, key_prefix="inspection:extract:v0:jobs")
        self._max_workers = max(1, int(getattr(get_app_config().inspection_extract_v0, "async_queue_workers", 2)))
        self._stop_event = threading.Event()
        self._worker_threads: list[threading.Thread] = []
        self._startup_recovery_completed = False
        self.recover_pending_on_startup()
        self._start_workers()
        if self._queue.enabled:
            logger.info(
                "inspection_extract_v0 async Redis queue enabled workers=%s prefix=inspection:extract:v0:jobs",
                self._max_workers,
            )

    def shutdown_workers(self, *, join_timeout_s: float = 2.0) -> None:
        self._stop_event.set()
        for t in self._worker_threads:
            t.join(timeout=join_timeout_s)

    def _start_workers(self) -> None:
        if not self._queue.enabled:
            return
        for i in range(self._max_workers):
            t = threading.Thread(
                target=self._queue_worker_loop,
                name=f"inspect-extract-v0-qw-{i}",
                daemon=True,
            )
            t.start()
            self._worker_threads.append(t)

    def _queue_worker_loop(self) -> None:
        while not self._stop_event.is_set():
            try:
                job_id = self._queue.pop(block_timeout_s=2)
                if not job_id:
                    continue
                if not self._queue.acquire_lease(job_id):
                    self._queue.nack_requeue(job_id)
                    self._queue.sleep_briefly(0.2)
                    continue
                try:
                    jd = self._job_dir(job_id)
                    if not jd.is_dir():
                        self._queue.ack(job_id)
                        continue
                    meta = _read_meta(jd)
                    if meta is None:
                        self._queue.ack(job_id)
                        continue
                    if str(meta.get("pipeline") or "") != "v0":
                        logger.warning("inspection_extract_v0 skip non-v0 job in v0 queue job_id=%s", job_id)
                        self._queue.ack(job_id)
                        continue
                    if meta.get("status") in {"completed", "failed", "cancelled"}:
                        self._queue.ack(job_id)
                        continue
                    asyncio.run(self._run_job_guarded(job_id))
                    self._queue.ack(job_id)
                finally:
                    self._queue.release_lease(job_id)
            except Exception:  # noqa: BLE001
                logger.exception("inspection_extract_v0 queue worker loop error")
                self._queue.sleep_briefly(0.5)

    def _thread_run_job(self, job_id: str) -> None:
        try:
            asyncio.run(self._run_job_guarded(job_id))
        except Exception:  # noqa: BLE001
            logger.exception("inspection_extract_v0 local thread job crashed job_id=%s", job_id)

    def _start_local_job_thread(self, job_id: str) -> None:
        t = threading.Thread(
            target=self._thread_run_job,
            args=(job_id,),
            daemon=True,
            name=f"inspect-extract-v0-{job_id[:8]}",
        )
        t.start()

    def _job_dir(self, job_id: str) -> Path:
        return self._root / job_id

    def _is_cancel_requested(self, jd: Path, job_id: str) -> bool:
        if (jd / INSPECTION_JOB_CANCEL_MARKER).is_file():
            return True
        return self._queue.is_cancel_signaled(job_id)

    def _finalize_terminal_cancelled(self, jd: Path, message: str) -> None:
        meta = _read_meta(jd)
        if not meta:
            return
        if str(meta.get("status") or "").lower() in {"completed", "failed", "cancelled"}:
            return
        meta["status"] = "cancelled"
        meta["step"] = "done"
        meta["finished_at"] = utcnow_iso()
        meta["updated_at"] = utcnow_iso()
        meta["error_code"] = "E_USER_CANCELLED"
        meta["error_message"] = (message or "cancelled")[:2000]
        _atomic_write_json(jd / "job_meta.json", meta)
        fr = jd / "final_response.json"
        if not fr.is_file():
            fr.write_text(json.dumps({"ok": False, "error": "cancelled"}, ensure_ascii=False), encoding="utf-8")
        jid = str(meta.get("job_id") or jd.name)
        self._queue.clear_cancel_signal(jid)
        try:
            (jd / INSPECTION_JOB_CANCEL_MARKER).unlink(missing_ok=True)
        except OSError:
            pass

    def cancel_job(self, job_id: str) -> InspectionExtractV0CancelResponse:
        job_id = (job_id or "").strip()
        if not job_id:
            return InspectionExtractV0CancelResponse(
                ok=False, job_id=job_id, outcome="not_found", message="empty job_id"
            )
        jd = self._job_dir(job_id)
        meta = _read_meta(jd)
        if meta is None:
            return InspectionExtractV0CancelResponse(
                ok=False, job_id=job_id, outcome="not_found", message="job not found"
            )
        if str(meta.get("pipeline") or "") != "v0":
            return InspectionExtractV0CancelResponse(
                ok=False, job_id=job_id, outcome="not_found", message="not a v0 job"
            )
        st = str(meta.get("status") or "").lower()
        if st in {"completed", "failed", "cancelled"}:
            return InspectionExtractV0CancelResponse(
                ok=False, job_id=job_id, outcome="already_terminal", message=st
            )
        marker = jd / INSPECTION_JOB_CANCEL_MARKER
        try:
            marker.write_text(utcnow_iso(), encoding="utf-8")
        except OSError:
            logger.warning("inspection_extract_v0 cancel write marker failed job_id=%s", job_id, exc_info=True)
        self._queue.set_cancel_signal(job_id)
        if self._queue.enabled:
            self._queue.purge_job_queue_state(job_id)
        meta2 = _read_meta(jd) or meta
        st_after = str(meta2.get("status") or "").lower()
        if st_after == "pending":
            meta2["status"] = "cancelled"
            meta2["step"] = "cancelled_before_run"
            meta2["finished_at"] = utcnow_iso()
            meta2["updated_at"] = utcnow_iso()
            meta2["error_code"] = "E_USER_CANCELLED"
            meta2["error_message"] = "Cancelled while pending (removed from queue)."
            _atomic_write_json(jd / "job_meta.json", meta2)
            self._queue.clear_cancel_signal(job_id)
            try:
                marker.unlink(missing_ok=True)
            except OSError:
                pass
            fr = jd / "final_response.json"
            if not fr.is_file():
                fr.write_text(
                    json.dumps({"ok": False, "error": "cancelled"}, ensure_ascii=False),
                    encoding="utf-8",
                )
            return InspectionExtractV0CancelResponse(
                ok=True,
                job_id=job_id,
                outcome="cancel_accepted",
                message="任务尚未进入 running，已从队列移除并标记为 cancelled。",
            )
        meta2["status"] = "cancelling"
        meta2["step"] = "user_cancel_requested"
        meta2["updated_at"] = utcnow_iso()
        _atomic_write_json(jd / "job_meta.json", meta2)
        return InspectionExtractV0CancelResponse(
            ok=True,
            job_id=job_id,
            outcome="cancel_accepted",
            message="已请求取消；worker 在节点边界停止。",
        )

    def submit_new_job(self, req: InspectionExtractV0Request) -> str:
        job_id = uuid.uuid4().hex
        jd = self._job_dir(job_id)
        jd.mkdir(parents=True, exist_ok=False)
        (jd / V0_REQUEST_FILENAME).write_text(req.model_dump_json(), encoding="utf-8")
        now = utcnow_iso()
        meta = {
            "job_id": job_id,
            "pipeline": "v0",
            "status": "pending",
            "step": "queued",
            "created_at": now,
            "updated_at": now,
            "finished_at": None,
            "error_code": None,
            "error_message": None,
            "metrics": {"pipeline": "v0", "chunks_total": 0, "chunks_done": 0},
            "langgraph_thread_id": job_id,
        }
        _atomic_write_json(jd / "job_meta.json", meta)
        if self._queue.enabled:
            self._queue.enqueue(job_id)
        else:
            self._start_local_job_thread(job_id)
        return job_id

    def recover_pending_on_startup(self) -> None:
        if self._startup_recovery_completed:
            return
        if self._queue.enabled:
            moved = self._queue.requeue_processing_on_startup()
            if moved > 0:
                logger.warning("inspection_extract_v0 Redis queue startup recovery requeued_processing=%s", moved)
        self._root.mkdir(parents=True, exist_ok=True)
        requeued = 0
        for sub in self._root.iterdir():
            if not sub.is_dir():
                continue
            mp = sub / "job_meta.json"
            if not mp.is_file():
                continue
            try:
                meta = json.loads(mp.read_text(encoding="utf-8"))
            except Exception:  # noqa: BLE001
                continue
            if str(meta.get("pipeline") or "") != "v0":
                continue
            jid = str(meta.get("job_id") or sub.name)
            st = str(meta.get("status") or "").lower()
            if st == "running":
                meta["status"] = "pending"
                meta["step"] = "requeued_after_restart"
                meta["error_code"] = "E_RESTART_RECOVERED"
                meta["error_message"] = "Recovered RUNNING v0 job after service restart and re-queued."
                meta["finished_at"] = None
                meta["updated_at"] = utcnow_iso()
                _atomic_write_json(mp, meta)
                st = "pending"
            if st == "pending":
                if self._queue.enabled:
                    if self._queue.enqueue(jid):
                        requeued += 1
                else:
                    self._start_local_job_thread(jid)
                    requeued += 1
        if requeued > 0:
            logger.warning("inspection_extract_v0 startup recovery jobs_scheduled=%s redis=%s", requeued, self._queue.enabled)
        self._startup_recovery_completed = True

    async def _run_job_guarded(self, job_id: str) -> None:
        try:
            await self._run_job(job_id)
        except InspectionExtractJobCancelled:
            jd = self._job_dir(job_id)
            self._finalize_terminal_cancelled(jd, "Cancelled during execution (user requested).")
        except Exception as exc:  # noqa: BLE001
            logger.exception("inspection_extract_v0 async job crashed job_id=%s", job_id)
            INSPECT_EXTRACT_V0_REQUEST_COUNT.labels(status="failed").inc()
            jd = self._job_dir(job_id)
            meta = _read_meta(jd)
            if meta and meta.get("status") not in {"completed", "failed", "cancelled"}:
                meta["status"] = "failed"
                meta["step"] = "error"
                meta["error_code"] = type(exc).__name__
                meta["error_message"] = (str(exc) or "unknown")[:2000]
                meta["finished_at"] = utcnow_iso()
                meta["updated_at"] = utcnow_iso()
                _atomic_write_json(jd / "job_meta.json", meta)
            try:
                from app.observability.trace_recorder import TraceRecorder

                tr = TraceRecorder.start(
                    module="inspection_extract",
                    request_id=job_id,
                    kind="job",
                    scene="async_extract_v0",
                    meta={"job_id": job_id, "pipeline": "v0", "error": str(exc)[:500]},
                )
                tr.record_node("running_graph", status="failed", error=str(exc)[:500])
                tr.add_degrade("extract_v0_failed")
                tr.finalize(status="failed", summary=str(exc)[:200])
            except Exception:  # noqa: BLE001
                pass

    async def _run_job(self, job_id: str) -> None:
        jd = self._job_dir(job_id)
        if not jd.is_dir():
            return
        meta0 = _read_meta(jd)
        if meta0 is None:
            return
        if str(meta0.get("pipeline") or "") != "v0":
            return
        st0 = str(meta0.get("status") or "").lower()
        if st0 in {"completed", "failed", "cancelled"}:
            return
        if st0 == "cancelling" or self._is_cancel_requested(jd, job_id):
            self._finalize_terminal_cancelled(jd, "Cancelled before job execution continued.")
            return
        meta0["status"] = "running"
        meta0["step"] = "running_graph"
        meta0["updated_at"] = utcnow_iso()
        _atomic_write_json(jd / "job_meta.json", meta0)

        req = InspectionExtractV0Request.model_validate(
            json.loads((jd / V0_REQUEST_FILENAME).read_text(encoding="utf-8"))
        )
        v0cfg = get_app_config().inspection_extract_v0
        llm_model = v0cfg.model_name or get_app_config().llm.default_model
        pv = (req.prompt_version or v0cfg.prompt_version or "v1").strip() or "v1"

        fin = await run_inspection_extract_v0_graph(
            job_id=job_id,
            job_dir=jd,
            request=req,
            should_cancel=lambda: self._is_cancel_requested(jd, job_id),
        )
        resp = self._svc._graph_result_to_response(fin, req=req)
        sm = fin.get("stage_ms") or {}
        parse_wall_ms = int(sm.get("preprocess", 0)) + int(sm.get("layout_ocr", 0)) + int(sm.get("build_irt", 0))
        n_chunks = max(1, int(fin.get("llm_chunks_total") or 1))
        metrics = InspectionExtractV0JobMetrics(
            pipeline="v0",
            parse_route=str(fin.get("parse_route") or ""),
            llm_model=llm_model,
            prompt_version=pv,
            parse_latency_ms=parse_wall_ms,
            llm_latency_ms=int(sm.get("llm", 0)),
            irt_build_ms=int(sm.get("build_irt", 0)),
            ocr_engine=fin.get("ocr_engine"),
            layout_engine=fin.get("layout_engine"),
            layout_api_version=fin.get("layout_api_version"),
            langgraph_thread_id=job_id,
            chunks_total=n_chunks,
            chunks_done=n_chunks,
        )
        meta = _read_meta(jd) or {}
        meta["status"] = "completed"
        meta["step"] = "done"
        meta["finished_at"] = utcnow_iso()
        meta["updated_at"] = utcnow_iso()
        meta["metrics"] = metrics.model_dump(mode="json")
        _atomic_write_json(jd / "job_meta.json", meta)
        (jd / "final_response.json").write_text(resp.model_dump_json(), encoding="utf-8")
        try:
            from app.observability.trace_recorder import TraceRecorder

            tr = TraceRecorder.start(
                module="inspection_extract",
                request_id=job_id,
                kind="job",
                scene="async_extract_v0",
                meta={"job_id": job_id, "pipeline": "v0"},
            )
            for stage, ms in (sm or {}).items():
                tr.record_node(str(stage), latency_ms=int(ms or 0))
            tr.finalize(status="success")
        except Exception:  # noqa: BLE001
            pass
        ch = jd / "chunks"
        ch.mkdir(parents=True, exist_ok=True)
        chunk_outputs = fin.get("llm_chunk_outputs")
        if isinstance(chunk_outputs, list) and chunk_outputs:
            for co in chunk_outputs:
                if not isinstance(co, dict):
                    continue
                try:
                    wi = int(co.get("work_idx") or 0)
                except (TypeError, ValueError):
                    continue
                if wi < 1:
                    continue
                recs = co.get("records") if isinstance(co.get("records"), list) else []
                payload = {
                    "work_idx": wi,
                    "table_id": co.get("table_id"),
                    "records": recs,
                    "raw_fragment": str(co.get("raw") or "")[:8000],
                }
                (ch / f"{wi}.json").write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        else:
            rec_dump = [r.model_dump(mode="json", by_alias=True) for r in resp.records]
            (ch / "1.json").write_text(json.dumps({"records": rec_dump}, ensure_ascii=False), encoding="utf-8")
        INSPECT_EXTRACT_V0_PARSE_LATENCY.observe(parse_wall_ms / 1000.0)
        INSPECT_EXTRACT_V0_LLM_LATENCY.observe(int(sm.get("llm", 0)) / 1000.0)
        INSPECT_EXTRACT_V0_RECORD_COUNT.inc(len(resp.records))
        INSPECT_EXTRACT_V0_REQUEST_COUNT.labels(status="success").inc()

    def get_public_status(self, job_id: str) -> InspectionExtractV0JobStatusResponse | None:
        jd = self._job_dir(job_id)
        meta = _read_meta(jd)
        if meta is None or str(meta.get("pipeline") or "") != "v0":
            return None
        metrics = InspectionExtractV0JobMetrics(**(meta.get("metrics") or {}))
        result: InspectionExtractV0Response | None = None
        fin_path = jd / "final_response.json"
        if fin_path.is_file() and meta.get("status") == "completed":
            try:
                result = InspectionExtractV0Response.model_validate(json.loads(fin_path.read_text(encoding="utf-8")))
            except Exception:  # noqa: BLE001
                result = None
        return InspectionExtractV0JobStatusResponse(
            job_id=str(meta.get("job_id") or job_id),
            status=str(meta.get("status") or "unknown"),
            step=str(meta.get("step") or ""),
            created_at=str(meta.get("created_at") or ""),
            updated_at=str(meta.get("updated_at") or ""),
            finished_at=meta.get("finished_at"),
            error_code=meta.get("error_code"),
            error_message=meta.get("error_message"),
            metrics=metrics,
            result=result,
        )

    def list_chunks(self, job_id: str) -> InspectionExtractV0ChunkListResponse | None:
        jd = self._job_dir(job_id)
        if not jd.is_dir():
            return None
        meta = _read_meta(jd)
        if meta is None or str(meta.get("pipeline") or "") != "v0":
            return None
        total = int((meta.get("metrics") or {}).get("chunks_total") or 0)
        if total <= 0:
            it = jd / "artifacts" / "irt.json"
            if it.is_file():
                try:
                    irt = json.loads(it.read_text(encoding="utf-8"))
                    total = count_llm_table_chunks(irt)
                except Exception:  # noqa: BLE001
                    total = 1
            else:
                total = 1
        items: list[InspectionExtractV0ChunkListItem] = []
        for w in range(1, max(total, _max_chunk_index(jd)) + 1):
            fp = jd / "chunks" / f"{w}.json"
            if fp.is_file():
                try:
                    data = json.loads(fp.read_text(encoding="utf-8"))
                    recs = data.get("records") if isinstance(data, dict) else None
                    n = len(recs) if isinstance(recs, list) else 0
                except Exception:  # noqa: BLE001
                    n = 0
                items.append(InspectionExtractV0ChunkListItem(work_idx=w, status="done", record_count=n))
            else:
                items.append(InspectionExtractV0ChunkListItem(work_idx=w, status="pending", record_count=0))
        return InspectionExtractV0ChunkListResponse(job_id=job_id, chunks=items)

    def get_chunk_payload(self, job_id: str, work_idx: int) -> InspectionExtractV0ChunkRecordsResponse | None:
        if work_idx < 1:
            return None
        jd = self._job_dir(job_id)
        if not jd.is_dir() or _read_meta(jd) is None:
            return None
        fp = jd / "chunks" / f"{work_idx}.json"
        if not fp.is_file():
            return None
        try:
            data = json.loads(fp.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            return None
        recs = data.get("records") if isinstance(data, dict) else []
        if not isinstance(recs, list):
            recs = []
        public_rows: list[dict[str, Any]] = []
        for row in recs:
            if not isinstance(row, dict):
                continue
            y = dict(row)
            y.pop("evidence", None)
            y.pop("warnings", None)
            public_rows.append(y)
        return InspectionExtractV0ChunkRecordsResponse(job_id=job_id, work_idx=work_idx, records=public_rows)
