from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional

import yaml

from app.core.logging import get_logger

logger = get_logger(__name__)


@dataclass
class PromptTemplate:
    scene: str
    version: str
    weight: float
    description: str | None
    content: str


class PromptTemplateRegistry:
    """
    提示词模板与 A/B 策略中心。

    - 从 configs/prompts.yaml 加载多场景、多版本模板；
    - 支持按场景 + 用户 ID 做简单的哈希分流，实现 A/B 测试；
    - 也支持显式指定版本（便于灰度/回放）。
    """

    def __init__(self, config_path: str | None = None) -> None:
        self._config_path = config_path or "configs/prompts.yaml"
        self._templates: Dict[str, List[PromptTemplate]] = {}
        self._load_from_yaml()

    def _load_from_yaml(self) -> None:
        path = Path(self._config_path)
        if not path.exists():
            logger.warning("prompt config file not found: %s", path)
            return
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        for scene, items in data.items():
            lst: List[PromptTemplate] = []
            for item in items or []:
                tpl = PromptTemplate(
                    scene=scene,
                    version=str(item.get("version", "v1")),
                    weight=float(item.get("weight", 1.0)),
                    description=item.get("description"),
                    content=item.get("content", ""),
                )
                lst.append(tpl)
            self._templates[scene] = lst
        logger.info("loaded prompt templates for scenes: %s", list(self._templates.keys()))

    def get_template(self, scene: str, user_id: Optional[str] = None, version: Optional[str] = None) -> Optional[PromptTemplate]:
        """
        获取指定场景的提示词模板。

        - 如显式提供 version，则按版本精确匹配；
        - 否则，按 user_id 做简单哈希分流，根据权重选择一个版本；
        - 若场景未配置模板则返回 None。
        """
        templates = self._templates.get(scene)
        if not templates:
            return None

        if version:
            for tpl in templates:
                if tpl.version == version:
                    return tpl
            return templates[0]

        if len(templates) == 1 or not user_id:
            return templates[0]

        # 简单哈希分流：根据 user_id 对权重区间取模
        total_weight = sum(max(t.weight, 0.0) for t in templates)
        if total_weight <= 0:
            return templates[0]

        h = hashlib.sha256(user_id.encode("utf-8")).hexdigest()
        rnd = int(h[:8], 16) / 0xFFFFFFFF  # 0~1
        threshold = rnd * total_weight

        acc = 0.0
        for tpl in templates:
            acc += max(tpl.weight, 0.0)
            if threshold <= acc:
                return tpl

        return templates[-1]

