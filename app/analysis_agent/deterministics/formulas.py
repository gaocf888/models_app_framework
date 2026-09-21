"""分层标周期沉降 / 压缩层公式内核（无 IO）。

与 P6 及 `季度报告分层标处理.py` 对齐：
- Δ(标) = settle_start − settle_end（窗内非空最早/最晚；须对应两个不同 data_time）
- 仅 monitor_layer ∈ {0,1,2,3,4}
- 仅相邻层 next − curr == 1
- compress(i→i+1) = Δ(i) − Δ(i+1)
- 缺层不成对（不要补 0）
"""

from __future__ import annotations

from typing import Any, Mapping

VALID_LAYERS = frozenset({0, 1, 2, 3, 4})


def period_delta(settle_start: Any, settle_end: Any) -> float:
    """窗内周期沉降 Δ；>0 下沉倾向，<0 回弹。"""
    return float(settle_start) - float(settle_end)


def compress_mm(delta_from: float, delta_to: float) -> float:
    """连续层间压缩量。"""
    return float(delta_from) - float(delta_to)


def consecutive_layer_pairs(
    layer_deltas: Mapping[int, float],
) -> list[tuple[int, int, float, float, float]]:
    """
    按层位排序后只产出连续对。

    返回 (layer_from, layer_to, delta_from, delta_to, compress_mm)。
    缺层（如 0,1,3）只出 0→1，不出 1→3。
    """
    layers = sorted(int(k) for k in layer_deltas.keys() if int(k) in VALID_LAYERS)
    out: list[tuple[int, int, float, float, float]] = []
    for i in range(len(layers) - 1):
        curr, nxt = layers[i], layers[i + 1]
        if nxt - curr != 1:
            continue
        d0 = float(layer_deltas[curr])
        d1 = float(layer_deltas[nxt])
        out.append((curr, nxt, d0, d1, compress_mm(d0, d1)))
    return out
