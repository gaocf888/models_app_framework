from __future__ import annotations

"""
LLaMA-Factory 集成适配层（骨架版）。

设计目标：
- 统一管理与 LLaMA-Factory 服务/脚本 的交互；
- 通过配置指定模型、数据集、输出目录等参数；
- 为后续接入 Web UI 或命令行训练提供清晰入口。

当前实现：
- 仅定义配置与启动占位接口，具体调用逻辑由实际部署的 LLaMA-Factory 决定；
- 建议在生产环境中通过环境变量或配置文件指定 LLaMA-Factory 的地址/脚本路径。
"""

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
    """
    与 LLaMA-Factory 的集成适配器。

    在实际环境中可以：
    - 调用本地脚本（如 python cli.py ...）；
    - 调用远程 API（如通过 HTTP 向 LLaMA-Factory 服务提交训练任务）。
    """

    def __init__(self, endpoint: Optional[str] = None, script_path: Optional[str] = None) -> None:
        self._endpoint = endpoint  # 远程服务地址（如果有）
        self._script_path = script_path  # 本地脚本路径（如果走命令行模式）

    def start_training(self, cfg: LLaMAFactoryConfig) -> None:
        """
        启动训练/微调任务（占位实现）。

        说明：
        - 在这里根据实际情况选择使用 HTTP 或 subprocess 调用；
        - 当前仅记录日志，方便后续接入真实调用逻辑。
        """
        logger.info(
            "start LLaMA-Factory training: base_model=%s, dataset=%s, output=%s, endpoint=%s, script=%s",
            cfg.base_model,
            cfg.dataset_path,
            cfg.output_dir,
            self._endpoint,
            self._script_path,
        )

