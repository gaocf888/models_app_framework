"""确定性 python_fn 注册表。plan item 的 executor=python 时按 python_fn 调用。"""

from __future__ import annotations

from typing import Any, Callable

from app.analysis_agent.deterministics.fcb_compress import layer_compress, rank_by_area, slice_layer0
from app.analysis_agent.deterministics.key_areas import join_key_areas
from app.analysis_agent.deterministics.typical_curve import typical_curve as run_typical_curve

PythonFn = Callable[..., list[dict[str, Any]]]

PYTHON_FNS: dict[str, PythonFn] = {
    "slice_layer0": slice_layer0,
    "layer_compress": layer_compress,
    "join_key_areas": join_key_areas,
    "typical_curve": run_typical_curve,
    "rank_by_area": rank_by_area,
}


def run_python_fn(
    name: str,
    *,
    gathered: dict[str, list[dict[str, Any]]],
    resolved: dict[str, Any] | None = None,
    task: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    fn = PYTHON_FNS.get((name or "").strip())
    if fn is None:
        raise KeyError(f"unknown_python_fn:{name}")
    return list(fn(gathered, resolved=resolved or {}, task=task or {}) or [])
