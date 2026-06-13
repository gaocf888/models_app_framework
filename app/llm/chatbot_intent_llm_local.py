"""
智能客服意图轻量 LLM：进程内 CPU 推理（与 EmbeddingService 相同的离线优先策略）。

优先从 CHATBOT_INTENT_LLM_MODEL_PATH 加载；否则用 CHATBOT_INTENT_LLM_MODEL_NAME 从 HuggingFace 拉取。
"""

from __future__ import annotations

import asyncio
import threading
from typing import Any, Dict, List

from app.core.config import get_app_config
from app.core.logging import get_logger

logger = get_logger(__name__)


class ChatbotIntentLocalLlm:
    """Qwen2.5-0.5B-Instruct 等小型 CausalLM，CPU 窄触发意图分类。"""

    _instance: ChatbotIntentLocalLlm | None = None
    _lock = threading.Lock()

    def __init__(self) -> None:
        cfg = get_app_config().chatbot
        self._model_path = (cfg.intent_llm_model_path or "").strip() or None
        self._model_name = (cfg.intent_llm_model_name or "Qwen/Qwen2.5-0.5B-Instruct").strip()
        self._device = (cfg.intent_llm_device or "cpu").strip()
        self._max_tokens = max(32, int(cfg.intent_llm_max_tokens))
        self._temperature = float(cfg.intent_llm_temperature)
        self._tokenizer = None
        self._model = None
        self._load_error: str | None = None

    @classmethod
    def get_instance(cls) -> ChatbotIntentLocalLlm:
        with cls._lock:
            if cls._instance is None:
                cls._instance = cls()
            return cls._instance

    @classmethod
    def reset_instance_for_tests(cls) -> None:
        with cls._lock:
            cls._instance = None

    def _ensure_loaded(self) -> bool:
        if self._model is not None and self._tokenizer is not None:
            return True
        if self._load_error:
            return False

        import os

        import torch
        from transformers import AutoModelForCausalLM, AutoTokenizer

        targets: list[str] = []
        if self._model_path and os.path.isdir(self._model_path):
            targets.append(self._model_path)
        if self._model_name and self._model_name not in targets:
            targets.append(self._model_name)

        last_err: Exception | None = None
        for target in targets:
            try:
                tok = AutoTokenizer.from_pretrained(target, trust_remote_code=True)
                model = AutoModelForCausalLM.from_pretrained(
                    target,
                    trust_remote_code=True,
                    torch_dtype=torch.float32,
                )
                device = self._device
                if device.startswith("cuda") and not torch.cuda.is_available():
                    logger.warning("ChatbotIntentLocalLlm: cuda unavailable, using cpu")
                    device = "cpu"
                model.to(device)
                model.eval()
                self._tokenizer = tok
                self._model = model
                self._device = device
                logger.info("ChatbotIntentLocalLlm: loaded target=%s device=%s", target, device)
                return True
            except Exception as e:
                last_err = e
                logger.warning("ChatbotIntentLocalLlm: failed target=%s err=%s", target, e)

        self._load_error = str(last_err or "no_model_path_or_name")
        logger.error("ChatbotIntentLocalLlm: all load attempts failed err=%s", self._load_error)
        return False

    def _generate_sync(self, messages: List[Dict[str, str]]) -> str:
        import torch

        if not self._ensure_loaded():
            raise RuntimeError(self._load_error or "intent_llm_not_loaded")

        assert self._tokenizer is not None
        assert self._model is not None

        if hasattr(self._tokenizer, "apply_chat_template"):
            prompt = self._tokenizer.apply_chat_template(
                messages,
                tokenize=False,
                add_generation_prompt=True,
            )
        else:
            parts = [f"[{m.get('role', 'user')}] {m.get('content', '')}" for m in messages]
            prompt = "\n".join(parts)

        inputs = self._tokenizer(prompt, return_tensors="pt", truncation=True, max_length=2048)
        inputs = {k: v.to(self._device) for k, v in inputs.items()}
        do_sample = self._temperature > 0.01
        with torch.no_grad():
            out = self._model.generate(
                **inputs,
                max_new_tokens=self._max_tokens,
                do_sample=do_sample,
                temperature=max(0.01, self._temperature) if do_sample else None,
                pad_token_id=self._tokenizer.eos_token_id,
            )
        new_tokens = out[0][inputs["input_ids"].shape[-1] :]
        return self._tokenizer.decode(new_tokens, skip_special_tokens=True).strip()

    async def generate(self, messages: List[Dict[str, str]]) -> str:
        return await asyncio.to_thread(self._generate_sync, messages)
