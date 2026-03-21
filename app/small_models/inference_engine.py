from __future__ import annotations

from app.core.logging import get_logger
from app.small_models.registry import SmallModelRegistry

logger = get_logger(__name__)


class SmallModelInferenceEngine:
    """
    小模型推理引擎占位实现。

    - 当前仅根据 model_name 从 SmallModelRegistry 读取元数据，并记录日志；
    - 生产环境中应在此处基于 weights_path 加载对应算法与权重，
      并执行真正的检测/分类/分割推理逻辑。

    说明：
    - 通过 extra_params 中的 algor_type 可区分不同任务（如安全帽/打电话等），便于选择不同后处理。
    """

    def __init__(self) -> None:
        self._registry = SmallModelRegistry()

    def infer(self, model_name: str, frame_item: dict) -> None:
        meta = self._registry.get(model_name)
        algor_type = frame_item.get("algor_type") or (meta.task_type if meta else None)
        logger.debug(
            "infer with model=%s, task_type=%s, weights=%s, frame_meta=%s",
            model_name,
            algor_type,
            meta.weights_path if meta else None,
            {k: v for k, v in frame_item.items() if k != "frame"},
        )

