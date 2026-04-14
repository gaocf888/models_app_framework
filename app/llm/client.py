from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, AsyncIterator, Dict, List, Optional

import httpx
import json
import time

from app.core.config import LLMModelConfig, get_app_config
from app.core.logging import get_logger
from app.core.metrics import LLM_REQUEST_COUNT, LLM_REQUEST_LATENCY

logger = get_logger(__name__)


def openai_chat_completions_url(endpoint: str) -> str:
    """
    将配置的 LLM 根地址解析为 OpenAI 兼容的 chat completions URL。

    支持两种常见写法（与 `LLM_DEFAULT_ENDPOINT` 文档一致）：
    - `http://host:8000` → `http://host:8000/v1/chat/completions`
    - `http://host:8000/v1`（或 `/v1/`）→ `http://host:8000/v1/chat/completions`，避免拼成 `/v1/v1/...`
    """
    base = (endpoint or "").rstrip("/")
    if base.lower().endswith("/v1"):
        return f"{base}/chat/completions"
    return f"{base}/v1/chat/completions"


class LLMClient(ABC):
    """
    大模型客户端抽象。

    后续可扩展为：
    - vLLM 本地/私有化部署；
    - 云端大模型（遵循 OpenAI 兼容协议或各家自定义协议）。
    """

    @abstractmethod
    async def generate(self, model: str, prompt: str, **kwargs: Any) -> str:
        """
        执行一次非流式推理，返回完整回答。
        """
        ...

    async def chat(self, model: str, messages: List[Dict[str, Any]], **kwargs: Any) -> str:
        """
        聊天消息接口（默认退化为将消息拼接后调用 generate）。
        """
        joined = []
        for m in messages:
            role = m.get("role", "user")
            content = m.get("content", "")
            joined.append(f"[{role}] {content}")
        return await self.generate(model=model, prompt="\n".join(joined), **kwargs)

    async def stream_generate(self, model: str, prompt: str, **kwargs: Any) -> AsyncIterator[str]:
        """
        执行一次流式推理，默认实现退化为一次性 generate。
        子类可按需覆盖以支持真正的流式输出。
        """
        yield await self.generate(model, prompt, **kwargs)

    async def stream_chat(
        self,
        model: str,
        messages: List[Dict[str, Any]],
        **kwargs: Any,
    ) -> AsyncIterator[str]:
        """
        聊天消息流式接口（默认退化为一次性 chat）。
        """
        yield await self.chat(model=model, messages=messages, **kwargs)


class VLLMHttpClient(LLMClient):
    """
    面向 vLLM 的 HTTP 客户端占位实现。

    假定 vLLM 暴露 OpenAI 兼容接口（`/v1/chat/completions`）。
    `LLMModelConfig.endpoint` 可为服务根地址或已带 `/v1` 的 base（见 `openai_chat_completions_url`）。
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
        return await self.chat(
            model=model,
            messages=[{"role": "user", "content": prompt}],
            max_tokens=kwargs.get("max_tokens"),
            temperature=kwargs.get("temperature"),
            top_p=kwargs.get("top_p"),
            seed=kwargs.get("seed"),
        )

    async def chat(self, model: str, messages: List[Dict[str, Any]], **kwargs: Any) -> str:
        cfg = self._get_model_cfg(model)
        url = openai_chat_completions_url(cfg.endpoint)

        payload: Dict[str, Any] = {
            "model": cfg.model_id,
            "messages": messages,
            "max_tokens": kwargs.get("max_tokens", cfg.max_tokens),
            "temperature": kwargs.get("temperature", cfg.temperature),
        }
        if kwargs.get("top_p") is not None:
            payload["top_p"] = kwargs["top_p"]
        if kwargs.get("seed") is not None:
            payload["seed"] = kwargs["seed"]
        payload.update(cfg.extras or {})

        headers: Dict[str, str] = {}
        if cfg.api_key:
            headers["Authorization"] = f"Bearer {cfg.api_key}"

        logger.debug("calling vLLM(chat) model=%s endpoint=%s", cfg.model_id, url)

        start = time.perf_counter()
        resp = await self._client.post(url, json=payload, headers=headers)
        duration = time.perf_counter() - start

        LLM_REQUEST_COUNT.labels(model=cfg.model_id).inc()
        LLM_REQUEST_LATENCY.labels(model=cfg.model_id).observe(duration)
        if resp.status_code >= 400:
            logger.error(
                "vLLM chat HTTP %s url=%s body=%s",
                resp.status_code,
                url,
                (resp.text or "")[:4000],
            )
        resp.raise_for_status()
        data = resp.json()

        try:
            content = data["choices"][0]["message"]["content"]
            if isinstance(content, str):
                return content
            if isinstance(content, list):
                # 兼容多模态/结构化返回，提取文本片段
                texts = []
                for it in content:
                    if isinstance(it, dict) and it.get("type") == "text":
                        texts.append(str(it.get("text", "")))
                return "".join(texts)
            return str(content)
        except Exception as exc:  # noqa: BLE001
            logger.exception("failed to parse vLLM chat response: %s", exc)
            raise

    async def stream_generate(self, model: str, prompt: str, **kwargs: Any) -> AsyncIterator[str]:
        """
        使用 vLLM/OpenAI 兼容接口执行流式推理（SSE 格式）。
        """
        async for token in self.stream_chat(
            model=model,
            messages=[{"role": "user", "content": prompt}],
            max_tokens=kwargs.get("max_tokens"),
            temperature=kwargs.get("temperature"),
        ):
            yield token

    async def stream_chat(
        self,
        model: str,
        messages: List[Dict[str, Any]],
        **kwargs: Any,
    ) -> AsyncIterator[str]:
        cfg = self._get_model_cfg(model)
        url = openai_chat_completions_url(cfg.endpoint)
        payload: Dict[str, Any] = {
            "model": cfg.model_id,
            "messages": messages,
            "stream": True,
            "max_tokens": kwargs.get("max_tokens", cfg.max_tokens),
            "temperature": kwargs.get("temperature", cfg.temperature),
        }
        if kwargs.get("top_p") is not None:
            payload["top_p"] = kwargs["top_p"]
        if kwargs.get("seed") is not None:
            payload["seed"] = kwargs["seed"]
        payload.update(cfg.extras or {})

        headers: Dict[str, str] = {}
        if cfg.api_key:
            headers["Authorization"] = f"Bearer {cfg.api_key}"

        logger.debug("calling vLLM(stream_chat) model=%s endpoint=%s", cfg.model_id, url)

        start = time.perf_counter()
        async with self._client.stream("POST", url, json=payload, headers=headers) as resp:
            if resp.status_code >= 400:
                err_body = (await resp.aread()).decode("utf-8", errors="replace")[:4000]
                logger.error("vLLM stream_chat HTTP %s url=%s body=%s", resp.status_code, url, err_body)
            resp.raise_for_status()
            async for line in resp.aiter_lines():
                if not line or not line.startswith("data:"):
                    continue
                chunk = line[len("data:") :].strip()
                if chunk == "[DONE]":
                    break
                try:
                    data = json.loads(chunk)
                    delta = data["choices"][0]["delta"].get("content") or ""
                    if isinstance(delta, str) and delta:
                        yield delta
                except Exception as exc:  # noqa: BLE001
                    logger.exception("failed to parse vLLM stream chunk: %s", exc)
                    continue

        duration = time.perf_counter() - start
        LLM_REQUEST_COUNT.labels(model=cfg.model_id).inc()
        LLM_REQUEST_LATENCY.labels(model=cfg.model_id).observe(duration)

