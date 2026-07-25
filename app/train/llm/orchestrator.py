from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Literal, Optional

from app.core.logging import get_logger
from app.train.llm.factory_adapter import LLaMAFactoryAdapter, LLaMAFactoryConfig
from app.train.llm.training_service import LLMTrainingConfig, LLMTrainingService

logger = get_logger(__name__)

TrainingChannel = Literal["factory", "code"]
TrainingStatus = Literal["pending", "running", "succeeded", "failed", "stopped"]


@dataclass
class LLMTrainingJob:
    job_id: str
    mode: TrainingChannel
    config_factory: Optional[LLaMAFactoryConfig] = None
    config_code: Optional[LLMTrainingConfig] = None
    status: TrainingStatus = "pending"
    created_at: float = field(default_factory=time.time)
    started_at: Optional[float] = None
    finished_at: Optional[float] = None
    output_dir: Optional[str] = None
    log_path: Optional[str] = None
    metrics_path: Optional[str] = None
    stop_flag_path: Optional[str] = None
    error: Optional[str] = None
    progress: Dict[str, Any] = field(default_factory=dict)


class LLMTrainingOrchestrator:
    """大模型训练任务调度器（进程内）。"""

    def __init__(
        self,
        factory_adapter: Optional[LLaMAFactoryAdapter] = None,
        code_service: Optional[LLMTrainingService] = None,
    ) -> None:
        self._factory = factory_adapter or LLaMAFactoryAdapter()
        self._code = code_service or LLMTrainingService()
        self._jobs: Dict[str, LLMTrainingJob] = {}
        self._threads: Dict[str, threading.Thread] = {}
        self._lock = threading.Lock()

    def start_llm_training(
        self,
        job_id: str,
        mode: TrainingChannel,
        factory_cfg: Optional[LLaMAFactoryConfig] = None,
        code_cfg: Optional[LLMTrainingConfig] = None,
    ) -> LLMTrainingJob:
        with self._lock:
            if job_id in self._jobs and self._jobs[job_id].status in ("pending", "running"):
                return self._jobs[job_id]

            if mode == "factory" and not factory_cfg:
                raise ValueError("factory mode requires factory_cfg")
            if mode == "code" and not code_cfg:
                raise ValueError("code mode requires code_cfg")

            output_dir = None
            if mode == "factory" and factory_cfg:
                output_dir = factory_cfg.output_dir
            if mode == "code" and code_cfg:
                output_dir = code_cfg.output_dir
                out = Path(output_dir)
                out.mkdir(parents=True, exist_ok=True)
                code_cfg.metrics_path = code_cfg.metrics_path or str(out / "metrics.jsonl")
                code_cfg.log_path = code_cfg.log_path or str(out / "train.log")
                code_cfg.stop_flag_path = code_cfg.stop_flag_path or str(out / "STOP")
                # clear stale stop flag
                stop_p = Path(code_cfg.stop_flag_path)
                if stop_p.exists():
                    stop_p.unlink()

            job = LLMTrainingJob(
                job_id=job_id,
                mode=mode,
                config_factory=factory_cfg,
                config_code=code_cfg,
                output_dir=output_dir,
                log_path=getattr(code_cfg, "log_path", None) if code_cfg else None,
                metrics_path=getattr(code_cfg, "metrics_path", None) if code_cfg else None,
                stop_flag_path=getattr(code_cfg, "stop_flag_path", None) if code_cfg else None,
            )
            self._jobs[job_id] = job
            th = threading.Thread(target=self._run_job, args=(job_id,), daemon=True, name=f"llm-train-{job_id}")
            self._threads[job_id] = th
            th.start()
            return job

    def get_job(self, job_id: str) -> Optional[LLMTrainingJob]:
        with self._lock:
            return self._jobs.get(job_id)

    def list_jobs(self) -> Dict[str, LLMTrainingJob]:
        with self._lock:
            return dict(self._jobs)

    def request_stop(self, job_id: str) -> bool:
        job = self.get_job(job_id)
        if not job:
            return False
        if not job.stop_flag_path:
            # factory 或异常任务无 stop 文件时，仍创建约定路径便于后续扩展
            if job.output_dir:
                job.stop_flag_path = str(Path(job.output_dir) / "STOP")
            else:
                return False
        Path(job.stop_flag_path).parent.mkdir(parents=True, exist_ok=True)
        Path(job.stop_flag_path).write_text("1", encoding="utf-8")
        logger.info("stop requested for job=%s flag=%s", job_id, job.stop_flag_path)
        return True

    def _run_job(self, job_id: str) -> None:
        job = self._jobs.get(job_id)
        if not job:
            return
        job.status = "running"
        job.started_at = time.time()

        def _progress(row: Dict[str, Any]) -> None:
            job.progress = dict(row)

        log_fh = None
        try:
            if job.log_path:
                Path(job.log_path).parent.mkdir(parents=True, exist_ok=True)
                log_fh = open(job.log_path, "a", encoding="utf-8")  # noqa: SIM115
                log_fh.write(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] job={job_id} start mode={job.mode}\n")
                log_fh.flush()

            if job.mode == "factory" and job.config_factory:
                self._factory.start_training(job.config_factory)
            elif job.mode == "code" and job.config_code:
                self._code.start_training(job.config_code, progress_cb=_progress)
            else:
                raise ValueError(f"invalid job configuration for job_id={job_id}")

            if job.stop_flag_path and Path(job.stop_flag_path).exists():
                job.status = "stopped"
            else:
                job.status = "succeeded"
        except Exception as exc:  # noqa: BLE001
            logger.exception("LLM training job failed: job_id=%s error=%s", job_id, exc)
            job.status = "failed"
            job.error = str(exc)
            if log_fh:
                log_fh.write(f"ERROR: {exc}\n")
        finally:
            job.finished_at = time.time()
            if log_fh:
                log_fh.write(
                    f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] job={job_id} finish status={job.status}\n"
                )
                log_fh.close()
            logger.info("finish LLM training job=%s status=%s", job_id, job.status)

    def read_metrics(self, job_id: str, *, limit: int = 500) -> List[Dict[str, Any]]:
        import json

        job = self.get_job(job_id)
        if not job or not job.metrics_path or not Path(job.metrics_path).exists():
            return []
        rows: List[Dict[str, Any]] = []
        with open(job.metrics_path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rows.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
        return rows[-limit:]

    def read_logs(self, job_id: str, *, tail: int = 200) -> str:
        job = self.get_job(job_id)
        if not job or not job.log_path or not Path(job.log_path).exists():
            return ""
        lines = Path(job.log_path).read_text(encoding="utf-8", errors="replace").splitlines()
        return "\n".join(lines[-tail:])

    def list_artifacts(self, root: str = "outputs/llm_train") -> List[Dict[str, Any]]:
        base = Path(root)
        if not base.exists():
            return []
        items: List[Dict[str, Any]] = []
        for p in sorted(base.glob("*")):
            if not p.is_dir():
                continue
            adapter = p / "adapter"
            items.append(
                {
                    "job_dir": str(p),
                    "name": p.name,
                    "has_adapter": adapter.exists(),
                    "adapter_dir": str(adapter) if adapter.exists() else None,
                    "metrics_path": str(p / "metrics.jsonl") if (p / "metrics.jsonl").exists() else None,
                    "config_snapshot": str(p / "config.snapshot.yaml")
                    if (p / "config.snapshot.yaml").exists()
                    else None,
                }
            )
        return items


# backward-compatible alias
TrainingOrchestrator = LLMTrainingOrchestrator
