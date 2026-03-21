from __future__ import annotations

from typing import Any, Dict, Literal, Optional

from pydantic import BaseModel, Field


TrainingMode = Literal["factory", "code"]


class LLMTrainJobRequest(BaseModel):
    """
    启动大模型训练任务的请求体。
    """

    job_id: Optional[str] = Field(None, description="任务 ID，可选；为空则由后端生成简易 ID")
    mode: TrainingMode = Field(..., description="训练模式：factory 或 code")

    # 公共字段
    base_model: str = Field(..., description="基础模型名称或路径")
    dataset_path: str = Field(..., description="训练数据集路径")
    output_dir: str = Field(..., description="训练输出目录")

    # 可选额外参数（透传给具体训练实现）
    extra_args: Dict[str, Any] = Field(default_factory=dict, description="训练额外配置（batch_size/lr 等）")
    resume_from_checkpoint: Optional[str] = Field(
        None, description="断点续训的 checkpoint 路径，仅 code 模式有效",
    )


class LLMTrainJobStatus(BaseModel):
    """
    训练任务状态对外视图。
    """

    job_id: str
    mode: TrainingMode
    status: str
    created_at: float
    started_at: Optional[float]
    finished_at: Optional[float]
    output_dir: Optional[str]
    error: Optional[str]

