from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional

import httpx

from app.core.config import LLMModelConfig, get_app_config
from app.core.logging import get_logger
from app.core.metrics import LLM_REQUEST_COUNT, LLM_REQUEST_LATENCY

logger = get_logger(__name__)


class LLMClient(ABC):
    """
    大模型客户端抽象。

    后续可扩展为：
    - vLLM 本地/私有化部署；
    - 云端大模型（遵循 OpenAI 兼容协议或各家自定义协议）。
    """

    @abstractmethod
    async def generate(self, model: str, prompt: str, **kwargs: Any) -> str:
        ...


class VLLMHttpClient(LLMClient):
    """
    面向 vLLM 的 HTTP 客户端占位实现。

    假定 vLLM 暴露 OpenAI 兼容接口（/v1/chat/completions 或 /v1/completions），
    实际路径和参数可以通过 LLMModelConfig.extras 中的配置进行调整。
    """

    def __init__(self, timeout: float = 30.0) -> None:
        self._cfg = get_app_config().llm
        self._client = httpx.AsyncClient(timeout=timeout)

    def _get_model_cfg(self, model: str) -> LLMModelConfig:
        if model in self._cfg.models:
            return self._cfg.models[model]
        if self._cfg.default_model in self._cfg.models:
            return self._cfg.models[self._cfg.default_model]
        raise ValueError(f"model '{model}' not configured")

    async def generate(self, model: str, prompt: str, **kwargs: Any) -> str:
        cfg = self._get_model_cfg(model)
        url = cfg.endpoint.rstrip("/") + "/v1/chat/completions"

        payload: Dict[str, Any] = {
            "model": cfg.model_id,
            "messages": [
                {"role": "user", "content": prompt},
            ],
            "max_tokens": kwargs.get("max_tokens", cfg.max_tokens),
            "temperature": kwargs.get("temperature", cfg.temperature),
        }
        payload.update(cfg.extras or {})

        headers: Dict[str, str] = {}
        if cfg.api_key:
            headers["Authorization"] = f"Bearer {cfg.api_key}"

        logger.debug("calling vLLM model=%s endpoint=%s", cfg.model_id, url)

        import time

        start = time.perf_counter()
        resp = await self._client.post(url, json=payload, headers=headers)
        duration = time.perf_counter() - start

        LLM_REQUEST_COUNT.labels(model=cfg.model_id).inc()
        LLM_REQUEST_LATENCY.labels(model=cfg.model_id).observe(duration)
        resp.raise_for_status()
        data = resp.json()

        # OpenAI 兼容格式解析
        try:
            return data["choices"][0]["message"]["content"]
        except Exception as exc:  # noqa: BLE001
            logger.exception("failed to parse vLLM response: %s", exc)
            raise

