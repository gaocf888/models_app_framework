from __future__ import annotations

"""
大模型训练管理接口（运维内部使用）。

路由前缀由 main.py 挂载为 ``/train``，本文件内路径均为 ``/llm/...``，
合起来为 ``/train/llm/...``。
"""

from pathlib import Path
from typing import Any, Optional
from uuid import uuid4

from fastapi import APIRouter, HTTPException, Query

from app.core.logging import get_logger
from app.models.train_llm import (
    LLMDataConvertRequest,
    LLMDataMergeRequest,
    LLMDataValidateRequest,
    LLMTrainJobRequest,
    LLMTrainJobStatus,
)
from app.train.llm.data_pipeline import (
    LabelStudioConverter,
    convert_legacy_ops_json,
    merge_exports,
    validate_dataset,
    write_validation_report,
)
from app.train.llm.factory_adapter import LLaMAFactoryConfig
from app.train.llm.orchestrator import LLMTrainingOrchestrator
from app.train.llm.training_service import LLMTrainingConfig, load_training_config

router = APIRouter()
logger = get_logger(__name__)
orchestrator = LLMTrainingOrchestrator()


def _job_to_status(job) -> LLMTrainJobStatus:  # noqa: ANN001
    return LLMTrainJobStatus(
        job_id=job.job_id,
        mode=job.mode,
        status=job.status,
        created_at=job.created_at,
        started_at=job.started_at,
        finished_at=job.finished_at,
        output_dir=job.output_dir,
        log_path=job.log_path,
        metrics_path=job.metrics_path,
        error=job.error,
        progress=dict(job.progress or {}),
    )


def _build_code_cfg(req: LLMTrainJobRequest) -> LLMTrainingConfig:
    overrides: dict[str, Any] = {
        "base_model": req.base_model,
        "dataset_path": req.dataset_path,
        "output_dir": req.output_dir,
        "modality": req.modality,
        "task_profile": req.task_profile,
        "mode": "lora",
        "resume_from_checkpoint": req.resume_from_checkpoint,
        "extra_args": req.extra_args or {},
    }
    for key in (
        "num_epochs",
        "batch_size",
        "learning_rate",
        "max_length",
        "gradient_accumulation_steps",
        "lora_r",
        "lora_alpha",
        "lora_dropout",
        "target_modules",
    ):
        val = getattr(req, key, None)
        if val is not None:
            overrides[key] = val

    if req.config_yaml:
        return load_training_config(req.config_yaml, overrides=overrides)

    # defaults from package yaml when present
    default_yaml = Path("configs/llm_train/train_lora.yaml")
    if default_yaml.exists():
        try:
            return load_training_config(default_yaml, overrides=overrides)
        except Exception:  # noqa: BLE001
            logger.warning("failed to load default train_lora.yaml, using request fields only", exc_info=True)

    return LLMTrainingConfig(**{k: v for k, v in overrides.items() if v is not None})  # type: ignore[arg-type]


@router.post("/llm/start", response_model=LLMTrainJobStatus, summary="启动大模型训练任务")
async def start_llm_training(req: LLMTrainJobRequest) -> LLMTrainJobStatus:
    job_id = req.job_id or f"llm-{req.mode}-{uuid4().hex[:12]}"

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
        code_cfg = _build_code_cfg(req)
        # 未显式传 job_id 时，将输出落到独立子目录，避免多任务互相覆盖
        if not req.job_id:
            base_out = Path(code_cfg.output_dir)
            code_cfg.output_dir = str(base_out / job_id)

    try:
        job = orchestrator.start_llm_training(
            job_id=job_id,
            mode=req.mode,
            factory_cfg=factory_cfg,
            code_cfg=code_cfg,
        )
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return _job_to_status(job)


@router.get("/llm/status", summary="查询大模型训练任务状态")
async def get_llm_training_status(job_id: Optional[str] = None) -> dict:
    if job_id:
        job = orchestrator.get_job(job_id)
        if not job:
            return {"jobs": []}
        return {"jobs": [_job_to_status(job).model_dump()]}
    jobs = orchestrator.list_jobs()
    return {"jobs": [_job_to_status(j).model_dump() for j in jobs.values()]}


@router.post("/llm/jobs/{job_id}/stop", summary="请求停止训练任务")
async def stop_llm_training(job_id: str) -> dict:
    ok = orchestrator.request_stop(job_id)
    if not ok:
        raise HTTPException(status_code=404, detail=f"job not found or not stoppable: {job_id}")
    return {"ok": True, "job_id": job_id}


@router.get("/llm/jobs/{job_id}/metrics", summary="读取训练 metrics.jsonl")
async def get_job_metrics(job_id: str, limit: int = Query(500, ge=1, le=5000)) -> dict:
    return {"job_id": job_id, "metrics": orchestrator.read_metrics(job_id, limit=limit)}


@router.get("/llm/jobs/{job_id}/logs", summary="读取训练日志尾部")
async def get_job_logs(job_id: str, tail: int = Query(200, ge=1, le=5000)) -> dict:
    return {"job_id": job_id, "logs": orchestrator.read_logs(job_id, tail=tail)}


@router.get("/llm/artifacts", summary="列出 outputs/llm_train 产物")
async def list_artifacts(root: str = "outputs/llm_train") -> dict:
    return {"artifacts": orchestrator.list_artifacts(root=root)}


@router.post("/llm/data/convert", summary="LabelStudio/旧格式 → 统一训练数据")
async def convert_data(req: LLMDataConvertRequest) -> dict:
    try:
        if req.legacy:
            result = convert_legacy_ops_json(
                req.export_path,
                out_path=req.out_path,
                task_profile=req.task_profile,
            )
        else:
            conv = LabelStudioConverter(task_profile=req.task_profile)
            result = conv.convert_export(
                req.export_path,
                image_root=req.image_root,
                out_path=req.out_path,
                as_jsonl=True,
            )
        return result
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/llm/data/validate", summary="校验统一训练数据集")
async def validate_data(req: LLMDataValidateRequest) -> dict:
    try:
        report = validate_dataset(req.dataset_path, check_images=req.check_images)
        if req.write_report:
            report["report_path"] = write_validation_report(report)
        return report
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/llm/data/merge", summary="合并多个统一数据集")
async def merge_data(req: LLMDataMergeRequest) -> dict:
    try:
        return merge_exports(req.paths, req.out_path)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=400, detail=str(exc)) from exc
