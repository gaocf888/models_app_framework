"""
大模型 LoRA 微调 CLI 入口（对齐方案中的 run_llm_training.py）。

用法：
  PYTHONPATH=. python run_llm_training.py --config configs/llm_train/train_lora.yaml
  PYTHONPATH=. python run_llm_training.py --config configs/llm_train/train_lora.yaml --validate-only
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser(description="LLM / VLM LoRA fine-tuning")
    parser.add_argument("--config", type=str, default="configs/llm_train/train_lora.yaml")
    parser.add_argument("--validate-only", action="store_true", help="只校验配置与数据，不训练")
    parser.add_argument("--base-model", type=str, default=None)
    parser.add_argument("--dataset-path", type=str, default=None)
    parser.add_argument("--output-dir", type=str, default=None)
    parser.add_argument("--modality", type=str, default=None, choices=["text", "vision", "auto"])
    args = parser.parse_args()

    # Ensure project root on path when executed as script
    root = Path(__file__).resolve().parent
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))

    from app.core.logging import get_logger
    from app.train.llm.data_pipeline import validate_dataset
    from app.train.llm.device import setup_training_env
    from app.train.llm.training_service import LLMTrainingService, load_training_config

    logger = get_logger("run_llm_training")
    cfg_path = Path(args.config)
    if not cfg_path.exists():
        logger.error("config not found: %s", cfg_path)
        return 1

    overrides = {}
    if args.base_model:
        overrides["base_model"] = args.base_model
    if args.dataset_path:
        overrides["dataset_path"] = args.dataset_path
    if args.output_dir:
        overrides["output_dir"] = args.output_dir
    if args.modality:
        overrides["modality"] = args.modality

    cfg = load_training_config(cfg_path, overrides=overrides or None)
    if not Path(cfg.base_model).exists() and "/" not in cfg.base_model and "\\" not in cfg.base_model:
        # allow HF hub ids; only warn for missing local paths that look like paths
        pass
    elif not Path(cfg.base_model).exists() and (cfg.base_model.startswith("models/") or Path(cfg.base_model).is_absolute()):
        logger.warning("base_model path does not exist yet: %s", cfg.base_model)

    if not Path(cfg.dataset_path).exists():
        logger.error("dataset_path not found: %s", cfg.dataset_path)
        return 1

    report = validate_dataset(cfg.dataset_path, check_images=True)
    logger.info(
        "dataset validate: total=%s valid=%s invalid=%s",
        report["total_samples"],
        report["valid_samples"],
        report["invalid_samples"],
    )
    if report["invalid_samples"] > 0:
        logger.error("dataset has invalid samples: %s", report["errors"][:5])
        return 1

    setup_training_env()
    if args.validate_only:
        logger.info("validate-only OK")
        return 0

    Path(cfg.output_dir).mkdir(parents=True, exist_ok=True)
    cfg.metrics_path = str(Path(cfg.output_dir) / "metrics.jsonl")
    cfg.log_path = str(Path(cfg.output_dir) / "train.log")
    cfg.stop_flag_path = str(Path(cfg.output_dir) / "STOP")

    logger.info("starting LLMTrainer ...")
    LLMTrainingService().start_training(cfg)
    logger.info("training finished, output=%s", cfg.output_dir)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
