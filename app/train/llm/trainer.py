from __future__ import annotations

"""
LLMTrainer：基于 Transformers + PEFT LoRA 的统一训练器（text LLM / vision VLM）。
"""

import json
import os
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

from app.core.logging import get_logger
from app.train.llm.data_pipeline import load_samples
from app.train.llm.device import resolve_torch_device
from app.train.llm.training_service import LLMTrainingConfig, ProgressCallback

logger = get_logger(__name__)

DEFAULT_TEXT_TARGETS = ["q_proj", "k_proj", "v_proj", "o_proj", "gate_proj", "up_proj", "down_proj"]
DEFAULT_VLM_TARGETS = ["q_proj", "k_proj", "v_proj", "o_proj"]


def resolve_target_modules(cfg: LLMTrainingConfig, modality: str) -> List[str]:
    tm = cfg.target_modules
    if isinstance(tm, list):
        return tm
    if isinstance(tm, str) and tm.strip() and tm.strip().lower() != "auto":
        return [x.strip() for x in tm.split(",") if x.strip()]
    return list(DEFAULT_VLM_TARGETS if modality == "vision" else DEFAULT_TEXT_TARGETS)


def infer_modality(cfg: LLMTrainingConfig, samples: Sequence[Dict[str, Any]]) -> str:
    if cfg.modality and cfg.modality != "auto":
        return cfg.modality
    for s in samples:
        if s.get("modality") == "vision" or s.get("images"):
            return "vision"
    return "text"


def messages_to_plain_text(messages: Sequence[Dict[str, Any]]) -> str:
    parts: List[str] = []
    for m in messages:
        role = m.get("role", "user")
        content = str(m.get("content") or "")
        parts.append(f"<|im_start|>{role}\n{content}<|im_end|>")
    return "\n".join(parts)


class MetricsCallback:
    """将 loss 等指标追加写入 metrics.jsonl，并检查 stop flag。"""

    def __init__(
        self,
        metrics_path: str,
        stop_flag_path: Optional[str] = None,
        progress_cb: Optional[ProgressCallback] = None,
    ) -> None:
        self.metrics_path = Path(metrics_path)
        self.metrics_path.parent.mkdir(parents=True, exist_ok=True)
        self.stop_flag_path = Path(stop_flag_path) if stop_flag_path else None
        self.progress_cb = progress_cb
        if not self.metrics_path.exists():
            self.metrics_path.write_text("", encoding="utf-8")

    def on_log(self, args, state, control, logs=None, **kwargs):  # noqa: ANN001
        logs = logs or {}
        row = {
            "step": int(getattr(state, "global_step", 0) or 0),
            "epoch": float(getattr(state, "epoch", 0.0) or 0.0),
            "loss": logs.get("loss"),
            "learning_rate": logs.get("learning_rate"),
        }
        with self.metrics_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
        if self.progress_cb:
            try:
                self.progress_cb(row)
            except Exception:  # noqa: BLE001
                logger.debug("progress_cb failed", exc_info=True)
        if self.stop_flag_path and self.stop_flag_path.exists():
            control.should_training_stop = True
            logger.warning("stop flag detected: %s", self.stop_flag_path)
        return control


def _build_hf_callback(metrics_cb: MetricsCallback):
    from transformers import TrainerCallback  # type: ignore[import-not-found]

    class _Cb(TrainerCallback):  # type: ignore[misc, valid-type]
        def on_log(self, args, state, control, logs=None, **kwargs):  # noqa: ANN001
            return metrics_cb.on_log(args, state, control, logs=logs, **kwargs)

    return _Cb()


