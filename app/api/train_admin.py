from __future__ import annotations

"""
训练管理接口（内部使用）。

服务配置前置条件（运维/开发）：
1) 训练运行环境可用：
   - GPU/CUDA（如启用）与依赖环境（PyTorch、训练框架）需可用。
2) 训练数据与输出目录可访问：
   - 请求中的 dataset_path / output_dir 需为服务进程可读写路径。
3) factory 模式：
   - 若 mode=factory，需确保 LLaMA-Factory 及其运行依赖已正确部署。
4) code 模式：
   - 若 mode=code，需确保内部训练代码依赖可用，且断点路径（如传入）存在。
"""

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

    参数说明（见 LLMTrainJobRequest）：
    - 必传：mode、base_model、dataset_path、output_dir
    - 可选：job_id、extra_args、resume_from_checkpoint
    - 默认行为：job_id 不传时由后端生成；resume_from_checkpoint 仅 code 模式生效
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

    参数说明：
    - 可选：job_id（不传则返回全部任务；传入则返回单任务）
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

