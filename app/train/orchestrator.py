from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import Dict, Literal, Optional

from app.core.logging import get_logger
from app.train.llm_factory_adapter import LLaMAFactoryAdapter, LLaMAFactoryConfig
from app.train.llm_training import LLMTrainingConfig, LLMTrainingService

logger = get_logger(__name__)

TrainingMode = Literal["factory", "code"]
TrainingStatus = Literal["pending", "running", "succeeded", "failed"]


@dataclass
class LLMTrainingJob:
    """
    大模型训练任务元数据。
    """

    job_id: str
    mode: TrainingMode
    config_factory: Optional[LLaMAFactoryConfig] = None
    config_code: Optional[LLMTrainingConfig] = None
    status: TrainingStatus = "pending"
    created_at: float = field(default_factory=time.time)
    started_at: Optional[float] = None
    finished_at: Optional[float] = None
    output_dir: Optional[str] = None
    error: Optional[str] = None


class TrainingOrchestrator:
    """
    大模型训练/微调统一调度器（企业级骨架）。

    能力：
    - 统一接入 LLaMA-Factory（factory 模式）与 LLMTrainingService（code 模式）；
    - 管理训练任务 ID、状态、起止时间与输出目录等元数据；
    - 提供基础的任务查询能力，为 `train_admin` API 提供服务。
    """

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
        mode: TrainingMode,
        factory_cfg: Optional[LLaMAFactoryConfig] = None,
        code_cfg: Optional[LLMTrainingConfig] = None,
    ) -> LLMTrainingJob:
        """
        启动一个新的大模型训练任务。

        - mode="factory"：走 LLaMA-Factory 通道；
        - mode="code"：走内部代码训练通道。
        """
        with self._lock:
            if job_id in self._threads:
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

            job = LLMTrainingJob(
                job_id=job_id,
                mode=mode,
                config_factory=factory_cfg,
                config_code=code_cfg,
                output_dir=output_dir,
            )
            self._jobs[job_id] = job

            th = threading.Thread(target=self._run_job, args=(job_id,), daemon=True)
            self._threads[job_id] = th
            th.start()
            return job

    def get_job(self, job_id: str) -> Optional[LLMTrainingJob]:
        with self._lock:
            return self._jobs.get(job_id)

    def list_jobs(self) -> Dict[str, LLMTrainingJob]:
        with self._lock:
            return dict(self._jobs)

    def _run_job(self, job_id: str) -> None:
        job = self._jobs.get(job_id)
        if not job:
            return

        job.status = "running"
        job.started_at = time.time()

        logger.info(
            "start LLM training job=%s mode=%s base_model=%s dataset=%s output_dir=%s",
            job.job_id,
            job.mode,
            (job.config_factory.base_model if job.config_factory else job.config_code.base_model if job.config_code else None),
            (job.config_factory.dataset_path if job.config_factory else job.config_code.dataset_path if job.config_code else None),
            job.output_dir,
        )

        try:
            if job.mode == "factory" and job.config_factory:
                self._factory.start_training(job.config_factory)
            elif job.mode == "code" and job.config_code:
                self._code.start_training(job.config_code)
            else:
                raise ValueError(f"invalid job configuration for job_id={job_id}")

            job.status = "succeeded"
        except Exception as exc:  # noqa: BLE001
            logger.exception("LLM training job failed: job_id=%s error=%s", job_id, exc)
            job.status = "failed"
            job.error = str(exc)
        finally:
            job.finished_at = time.time()
            logger.info("finish LLM training job=%s status=%s", job_id, job.status)

