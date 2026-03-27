from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional

import yaml

from app.core.logging import get_logger

logger = get_logger(__name__)


@dataclass(frozen=True)
class AlgorithmConfig:
    algor_type: str
    name: str | None = None
    description: str | None = None
    strategy: str | None = None
    model_name: str | None = None
    weights_path: str | None = None
    device: str | None = None
    imgsz: int | None = None
    conf: float | None = None
    iou: float | None = None
    cooldown_seconds: int | None = None
    evidence_dir: str | None = None
    clip_seconds: int | None = None
    callback_url: str | None = None


class SmallModelAlgorithmRegistry:
    """
    algor_type -> AlgorithmConfig

    来源：configs/small_model_algorithms.yaml（本地配置）
    """

    def __init__(self, config_path: str | None = None) -> None:
        self._algorithms: Dict[str, AlgorithmConfig] = {}
        self._config_path = config_path or "configs/small_model_algorithms.yaml"
        self._load_from_yaml()

    def _load_from_yaml(self) -> None:
        path = Path(self._config_path)
        if not path.exists():
            logger.warning("small model algorithms config file not found: %s", path)
            return
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        algos = data.get("algorithms") or {}
        for algor_type, cfg in algos.items():
            cfg = cfg or {}
            self._algorithms[str(algor_type)] = AlgorithmConfig(
                algor_type=str(algor_type),
                name=cfg.get("name"),
                description=cfg.get("description"),
                strategy=cfg.get("strategy"),
                model_name=cfg.get("model_name"),
                weights_path=cfg.get("weights_path"),
                device=cfg.get("device"),
                imgsz=cfg.get("imgsz"),
                conf=cfg.get("conf"),
                iou=cfg.get("iou"),
                cooldown_seconds=cfg.get("cooldown_seconds"),
                evidence_dir=cfg.get("evidence_dir"),
                clip_seconds=cfg.get("clip_seconds"),
                callback_url=cfg.get("callback_url"),
            )
        logger.info("loaded %d algorithm configs from %s", len(self._algorithms), path)

    def get(self, algor_type: str | None) -> AlgorithmConfig | None:
        if not algor_type:
            return None
        return self._algorithms.get(str(algor_type))


def resolve_path(path_str: str | None) -> str | None:
    if not path_str:
        return None
    p = Path(path_str)
    if p.is_absolute():
        return str(p)
    # workspace relative
    return str((Path.cwd() / p).resolve())


def merge_algorithm_config(base: AlgorithmConfig | None, overrides: Dict[str, Any]) -> AlgorithmConfig:
    """
    API 覆盖本地配置：overrides 中非 None 的值覆盖 base。
    """
    algor_type = str(overrides.get("algor_type") or (base.algor_type if base else ""))
    if not algor_type:
        raise ValueError("algor_type is required to resolve algorithm config")

    def pick(key: str) -> Any:
        if key in overrides and overrides.get(key) is not None:
            return overrides.get(key)
        return getattr(base, key) if base is not None else None

    return AlgorithmConfig(
        algor_type=algor_type,
        name=pick("name"),
        description=pick("description"),
        strategy=pick("strategy"),
        model_name=pick("model_name"),
        weights_path=pick("weights_path"),
        device=pick("device"),
        imgsz=pick("imgsz"),
        conf=pick("conf"),
        iou=pick("iou"),
        cooldown_seconds=pick("cooldown_seconds"),
        evidence_dir=pick("evidence_dir"),
        clip_seconds=pick("clip_seconds"),
        callback_url=pick("callback_url"),
    )

