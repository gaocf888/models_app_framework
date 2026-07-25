from __future__ import annotations

"""LLaMA-Factory 集成适配层（骨架）。生产接线时可补 HTTP / subprocess。"""

from dataclasses import dataclass
from typing import Dict, Optional

from app.core.logging import get_logger

logger = get_logger(__name__)


@dataclass
class LLaMAFactoryConfig:
    base_model: str
    dataset_path: str
    output_dir: str
    extra_args: Dict[str, str] | None = None


class LLaMAFactoryAdapter:
    """与 LLaMA-Factory 的集成适配器（当前仅记录参数，不发起真实训练）。"""

    def __init__(self, endpoint: Optional[str] = None, script_path: Optional[str] = None) -> None:
        self._endpoint = endpoint
        self._script_path = script_path

    def start_training(self, cfg: LLaMAFactoryConfig) -> None:
        logger.info(
            "LLaMA-Factory adapter placeholder: base_model=%s dataset=%s output=%s endpoint=%s script=%s",
            cfg.base_model,
            cfg.dataset_path,
            cfg.output_dir,
            self._endpoint,
            self._script_path,
        )
        raise NotImplementedError(
            "LLaMA-Factory channel is not wired yet. Use mode=code with LLMTrainer instead."
        )
