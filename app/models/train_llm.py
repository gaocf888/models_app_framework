from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field

TrainingChannel = Literal["factory", "code"]
Modality = Literal["text", "vision", "auto"]


class LLMTrainJobRequest(BaseModel):
    job_id: Optional[str] = Field(None, description="任务 ID；为空则后端生成")
    mode: TrainingChannel = Field("code", description="factory 或 code（推荐 code）")
    base_model: str = Field(..., description="基座模型路径")
    dataset_path: str = Field(..., description="统一格式训练数据路径 json/jsonl")
    output_dir: str = Field(..., description="输出目录，建议 outputs/llm_train/<job_id>")
    modality: Modality = Field("auto", description="text / vision / auto")
    task_profile: str = Field("chat_sft", description="任务模板：vl_ops / defect_vl / chat_sft")
    resume_from_checkpoint: Optional[str] = Field(None, description="断点续训路径（仅 code）")
    config_yaml: Optional[str] = Field(None, description="可选：从 YAML 加载默认超参后再用请求字段覆盖")
    num_epochs: Optional[int] = None
    batch_size: Optional[int] = None
    learning_rate: Optional[float] = None
    max_length: Optional[int] = None
    gradient_accumulation_steps: Optional[int] = None
    lora_r: Optional[int] = None
    lora_alpha: Optional[int] = None
    lora_dropout: Optional[float] = None
    target_modules: Optional[str] = Field(None, description="auto 或逗号分隔模块名")
    extra_args: Dict[str, Any] = Field(default_factory=dict)


class LLMTrainJobStatus(BaseModel):
    job_id: str
    mode: TrainingChannel
    status: str
    created_at: float
    started_at: Optional[float] = None
    finished_at: Optional[float] = None
    output_dir: Optional[str] = None
    log_path: Optional[str] = None
    metrics_path: Optional[str] = None
    error: Optional[str] = None
    progress: Dict[str, Any] = Field(default_factory=dict)


class LLMDataConvertRequest(BaseModel):
    export_path: str = Field(..., description="LabelStudio 导出 JSON 路径")
    task_profile: str = Field("vl_ops", description="vl_ops / defect_vl / chat_sft")
    image_root: Optional[str] = Field(None, description="本地图片根目录")
    out_path: Optional[str] = None
    legacy: bool = Field(False, description="True 时按参考项目旧 JSON（image_path+structured）转换")


class LLMDataValidateRequest(BaseModel):
    dataset_path: str
    check_images: bool = True
    write_report: bool = True


class LLMDataMergeRequest(BaseModel):
    paths: List[str]
    out_path: str
