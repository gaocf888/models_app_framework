from __future__ import annotations

"""
LangSmith tracing 集成中间层（可选启用）。

设计目标：
- 不强依赖 LangSmith SDK 或 LangChain 内置 tracing 配置；
- 通过环境变量控制开关与项目配置；
- 在关键链路（LLMInference/Chatbot/Analysis/NL2SQL）记录最小必要的信息；
- 当未配置或出现异常时自动降级为 no-op，不影响主流程。
"""

from dataclasses import dataclass
from typing import Any, Dict, Optional

from app.core.logging import get_logger

logger = get_logger(__name__)


@dataclass
class LangSmithSettings:
    enabled: bool
    api_key: Optional[str]
    project: Optional[str]


def _load_settings() -> LangSmithSettings:
    import os

    api_key = os.getenv("LANGSMITH_API_KEY")
    project = os.getenv("LANGSMITH_PROJECT")
    explicit_disabled = os.getenv("LANGSMITH_ENABLED", "").lower() == "false"

    enabled = bool(api_key and project) and not explicit_disabled
    if not enabled:
        logger.info("LangSmithTracker: disabled (missing env or explicitly disabled).")
    return LangSmithSettings(enabled=enabled, api_key=api_key, project=project)


class LangSmithTracker:
    """
    LangSmith trace 记录器。

    当前实现：
    - 若未配置环境变量或安装 SDK，则所有方法为 no-op；
    - 若可用，则通过 langsmith.Client.create_run 记录基础 run 信息。
    """

    def __init__(self) -> None:
        self._settings = _load_settings()
        self._client = None

        if not self._settings.enabled:
            return

        try:
            from langsmith import Client  # type: ignore[import-not-found]

            self._client = Client(
                api_key=self._settings.api_key,
                project=self._settings.project,
            )
            logger.info("LangSmithTracker: initialized for project=%s", self._settings.project)
        except Exception as exc:  # noqa: BLE001
            logger.warning("LangSmithTracker: failed to initialize LangSmith client: %s", exc)
            self._client = None

    @property
    def enabled(self) -> bool:
        return self._client is not None

    def log_run(
        self,
        name: str,
        run_type: str,
        inputs: Dict[str, Any],
        outputs: Optional[Dict[str, Any]] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        """
        记录一次简单的 LangSmith run。
        """
        if not self.enabled:
            return

        try:
            # create_run 接口的字段名参考 LangSmith 官方 Python SDK
            self._client.create_run(  # type: ignore[union-attr]
                name=name,
                run_type=run_type,
                inputs=inputs,
                outputs=outputs or {},
                extra=metadata or {},
            )
        except Exception as exc:  # noqa: BLE001
            # 不影响主流程，仅记录日志
            logger.warning("LangSmithTracker: failed to log run '%s': %s", name, exc)

