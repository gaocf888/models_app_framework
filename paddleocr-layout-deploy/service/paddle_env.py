"""
Paddle / PaddleOCR 在部分 CPU（虚拟化、缺 AVX2、或指令集报告不准）上推理阶段 SIGILL 的缓解。

在 `import paddle` / `import paddleocr` 之前调用 `apply_paddle_runtime_flags()`；
`PaddleOCR` / `PPStructure` 的 `ir_optim` / `enable_mkldnn` 与下列环境变量对齐。
"""

from __future__ import annotations

import os
from typing import Any


def env_bool(name: str, *, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


def apply_paddle_runtime_flags() -> None:
    """在首次 import paddleocr / paddle 前调用一次即可。"""
    if not env_bool("PADDLE_LAYOUT_OCR_ENABLE_MKLDNN", default=False):
        os.environ["FLAGS_use_mkldnn"] = "0"


def paddle_infer_kw() -> dict[str, Any]:
    """
    传给 PaddleOCR / PPStructure 等与推理相关的布尔开关。

    默认 ir_optim=False、enable_mkldnn=False，避免 AnalysisPredictor 在 IR 融合阶段
    生成当前 CPU 不支持的指令（现场日志常见 SIGILL + SelfAttentionFusePass）。
    性能优先且 CPU 正常时，可在 .env 中设 PADDLE_LAYOUT_OCR_IR_OPTIM=true 等。
    """
    return {
        "ir_optim": env_bool("PADDLE_LAYOUT_OCR_IR_OPTIM", default=False),
        "enable_mkldnn": env_bool("PADDLE_LAYOUT_OCR_ENABLE_MKLDNN", default=False),
    }
