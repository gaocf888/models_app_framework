from __future__ import annotations

"""LLM / VLM 训练服务入口：配置加载 + 调用 LLMTrainer。"""

from dataclasses import asdict, dataclass, field, fields
from pathlib import Path
from typing import Any, Callable, Dict, List, Literal, Optional

import yaml

from app.core.logging import get_logger
from app.train.llm.device import setup_training_env

logger = get_logger(__name__)

TrainingMode = Literal["lora", "full"]
Modality = Literal["text", "vision", "auto"]


@dataclass
class LLMTrainingConfig:
    base_model: str
    dataset_path: str
    output_dir: str
    modality: Modality = "auto"
    task_profile: str = "chat_sft"
    mode: TrainingMode = "lora"
    resume_from_checkpoint: Optional[str] = None
    num_epochs: int = 3
    batch_size: int = 1
    learning_rate: float = 1e-5
    max_length: int = 2048
    image_size: int = 224
    gradient_accumulation_steps: int = 4
    warmup_steps: int = 50
    weight_decay: float = 0.01
    max_grad_norm: float = 1.0
    lora_r: int = 8
    lora_alpha: int = 32
    lora_dropout: float = 0.1
    target_modules: str | List[str] = "auto"
    save_steps: int = 200
    logging_steps: int = 10
    save_total_limit: int = 3
    fp16: bool = True
    bf16: bool = False
    metrics_path: Optional[str] = None
    log_path: Optional[str] = None
    stop_flag_path: Optional[str] = None
    extra_args: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    def snapshot(self, path: str | Path) -> str:
        out = Path(path)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(yaml.safe_dump(self.to_dict(), allow_unicode=True, sort_keys=False), encoding="utf-8")
        return str(out.resolve())


def _coerce_cfg(data: Dict[str, Any]) -> Dict[str, Any]:
    out = dict(data)
    lora = out.pop("lora", None)
    if isinstance(lora, dict):
        if "r" in lora:
            out.setdefault("lora_r", lora["r"])
        if "alpha" in lora:
            out.setdefault("lora_alpha", lora["alpha"])
        if "dropout" in lora:
            out.setdefault("lora_dropout", lora["dropout"])
        if "target_modules" in lora:
            out.setdefault("target_modules", lora["target_modules"])
    # aliases from older / factory style
    if "model_path" in out and "base_model" not in out:
        out["base_model"] = out.pop("model_path")
    if "data_path" in out and "dataset_path" not in out:
        out["dataset_path"] = out.pop("data_path")
    if "lora_rank" in out and "lora_r" not in out:
        out["lora_r"] = out.pop("lora_rank")
    allowed = {f.name for f in fields(LLMTrainingConfig)}
    return {k: v for k, v in out.items() if k in allowed}


def load_training_config(path: str | Path, overrides: Optional[Dict[str, Any]] = None) -> LLMTrainingConfig:
    raw = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    if not isinstance(raw, dict):
        raise ValueError(f"invalid yaml config: {path}")
    merged = _coerce_cfg(raw)
    if overrides:
        merged.update(_coerce_cfg(overrides))
    required = ("base_model", "dataset_path", "output_dir")
    for key in required:
        if not merged.get(key):
            raise ValueError(f"config missing required field: {key}")
    return LLMTrainingConfig(**merged)  # type: ignore[arg-type]


ProgressCallback = Callable[[Dict[str, Any]], None]


class LLMTrainingService:
    """代码方式大模型训练服务（委托 LLMTrainer）。"""

    def start_training(
        self,
        cfg: LLMTrainingConfig,
        *,
        progress_cb: Optional[ProgressCallback] = None,
    ) -> None:
        logger.info(
            "start LLM training: base_model=%s dataset=%s output=%s modality=%s mode=%s resume=%s",
            cfg.base_model,
            cfg.dataset_path,
            cfg.output_dir,
            cfg.modality,
            cfg.mode,
            cfg.resume_from_checkpoint,
        )
        if cfg.mode != "lora":
            raise NotImplementedError("Only LoRA fine-tuning is supported in this release (mode=lora).")

        setup_training_env()
        Path(cfg.output_dir).mkdir(parents=True, exist_ok=True)
        cfg.snapshot(Path(cfg.output_dir) / "config.snapshot.yaml")

        file_handler = None
        if cfg.log_path:
            import logging

            Path(cfg.log_path).parent.mkdir(parents=True, exist_ok=True)
            file_handler = logging.FileHandler(cfg.log_path, encoding="utf-8")
            file_handler.setFormatter(
                logging.Formatter("%(asctime)s [%(levelname)s] %(name)s - %(message)s")
            )
            logging.getLogger().addHandler(file_handler)
            logging.getLogger("app.train.llm").addHandler(file_handler)

        try:
            from app.train.llm.trainer import LLMTrainer

            trainer = LLMTrainer(cfg, progress_cb=progress_cb)
            trainer.train()
        finally:
            if file_handler is not None:
                import logging

                root = logging.getLogger()
                train_log = logging.getLogger("app.train.llm")
                root.removeHandler(file_handler)
                train_log.removeHandler(file_handler)
                file_handler.close()
