from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Dict

import yaml

from app.core.logging import get_logger

logger = get_logger(__name__)


@dataclass
class SmallModelMeta:
    """
    小模型元数据。
    """

    name: str
    description: str | None = None
    weights_path: str | None = None
    task_type: str | None = None
    threshold: float | None = None
    max_limit: int | None = None


class SmallModelRegistry:
    """
    小模型注册表。

    - 启动时从 configs/small_models.yaml 加载所有小模型配置；
    - 生产中可扩展为从数据库或配置中心加载，支持热更新。
    """

    def __init__(self, config_path: str | None = None) -> None:
        self._models: Dict[str, SmallModelMeta] = {}
        self._config_path = config_path or "configs/small_models.yaml"
        self._load_from_yaml()

    def _load_from_yaml(self) -> None:
        path = Path(self._config_path)
        if not path.exists():
            logger.warning("small model config file not found: %s", path)
            return
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        for name, cfg in (data.get("models") or {}).items():
            meta = SmallModelMeta(
                name=cfg.get("name", name),
                description=cfg.get("description"),
                weights_path=cfg.get("weights_path"),
                task_type=cfg.get("task_type"),
                threshold=cfg.get("threshold"),
                max_limit=cfg.get("max_limit"),
            )
            self._models[meta.name] = meta
        logger.info("loaded %d small models from %s", len(self._models), path)

    def register(self, meta: SmallModelMeta) -> None:
        self._models[meta.name] = meta

    def get(self, name: str) -> SmallModelMeta | None:
        return self._models.get(name)

    def list(self) -> Dict[str, SmallModelMeta]:
        return dict(self._models)

