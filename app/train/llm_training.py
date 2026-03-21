from __future__ import annotations

"""
大模型训练/微调脚本封装（LoRA 示例版）。

设计目标：
- 为大语言模型和多模态模型提供统一的代码训练/微调入口；
- 支持 LoRA、全参微调、断点续训等模式的配置化；
- 与 LLaMA-Factory 适配器协同使用，形成“可视化 + 代码”双通道。

当前实现：
- 在骨架基础上，增加一个基于 HuggingFace Transformers + PEFT 的 LoRA 训练示例；
- 该示例不会自动运行，只在显式调用时触发；
- 依赖包（transformers、datasets、peft、accelerate 等）需在实际环境中通过 pip 安装。
"""

from dataclasses import dataclass
from typing import Dict, Literal, Optional

from app.core.logging import get_logger

logger = get_logger(__name__)


TrainingMode = Literal["lora", "full"]


@dataclass
class LLMTrainingConfig:
    base_model: str
    dataset_path: str
    output_dir: str
    mode: TrainingMode = "lora"
    resume_from_checkpoint: Optional[str] = None
    extra_args: Dict[str, str] | None = None


class LLMTrainingService:
    """
    代码方式的大模型训练服务封装。
    """

    def start_training(self, cfg: LLMTrainingConfig) -> None:
        """
        启动训练任务（占位实现）。

        说明：
        - 在此处根据 cfg.mode 选择 LoRA/全参微调路径；
        - 通过 extra_args 传递 batch_size、lr、max_steps 等细节参数；
        - 默认走 LoRA 示例训练路径，便于快速试验。
        """
        logger.info(
            "start LLM training: base_model=%s, dataset=%s, output=%s, mode=%s, resume=%s",
            cfg.base_model,
            cfg.dataset_path,
            cfg.output_dir,
            cfg.mode,
            cfg.resume_from_checkpoint,
        )

        if cfg.mode == "lora":
            self._run_lora_training(cfg)
        else:
            logger.warning("full-parameter training not implemented yet, only logging config for now.")

    def _run_lora_training(self, cfg: LLMTrainingConfig) -> None:
        """
        基于 HuggingFace Transformers + PEFT 的 LoRA 训练示例。

        说明：
        - 该函数展示了一个典型 LoRA 训练流程，实际项目中可根据需要修改；
        - 需要在运行环境中安装以下依赖：
          - transformers
          - datasets
          - peft
          - accelerate
        """
        try:
            from datasets import load_dataset  # type: ignore[import-not-found]
            from peft import LoraConfig, get_peft_model  # type: ignore[import-not-found]
            from torch.utils.data import DataLoader  # type: ignore[import-not-found]
            from transformers import (  # type: ignore[import-not-found]
                AutoModelForCausalLM,
                AutoTokenizer,
                get_linear_schedule_with_warmup,
            )
            import torch  # type: ignore[import-not-found]
        except Exception as exc:  # noqa: BLE001
            logger.error("LoRA training dependencies not installed, aborting training. error=%s", exc)
            return

        batch_size = int((cfg.extra_args or {}).get("batch_size", 2))
        lr = float((cfg.extra_args or {}).get("learning_rate", 1e-4))
        num_epochs = int((cfg.extra_args or {}).get("num_epochs", 1))
        max_length = int((cfg.extra_args or {}).get("max_length", 512))

        logger.info(
            "running LoRA training example: batch_size=%s, lr=%s, num_epochs=%s, max_length=%s",
            batch_size,
            lr,
            num_epochs,
            max_length,
        )

        tokenizer = AutoTokenizer.from_pretrained(cfg.base_model)
        if tokenizer.pad_token is None:
            tokenizer.pad_token = tokenizer.eos_token

        model = AutoModelForCausalLM.from_pretrained(cfg.base_model)
        model = model.to("cuda" if torch.cuda.is_available() else "cpu")

        lora_cfg = LoraConfig(
            r=8,
            lora_alpha=16,
            target_modules=["q_proj", "v_proj"],  # 示例，需根据具体模型调整
            lora_dropout=0.1,
            bias="none",
            task_type="CAUSAL_LM",
        )
        model = get_peft_model(model, lora_cfg)

        dataset = load_dataset("json", data_files=cfg.dataset_path)["dajia"]

        def collate_fn(batch):
            texts = [sample.get("text", "") for sample in batch]
            enc = tokenizer(
                texts,
                padding=True,
                truncation=True,
                max_length=max_length,
                return_tensors="pt",
            )
            enc["labels"] = enc["input_ids"].clone()
            return enc

        dataloader = DataLoader(dataset, batch_size=batch_size, shuffle=True, collate_fn=collate_fn)

        optimizer = torch.optim.AdamW(model.parameters(), lr=lr)
        total_steps = num_epochs * len(dataloader)
        scheduler = get_linear_schedule_with_warmup(
            optimizer,
            num_warmup_steps=int(0.1 * total_steps),
            num_training_steps=total_steps,
        )

        model.train()
        global_step = 0
        device = next(model.parameters()).device

        for epoch in range(num_epochs):
            for batch in dataloader:
                batch = {k: v.to(device) for k, v in batch.items()}
                outputs = model(**batch)
                loss = outputs.loss
                loss.backward()
                optimizer.step()
                scheduler.step()
                optimizer.zero_grad()

                global_step += 1
                if global_step % 10 == 0:
                    logger.info("LoRA training step=%s, epoch=%s, loss=%.4f", global_step, epoch, loss.item())

        # 保存 LoRA 权重到 output_dir
        import os  # type: ignore[import-not-found]

        os.makedirs(cfg.output_dir, exist_ok=True)
        model.save_pretrained(cfg.output_dir)
        logger.info("LoRA training finished, adapter weights saved to %s", cfg.output_dir)

