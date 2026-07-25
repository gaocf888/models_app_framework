from __future__ import annotations

"""训练设备探测（CUDA / CPU）。"""

from typing import Any

from app.core.logging import get_logger

logger = get_logger(__name__)


def resolve_torch_device() -> str:
    try:
        import torch  # type: ignore[import-not-found]
    except Exception:  # noqa: BLE001
        logger.warning("torch not installed; device fallback=cpu")
        return "cpu"
    if torch.cuda.is_available():
        name = torch.cuda.get_device_name(0)
        logger.info("training device=cuda (%s)", name)
        return "cuda"
    logger.info("training device=cpu")
    return "cpu"


def setup_training_env() -> dict[str, Any]:
    import os

    os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")
    os.environ.setdefault("TRANSFORMERS_NO_ADVISORY_WARNINGS", "true")
    device = resolve_torch_device()
    return {"device": device}
