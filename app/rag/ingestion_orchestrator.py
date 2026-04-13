from __future__ import annotations

import json
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Dict, List, Optional

from app.core.config import get_app_config
from app.core.logging import get_logger
from app.rag.document_repository import DocumentRepository, make_document_storage_key
from app.rag.document_pipeline import ChunkingConfig, DocumentPipeline
from app.rag.ingestion import RAGIngestionService
from app.rag.job_repository import JobRepository
from app.rag.content_url_fetch import ContentFetchError, materialize_document_content_from_url
from app.rag.mineru_errors import MinerUParseError
from app.rag.mineru_ingest import prepare_pdf_document_for_pipeline
from app.rag.models import DocumentSource, IngestionJob, IngestionJobStatus, IngestionJobType, utcnow_iso

logger = get_logger(__name__)


class IngestionOrchestrator:
    """
    企业级摄入编排器（轻量任务系统）：
    - 任务状态机：PENDING/RUNNING/SUCCESS/FAILED/PARTIAL
    - 步骤：validate_input -> parse -> clean -> chunk -> enrich -> index -> quality_check -> finalize_alias_version
    - 支持失败重试与任务状态持久化（本地 JSON）
    """

    def __init__(self, ingestion_service: RAGIngestionService | None = None, state_dir: str = "./data/rag_jobs") -> None:
        self._ingestion = ingestion_service or RAGIngestionService()
        ingest_cfg = get_app_config().rag.ingestion
        self._default_chunk_cfg = ChunkingConfig(
            chunk_size=ingest_cfg.chunk_size,
            chunk_overlap=ingest_cfg.chunk_overlap,
            min_chunk_size=ingest_cfg.min_chunk_size,
        )
        self._pipeline_version = ingest_cfg.pipeline_version
        self._tenant_id_default = ingest_cfg.tenant_id_default or "__tenant__"
        max_workers = max(1, ingest_cfg.max_concurrency)
        self._executor = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="rag-ingest")
        self._lock = threading.RLock()
        self._jobs: Dict[str, IngestionJob] = {}
        self._state_file = Path(state_dir) / "jobs.json"
        self._state_file.parent.mkdir(parents=True, exist_ok=True)
        self._job_repo = JobRepository(state_dir=state_dir)
        self._doc_repo = DocumentRepository(state_dir=state_dir)
        self._load_state()

    def close(self) -> None:
        self._executor.shutdown(wait=True, cancel_futures=False)

    def submit_job(
        self,
        documents: List[DocumentSource],
        operator: str | None = None,
        chunk_cfg: ChunkingConfig | None = None,
        idempotency_key: str | None = None,
    ) -> str:
        idem_key = idempotency_key or self._build_idempotency_key(documents)
        if idempotency_key:
            with self._lock:
                for existing in self._jobs.values():
                    if (
                        existing.idempotency_key == idem_key
                        and existing.status in {IngestionJobStatus.PENDING, IngestionJobStatus.RUNNING}
                    ):
                        logger.info("reuse existing ingestion job by idempotency key: %s", idem_key)
                        return existing.job_id
        job_id = str(uuid.uuid4())
        job = IngestionJob(
            job_id=job_id,
            status=IngestionJobStatus.PENDING,
            step="created",
            documents=documents,
            job_type=IngestionJobType.UPSERT,
            idempotency_key=idem_key,
            operator=operator,
            metrics={
                "documents_total": len(documents),
                "documents_success": 0,
                "documents_failed": 0,
                "chunks_total": 0,
                "step_durations_ms": {},
            },
        )
        with self._lock:
            self._jobs[job_id] = job
            self._save_state()
        self._executor.submit(self._guarded_run_job, job_id, chunk_cfg)
        return job_id

    def get_job(self, job_id: str) -> Optional[IngestionJob]:
        """
        返回任务状态。优先与 JobRepository（ES / jobs_index.json）对齐：
        多 worker / 多副本时，执行线程只在受理 POST 的进程内；其它进程的内存会停在 RUNNING，
        若不合并持久化视图，轮询会一直看到 RUNNING 且无本进程日志。
        """
        persisted: dict | None = None
        try:
            persisted = self._job_repo.get(job_id)
        except Exception:  # noqa: BLE001
            logger.warning("job_repo.get failed job_id=%s", job_id, exc_info=True)

        with self._lock:
            mem = self._jobs.get(job_id)

        if not persisted and not mem:
            return None

        use_persisted = False
        if persisted and not mem:
            use_persisted = True
        elif persisted and mem:
            p_u = persisted.get("updated_at") or ""
            m_u = mem.updated_at or ""
            if p_u > m_u:
                use_persisted = True
            elif persisted.get("finished_at") and not mem.finished_at:
                use_persisted = True
            elif persisted.get("finished_at") and persisted.get("status") != mem.status.value:
                use_persisted = True

        if use_persisted and persisted:
            try:
                job = self._dict_to_job(persisted)
                with self._lock:
                    self._jobs[job_id] = job
                return job
            except Exception:  # noqa: BLE001
                logger.warning("failed to refresh job from persistence job_id=%s", job_id, exc_info=True)
                return mem

        return mem

    def retry_job(self, job_id: str, chunk_cfg: ChunkingConfig | None = None) -> str:
        with self._lock:
            old = self._jobs.get(job_id)
        if old is None:
            raise ValueError(f"job not found: {job_id}")
        return self.submit_job(documents=old.documents, operator=old.operator, chunk_cfg=chunk_cfg)

    def list_jobs(self, limit: int = 20, offset: int = 0) -> List[IngestionJob]:
        with self._lock:
            items = list(self._jobs.values())
        items.sort(key=lambda j: j.created_at, reverse=True)
        start = max(offset, 0)
        end = start + max(limit, 1)
        return items[start:end]

    def count_jobs(self) -> int:
        with self._lock:
            return len(self._jobs)

    def _guarded_run_job(self, job_id: str, chunk_cfg: ChunkingConfig | None) -> None:
        """线程入口：未捕获异常时落 FAILED，避免进程内永远 RUNNING 且无 ES 文档。"""
        try:
            self._run_job(job_id, chunk_cfg)
        except Exception as e:  # noqa: BLE001
            logger.exception("ingestion job thread crashed job_id=%s", job_id)
            with self._lock:
                job = self._jobs.get(job_id)
                if job is None:
                    return
                if job.status in (
                    IngestionJobStatus.SUCCESS,
                    IngestionJobStatus.FAILED,
                    IngestionJobStatus.PARTIAL,
                ):
                    return
                job.status = IngestionJobStatus.FAILED
                job.error_code = "E_JOB_UNHANDLED"
                job.error_message = (str(e) or type(e).__name__)[:2000]
                job.step = "error"
                job.finished_at = utcnow_iso()
                job.updated_at = utcnow_iso()
                try:
                    self._save_state()
                except Exception:  # noqa: BLE001
                    logger.exception("save_state after job crash failed job_id=%s", job_id)
                try:
                    self._save_job_record(job)
                except Exception:  # noqa: BLE001
                    logger.exception("save_job_record after job crash failed job_id=%s", job_id)

    def _run_job(self, job_id: str, chunk_cfg: ChunkingConfig | None) -> None:
        pipeline = DocumentPipeline(cfg=chunk_cfg or self._default_chunk_cfg)
        with self._lock:
            job = self._jobs[job_id]
            job.status = IngestionJobStatus.RUNNING
            job.step = "validate_input"
            job.updated_at = utcnow_iso()
            self._save_state()
            self._save_job_record(job)

        failed = 0
        for doc in job.documents:
            tmp_fetched: Path | None = None
            try:
                self._set_job_step(job, "validate_input")
                self._validate_document(doc)

                self._set_job_step(job, "content_fetch")
                doc, tmp_fetched = materialize_document_content_from_url(doc)
                self._validate_document(doc)

                if (doc.source_type or "").lower() == "pdf":
                    self._set_job_step(job, "mineru_route")
                doc, mineru_wall_s = prepare_pdf_document_for_pipeline(doc)
                if mineru_wall_s is not None:
                    self._set_job_step(job, "mineru_parse")
                    self._record_step_ms(job, doc.doc_name, "mineru_parse", int(mineru_wall_s * 1000))

                staged = pipeline.process_document_staged(doc)
                chunks = staged["chunks"]
                stats = staged["stats"]
                stage_durations = staged.get("stage_durations_ms") or {}
                if not chunks:
                    raise ValueError(f"E_CHUNK_EMPTY: no chunks generated for doc={doc.doc_name}")

                # 显式记录 parse/clean/chunk/enrich 四个步骤
                for step_name in ("parse", "clean", "chunk", "enrich"):
                    self._set_job_step(job, step_name)
                    self._record_step_ms(job, doc.doc_name, step_name, int(stage_durations.get(step_name, 0)))

                self._set_job_step(job, "index")
                t1 = time.perf_counter()
                try:
                    self._ingestion.ingest_texts(
                        dataset_id=doc.dataset_id,
                        texts=[c.text for c in chunks],
                        description=doc.description,
                        namespace=doc.namespace,
                        doc_name=doc.doc_name,
                        replace_if_exists=doc.replace_if_exists,
                        doc_version=doc.doc_version,
                        tenant_id=doc.tenant_id,
                        run_post_hook=False,
                    )
                except TypeError:
                    # 兼容旧版 ingestion service mock（无 doc_version/tenant_id 参数）
                    self._ingestion.ingest_texts(
                        dataset_id=doc.dataset_id,
                        texts=[c.text for c in chunks],
                        description=doc.description,
                        namespace=doc.namespace,
                        doc_name=doc.doc_name,
                        replace_if_exists=doc.replace_if_exists,
                    )
                try:
                    self._ingestion.post_index_hook(
                        dataset_id=doc.dataset_id,
                        texts=[c.text for c in chunks],
                        namespace=doc.namespace,
                        doc_name=doc.doc_name,
                        doc_version=doc.doc_version,
                        replace_if_exists=doc.replace_if_exists,
                    )
                except AttributeError:
                    # 兼容测试中的旧 fake ingestion service。
                    pass
                index_ms = int((time.perf_counter() - t1) * 1000)
                self._record_step_ms(job, doc.doc_name, "index", index_ms)

                self._set_job_step(job, "quality_check")
                qc_t0 = time.perf_counter()
                self._quality_check(chunks, stats, doc)
                qc_ms = int((time.perf_counter() - qc_t0) * 1000)
                self._record_step_ms(job, doc.doc_name, "quality_check", qc_ms)

                self._set_job_step(job, "finalize_alias_version")
                fin_t0 = time.perf_counter()
                try:
                    self._ingestion.finalize_alias_version(namespace=doc.namespace, doc_version=doc.doc_version)
                except AttributeError:
                    pass
                fin_ms = int((time.perf_counter() - fin_t0) * 1000)
                self._record_step_ms(job, doc.doc_name, "finalize_alias_version", fin_ms)

                with self._lock:
                    job.metrics["documents_success"] += 1
                    job.metrics["chunks_total"] += len(chunks)
                    job.metrics[f"doc_stats:{doc.doc_name}"] = stats
                    job.updated_at = utcnow_iso()
                    self._save_state()
                    self._save_job_record(job)
                self._save_doc_record(doc, job=job, chunk_count=len(chunks), status="SUCCESS")
            except Exception as e:  # noqa: BLE001
                failed += 1
                if isinstance(e, ContentFetchError):
                    logger.warning("ingestion content URL fetch failed doc=%s job=%s err=%s", doc.doc_name, job_id, e)
                elif isinstance(e, MinerUParseError):
                    logger.error(
                        "ingestion MinerU parse failed doc=%s job=%s status=%s output_dir=%s err=%s snippet=%s",
                        doc.doc_name,
                        job_id,
                        e.status_code,
                        e.output_dir_hint,
                        str(e),
                        (e.response_snippet or "")[:8000],
                        exc_info=True,
                    )
                else:
                    logger.exception("ingestion job failed for doc=%s job=%s", doc.doc_name, job_id)
                err = str(e)
                err_code = "E_INGEST_UNKNOWN"
                if isinstance(e, ContentFetchError):
                    err_code = "E_CONTENT_FETCH"
                elif err.startswith("E_CHUNK_EMPTY"):
                    err_code = "E_CHUNK_EMPTY"
                elif err.startswith("E_MINERU_REQUIRED") or "E_MINERU_REQUIRED" in err:
                    err_code = "E_MINERU_REQUIRED"
                elif isinstance(e, MinerUParseError):
                    err_code = "E_MINERU_PARSE"
                with self._lock:
                    job.metrics["documents_failed"] += 1
                    # 避免将 doc_name 拼进 metrics 的动态字段名（会导致 ES/EasySearch mapping 冲突，
                    # 例如此前某次写入把某个键推断成 object，后续再写 string 会触发 mapper_parsing_exception）。
                    doc_errors = job.metrics.get("doc_errors")
                    if not isinstance(doc_errors, list):
                        doc_errors = []
                    doc_errors.append({"doc_name": doc.doc_name, "error": err, "code": err_code})
                    job.metrics["doc_errors"] = doc_errors
                    job.updated_at = utcnow_iso()
                    self._save_state()
                    self._save_job_record(job)
                self._save_doc_record(doc, job=job, chunk_count=0, status="FAILED", error=err)
            finally:
                if tmp_fetched is not None:
                    tmp_fetched.unlink(missing_ok=True)

        with self._lock:
            job.step = "finalize"
            if failed == 0:
                job.status = IngestionJobStatus.SUCCESS
            elif failed == len(job.documents):
                job.status = IngestionJobStatus.FAILED
                job.error_code = "ALL_DOCS_FAILED"
                job.error_message = "All documents failed during ingestion"
            else:
                job.status = IngestionJobStatus.PARTIAL
                job.error_code = "PARTIAL_FAILED"
                job.error_message = "Some documents failed during ingestion"
            job.finished_at = utcnow_iso()
            job.updated_at = utcnow_iso()
            self._save_state()
            self._save_job_record(job)

    def _set_job_step(self, job: IngestionJob, step: str) -> None:
        with self._lock:
            job.step = step
            job.updated_at = utcnow_iso()
            self._save_state()
            self._save_job_record(job)

    def _record_step_ms(self, job: IngestionJob, doc_name: str, step: str, elapsed_ms: int) -> None:
        with self._lock:
            job.metrics["step_durations_ms"][f"{doc_name}:{step}"] = int(max(0, elapsed_ms))
            self._save_state()
            self._save_job_record(job)

    @staticmethod
    def _validate_document(doc: DocumentSource) -> None:
        if not doc.doc_name:
            raise ValueError("E_DOC_INVALID: empty doc_name")
        if not doc.dataset_id:
            raise ValueError("E_DOC_INVALID: empty dataset_id")
        if not (doc.content or "").strip():
            raise ValueError(f"E_DOC_EMPTY: empty content for doc={doc.doc_name}")

    def _quality_check(self, chunks: List, stats: dict, doc: DocumentSource) -> None:
        chunk_count = len(chunks)
        if chunk_count <= 0:
            raise ValueError(f"E_QUALITY_CHECK_FAILED: chunk_count=0 for doc={doc.doc_name}")
        avg_len = float(stats.get("avg_chunk_length") or 0.0)
        if avg_len <= 0:
            raise ValueError(f"E_QUALITY_CHECK_FAILED: avg_chunk_length<=0 for doc={doc.doc_name}")

    def _save_state(self) -> None:
        payload = {"jobs": [self._job_to_dict(j) for j in self._jobs.values()]}
        tmp = self._state_file.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        tmp.replace(self._state_file)

    def _save_job_record(self, job: IngestionJob) -> None:
        payload = self._job_to_dict(job)
        self._job_repo.upsert(job.job_id, payload)

    def _save_doc_record(
        self,
        doc: DocumentSource,
        job: IngestionJob,
        chunk_count: int,
        status: str,
        error: str | None = None,
    ) -> None:
        doc_key = make_document_storage_key(
            doc.doc_name,
            namespace=doc.namespace,
            tenant_id=doc.tenant_id,
            doc_version=doc.doc_version,
            tenant_id_fallback=self._tenant_id_default,
        )
        existing = self._doc_repo.get(doc_key) or {}
        created_at = existing.get("created_at") or utcnow_iso()
        payload = {
            "doc_name": doc.doc_name,
            "doc_version": doc.doc_version,
            "tenant_id": doc.tenant_id,
            "dataset_id": doc.dataset_id,
            "namespace": doc.namespace,
            "source_type": doc.source_type,
            "source_uri": doc.source_uri,
            "description": doc.description,
            "chunk_count": chunk_count,
            "pipeline_version": self._pipeline_version,
            "status": status,
            "created_at": created_at,
            "updated_at": utcnow_iso(),
            "last_job_id": job.job_id,
            "last_job_type": job.job_type.value,
            "last_job_status": job.status.value,
            "metadata": doc.metadata,
            "error": error,
        }
        self._doc_repo.upsert(doc_key, payload)

    def _load_state(self) -> None:
        if not self._state_file.exists():
            return
        try:
            payload = json.loads(self._state_file.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            logger.warning("failed to load ingestion job state file: %s", self._state_file)
            return
        for item in payload.get("jobs", []):
            try:
                job = self._dict_to_job(item)
                self._jobs[job.job_id] = job
            except Exception:  # noqa: BLE001
                logger.warning("skip invalid job record in state file")

    @staticmethod
    def _job_to_dict(job: IngestionJob) -> dict:
        return {
            "job_id": job.job_id,
            "job_type": job.job_type.value,
            "idempotency_key": job.idempotency_key,
            "status": job.status.value,
            "step": job.step,
            "created_at": job.created_at,
            "updated_at": job.updated_at,
            "finished_at": job.finished_at,
            "error_code": job.error_code,
            "error_message": job.error_message,
            "metrics": job.metrics,
            "operator": job.operator,
            "documents": [
                {
                    "dataset_id": d.dataset_id,
                    "doc_name": d.doc_name,
                    "doc_version": d.doc_version,
                    "tenant_id": d.tenant_id,
                    "namespace": d.namespace,
                    "content": d.content,
                    "source_type": d.source_type,
                    "source_uri": d.source_uri,
                    "description": d.description,
                    "replace_if_exists": d.replace_if_exists,
                    "metadata": d.metadata,
                }
                for d in job.documents
            ],
        }

    @staticmethod
    def _dict_to_job(item: dict) -> IngestionJob:
        docs = [
            DocumentSource(
                dataset_id=d["dataset_id"],
                doc_name=d["doc_name"],
                doc_version=d.get("doc_version", "v1"),
                tenant_id=d.get("tenant_id"),
                namespace=d.get("namespace"),
                content=d.get("content", ""),
                source_type=d.get("source_type", "text"),
                source_uri=d.get("source_uri"),
                description=d.get("description"),
                replace_if_exists=bool(d.get("replace_if_exists", True)),
                metadata=d.get("metadata") or {},
            )
            for d in item.get("documents", [])
        ]
        jt_raw = item.get("job_type") or IngestionJobType.UPSERT.value
        try:
            job_type = IngestionJobType(jt_raw)
        except ValueError:
            job_type = IngestionJobType.UPSERT
        return IngestionJob(
            job_id=item["job_id"],
            status=IngestionJobStatus(item["status"]),
            step=item.get("step", "created"),
            documents=docs,
            job_type=job_type,
            idempotency_key=item.get("idempotency_key"),
            created_at=item.get("created_at", utcnow_iso()),
            updated_at=item.get("updated_at", utcnow_iso()),
            finished_at=item.get("finished_at"),
            error_code=item.get("error_code"),
            error_message=item.get("error_message"),
            metrics=item.get("metrics") or {},
            operator=item.get("operator"),
        )

    def _build_idempotency_key(self, documents: List[DocumentSource]) -> str:
        parts: list[str] = []
        for d in documents:
            tenant = d.tenant_id or self._tenant_id_default
            ns = d.namespace or "__default__"
            ver = d.doc_version or "v1"
            parts.append(f"{tenant}|{ns}|{d.doc_name}|{ver}")
        parts.sort()
        return "||".join(parts)

