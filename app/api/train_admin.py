from __future__ import annotations

from fastapi import APIRouter

from app.core.logging import get_logger
from app.models.train import LLMTrainJobRequest, LLMTrainJobStatus
from app.train.llm_factory_adapter import LLaMAFactoryConfig
from app.train.llm_training import LLMTrainingConfig
from app.train.orchestrator import TrainingOrchestrator

router = APIRouter()
logger = get_logger(__name__)
orchestrator = TrainingOrchestrator()


@router.post("/llm/start", response_model=LLMTrainJobStatus, summary="启动大模型训练任务（内部使用）")
async def start_llm_training(req: LLMTrainJobRequest) -> LLMTrainJobStatus:
    """
    启动大模型训练/微调任务。

    说明：
    - mode="factory"：通过 LLaMA-Factory 适配器提交任务；
    - mode="code"：通过内部 LLMTrainingService 启动代码训练任务；
    - 该接口仅面向内部/运维使用，不建议对业务侧直接开放。
    """
    job_id = req.job_id or f"llm-{req.mode}-{id(req)}"

    factory_cfg = None
    code_cfg = None
    if req.mode == "factory":
        factory_cfg = LLaMAFactoryConfig(
            base_model=req.base_model,
            dataset_path=req.dataset_path,
            output_dir=req.output_dir,
            extra_args={k: str(v) for k, v in (req.extra_args or {}).items()},
        )
    else:
        code_cfg = LLMTrainingConfig(
            base_model=req.base_model,
            dataset_path=req.dataset_path,
            output_dir=req.output_dir,
            mode="lora",
            resume_from_checkpoint=req.resume_from_checkpoint,
            extra_args={k: str(v) for k, v in (req.extra_args or {}).items()},
        )

    job = orchestrator.start_llm_training(
        job_id=job_id,
        mode=req.mode,
        factory_cfg=factory_cfg,
        code_cfg=code_cfg,
    )

    return LLMTrainJobStatus(
        job_id=job.job_id,
        mode=job.mode,
        status=job.status,
        created_at=job.created_at,
        started_at=job.started_at,
        finished_at=job.finished_at,
        output_dir=job.output_dir,
        error=job.error,
    )


@router.get("/llm/status", summary="查询大模型训练任务状态（内部使用）")
async def get_llm_training_status(job_id: str | None = None) -> dict:
    """
    查询大模型训练任务状态。

    - 若提供 job_id，则返回该任务的详细状态；
    - 若未提供，则返回所有任务的简要列表。
    """
    if job_id:
        job = orchestrator.get_job(job_id)
        if not job:
            return {"jobs": []}
        return {
            "jobs": [
                {
                    "job_id": job.job_id,
                    "mode": job.mode,
                    "status": job.status,
                    "created_at": job.created_at,
                    "started_at": job.started_at,
                    "finished_at": job.finished_at,
                    "output_dir": job.output_dir,
                    "error": job.error,
                }
            ]
        }

    jobs = orchestrator.list_jobs()
    return {
        "jobs": [
            {
                "job_id": j.job_id,
                "mode": j.mode,
                "status": j.status,
                "created_at": j.created_at,
                "started_at": j.started_at,
                "finished_at": j.finished_at,
                "output_dir": j.output_dir,
                "error": j.error,
            }
            for j in jobs.values()
        ]
    }