class LLMTrainer:
    """统一 LoRA 训练器。"""

    def __init__(self, cfg: LLMTrainingConfig, progress_cb: Optional[ProgressCallback] = None) -> None:
        self.cfg = cfg
        self.progress_cb = progress_cb
        self.modality = "text"

    def train(self) -> None:
        samples = load_samples(self.cfg.dataset_path)
        if not samples:
            raise ValueError(f"empty dataset: {self.cfg.dataset_path}")
        self.modality = infer_modality(self.cfg, samples)
        logger.info("LLMTrainer modality=%s samples=%s", self.modality, len(samples))

        metrics_path = self.cfg.metrics_path or str(Path(self.cfg.output_dir) / "metrics.jsonl")
        stop_flag = self.cfg.stop_flag_path or str(Path(self.cfg.output_dir) / "STOP")
        metrics_cb = MetricsCallback(metrics_path, stop_flag_path=stop_flag, progress_cb=self.progress_cb)

        if self.modality == "vision":
            self._train_vision(samples, metrics_cb)
        else:
            self._train_text(samples, metrics_cb)

    def _train_text(self, samples: List[Dict[str, Any]], metrics_cb: MetricsCallback) -> None:
        import torch  # type: ignore[import-not-found]
        from peft import LoraConfig, TaskType, get_peft_model  # type: ignore[import-not-found]
        from torch.utils.data import Dataset  # type: ignore[import-not-found]
        from transformers import (  # type: ignore[import-not-found]
            AutoModelForCausalLM,
            AutoTokenizer,
            Trainer,
            TrainingArguments,
        )

        tokenizer = AutoTokenizer.from_pretrained(self.cfg.base_model, trust_remote_code=True)
        if tokenizer.pad_token is None:
            tokenizer.pad_token = tokenizer.eos_token

        model = AutoModelForCausalLM.from_pretrained(
            self.cfg.base_model,
            trust_remote_code=True,
            torch_dtype=torch.float16 if self.cfg.fp16 and resolve_torch_device() == "cuda" else None,
        )
        lora_cfg = LoraConfig(
            r=int(self.cfg.lora_r),
            lora_alpha=int(self.cfg.lora_alpha),
            lora_dropout=float(self.cfg.lora_dropout),
            target_modules=resolve_target_modules(self.cfg, "text"),
            bias="none",
            task_type=TaskType.CAUSAL_LM,
        )
        model = get_peft_model(model, lora_cfg)
        model.print_trainable_parameters()

        max_length = int(self.cfg.max_length)

        class TextSFTDataset(Dataset):  # type: ignore[misc, valid-type]
            def __len__(self_inner) -> int:  # noqa: N805
                return len(samples)

            def __getitem__(self_inner, idx: int) -> Dict[str, Any]:  # noqa: N805
                item = samples[idx]
                messages = item.get("messages") or []
                # Prefer tokenizer chat template when available.
                if hasattr(tokenizer, "apply_chat_template"):
                    try:
                        text = tokenizer.apply_chat_template(
                            messages,
                            tokenize=False,
                            add_generation_prompt=False,
                        )
                    except Exception:  # noqa: BLE001
                        text = messages_to_plain_text(messages)
                else:
                    text = messages_to_plain_text(messages)
                enc = tokenizer(
                    text,
                    truncation=True,
                    max_length=max_length,
                    padding=False,
                    return_tensors=None,
                )
                input_ids = enc["input_ids"]
                labels = list(input_ids)
                # Heuristic assistant-only mask: mask tokens before last assistant turn marker if present.
                try:
                    assistant_msgs = [m for m in messages if m.get("role") == "assistant"]
                    if assistant_msgs and hasattr(tokenizer, "apply_chat_template"):
                        prompt_msgs = [m for m in messages if m.get("role") != "assistant"]
                        # include empty assistant header for length approx
                        prompt_msgs_for_len = list(prompt_msgs) + [{"role": "assistant", "content": ""}]
                        prompt_text = tokenizer.apply_chat_template(
                            prompt_msgs_for_len,
                            tokenize=False,
                            add_generation_prompt=False,
                        )
                        prompt_ids = tokenizer(prompt_text, add_special_tokens=False)["input_ids"]
                        cut = min(len(prompt_ids), len(labels))
                        labels = ([-100] * cut) + labels[cut:]
                except Exception:  # noqa: BLE001
                    pass
                return {
                    "input_ids": input_ids,
                    "attention_mask": enc.get("attention_mask") or [1] * len(input_ids),
                    "labels": labels,
                }

        dataset = TextSFTDataset()
        args = self._training_args(use_fp16=self.cfg.fp16 and resolve_torch_device() == "cuda")
        collator = DataCollatorForLanguageModeling(tokenizer=tokenizer, mlm=False)
        # Custom collator that preserves labels already provided
        from dataclasses import dataclass

        @dataclass
        class _LabelCollator:
            tokenizer: Any

            def __call__(self, features: List[Dict[str, Any]]) -> Dict[str, Any]:
                import torch as _torch

                max_len = max(len(f["input_ids"]) for f in features)
                batch_input, batch_mask, batch_labels = [], [], []
                pad_id = self.tokenizer.pad_token_id or 0
                for f in features:
                    ids = f["input_ids"]
                    mask = f.get("attention_mask") or [1] * len(ids)
                    labels = f.get("labels") or list(ids)
                    pad_n = max_len - len(ids)
                    batch_input.append(ids + [pad_id] * pad_n)
                    batch_mask.append(mask + [0] * pad_n)
                    batch_labels.append(labels + [-100] * pad_n)
                return {
                    "input_ids": _torch.tensor(batch_input, dtype=_torch.long),
                    "attention_mask": _torch.tensor(batch_mask, dtype=_torch.long),
                    "labels": _torch.tensor(batch_labels, dtype=_torch.long),
                }

        trainer = Trainer(
            model=model,
            args=args,
            train_dataset=dataset,
            data_collator=_LabelCollator(tokenizer),
            callbacks=[_build_hf_callback(metrics_cb)],
        )
        resume = self.cfg.resume_from_checkpoint
        trainer.train(resume_from_checkpoint=resume if resume else None)
        adapter_dir = Path(self.cfg.output_dir) / "adapter"
        adapter_dir.mkdir(parents=True, exist_ok=True)
        trainer.save_model(str(adapter_dir))
        tokenizer.save_pretrained(str(adapter_dir))
        logger.info("text LoRA training finished, adapter=%s", adapter_dir)

    def _train_vision(self, samples: List[Dict[str, Any]], metrics_cb: MetricsCallback) -> None:
        import torch  # type: ignore[import-not-found]
        from peft import LoraConfig, TaskType, get_peft_model  # type: ignore[import-not-found]
        from PIL import Image  # type: ignore[import-not-found]
        from torch.utils.data import Dataset  # type: ignore[import-not-found]
        from transformers import AutoProcessor, Trainer, TrainingArguments  # type: ignore[import-not-found]

        try:
            from transformers import Qwen2_5_VLForConditionalGeneration  # type: ignore[import-not-found]
        except Exception as exc:  # noqa: BLE001
            raise ImportError(
                "VLM training requires transformers with Qwen2_5_VLForConditionalGeneration. "
                "Install matching transformers / qwen-vl-utils."
            ) from exc

        processor = AutoProcessor.from_pretrained(self.cfg.base_model, trust_remote_code=True)
        dtype = torch.float16 if self.cfg.fp16 and resolve_torch_device() == "cuda" else torch.float32
        model = Qwen2_5_VLForConditionalGeneration.from_pretrained(
            self.cfg.base_model,
            torch_dtype=dtype,
            trust_remote_code=True,
            low_cpu_mem_usage=True,
            device_map="auto" if resolve_torch_device() == "cuda" else None,
        )
        lora_cfg = LoraConfig(
            r=int(self.cfg.lora_r),
            lora_alpha=int(self.cfg.lora_alpha),
            lora_dropout=float(self.cfg.lora_dropout),
            target_modules=resolve_target_modules(self.cfg, "vision"),
            bias="none",
            task_type=TaskType.CAUSAL_LM,
            inference_mode=False,
        )
        model = get_peft_model(model, lora_cfg)
        model.print_trainable_parameters()

        image_size = int(self.cfg.image_size)
        max_length = int(self.cfg.max_length)

        class VisionSFTDataset(Dataset):  # type: ignore[misc, valid-type]
            def __len__(self_inner) -> int:  # noqa: N805
                return len(samples)

            def _load_image(self_inner, paths: Sequence[str]) -> Image.Image:  # noqa: N805
                for p in paths:
                    if p and os.path.exists(p):
                        try:
                            img = Image.open(p).convert("RGB")
                            if img.size != (image_size, image_size):
                                img = img.resize((image_size, image_size), Image.Resampling.LANCZOS)
                            return img
                        except Exception as exc:  # noqa: BLE001
                            logger.warning("failed to open image %s: %s", p, exc)
                return Image.new("RGB", (image_size, image_size), color="black")

            def __getitem__(self_inner, idx: int) -> Dict[str, Any]:  # noqa: N805
                item = samples[idx]
                messages = item.get("messages") or []
                text = messages_to_plain_text(messages)
                # Ensure image placeholder exists for Qwen-VL style prompts.
                if "<image>" not in text and "<|image_pad|>" not in text:
                    text = text.replace("<|im_start|>user\n", "<|im_start|>user\n<|image_pad|>", 1)
                else:
                    text = text.replace("<image>", "<|image_pad|>")
                image = self_inner._load_image(item.get("images") or [])
                inputs = processor(
                    text=text,
                    images=image,
                    return_tensors="pt",
                    max_length=max_length,
                    truncation=True,
                    padding=True,
                )
                for key in list(inputs.keys()):
                    if hasattr(inputs[key], "cpu"):
                        inputs[key] = inputs[key].cpu()
                input_ids = inputs["input_ids"].squeeze(0)
                attention_mask = inputs["attention_mask"].squeeze(0)
                labels = input_ids.clone()
                labels[attention_mask == 0] = -100
                pixel_values = inputs.get("pixel_values")
                if pixel_values is not None:
                    if pixel_values.dim() == 4:
                        pixel_values = pixel_values.squeeze(0)
                else:
                    pixel_values = torch.zeros((3, image_size, image_size), dtype=torch.float32)
                image_grid_thw = inputs.get("image_grid_thw")
                if image_grid_thw is not None:
                    if image_grid_thw.dim() == 2:
                        image_grid_thw = image_grid_thw.squeeze(0)
                    elif image_grid_thw.dim() == 0:
                        image_grid_thw = torch.tensor([1, 14, 14], dtype=torch.long)
                else:
                    image_grid_thw = torch.tensor([1, 14, 14], dtype=torch.long)
                return {
                    "input_ids": input_ids,
                    "attention_mask": attention_mask,
                    "labels": labels,
                    "pixel_values": pixel_values,
                    "image_grid_thw": image_grid_thw,
                }

        class VisionCollator:
            def __init__(self, max_len: int) -> None:
                self.max_length = max_len

            def __call__(self, features: List[Dict[str, Any]]) -> Dict[str, Any]:
                max_len = min(max(len(f["input_ids"]) for f in features), self.max_length)
                bs = len(features)
                input_ids = torch.zeros((bs, max_len), dtype=torch.long)
                attention_mask = torch.zeros((bs, max_len), dtype=torch.long)
                labels = torch.full((bs, max_len), -100, dtype=torch.long)
                for i, f in enumerate(features):
                    seq_len = min(len(f["input_ids"]), max_len)
                    input_ids[i, :seq_len] = f["input_ids"][:seq_len]
                    attention_mask[i, :seq_len] = f["attention_mask"][:seq_len]
                    labels[i, :seq_len] = f["labels"][:seq_len]
                pixel_values = torch.stack([f["pixel_values"] for f in features])
                image_grid_thw = torch.stack([f["image_grid_thw"] for f in features])
                return {
                    "input_ids": input_ids,
                    "attention_mask": attention_mask,
                    "labels": labels,
                    "pixel_values": pixel_values,
                    "image_grid_thw": image_grid_thw,
                }

        dataset = VisionSFTDataset()
        args = self._training_args(use_fp16=self.cfg.fp16 and resolve_torch_device() == "cuda")
        trainer = Trainer(
            model=model,
            args=args,
            train_dataset=dataset,
            data_collator=VisionCollator(max_length),
            callbacks=[_build_hf_callback(metrics_cb)],
        )
        resume = self.cfg.resume_from_checkpoint
        trainer.train(resume_from_checkpoint=resume if resume else None)
        adapter_dir = Path(self.cfg.output_dir) / "adapter"
        adapter_dir.mkdir(parents=True, exist_ok=True)
        trainer.save_model(str(adapter_dir))
        processor.save_pretrained(str(adapter_dir))
        logger.info("vision LoRA training finished, adapter=%s", adapter_dir)

    def _training_args(self, *, use_fp16: bool):
        from transformers import TrainingArguments  # type: ignore[import-not-found]

        return TrainingArguments(
            output_dir=self.cfg.output_dir,
            num_train_epochs=int(self.cfg.num_epochs),
            per_device_train_batch_size=int(self.cfg.batch_size),
            learning_rate=float(self.cfg.learning_rate),
            gradient_accumulation_steps=int(self.cfg.gradient_accumulation_steps),
            warmup_steps=int(self.cfg.warmup_steps),
            weight_decay=float(self.cfg.weight_decay),
            max_grad_norm=float(self.cfg.max_grad_norm),
            logging_steps=int(self.cfg.logging_steps),
            save_steps=int(self.cfg.save_steps),
            save_total_limit=int(self.cfg.save_total_limit),
            remove_unused_columns=False,
            dataloader_pin_memory=False,
            dataloader_num_workers=0,
            fp16=bool(use_fp16 and not self.cfg.bf16),
            bf16=bool(self.cfg.bf16),
            report_to=[],
            logging_dir=str(Path(self.cfg.output_dir) / "hf_logs"),
        )
