from __future__ import annotations

from typing import Dict

from app.core.config import LLMConfig, LLMModelConfig, get_app_config


class LLMConfigRegistry:
    """
    大模型配置注册中心。

    负责：
    - 提供对当前 AppConfig.llm 的封装访问；
    - 为后续动态加载/刷新模型配置预留接口。
    """

    def __init__(self) -> None:
        self._cfg: LLMConfig = get_app_config().llm

    @property
    def default_model(self) -> str:
        return self._cfg.default_model

    def get_model(self, name: str | None = None) -> LLMModelConfig:
        if name is None:
            name = self._cfg.default_model
        try:
            return self._cfg.models[name]
        except KeyError as exc:
            raise KeyError(f"LLM model '{name}' not found in registry") from exc

    def list_models(self) -> Dict[str, LLMModelConfig]:
        return dict(self._cfg.models)

