"""
复杂行为检测 — 企业级分层中的 L3（时空规则 + YOLO）。

典型业务：滞留、绊线、禁区；离岗/睡岗/跌倒等可优先用专用自训权重走 L1/L2，
必要时再叠加本类的 complex_mode。

默认仍以 YOLOv8 检出为基础（自训权重在 YAML 配置）；
对需要「跨帧几何/时长」的场景，通过 complex_mode 启用内置简化规则（无额外子包）：

- none：仅 YOLO + class_filter + roi（与常规检测相同）
- dwell：滞留 — dwell_polygon + dwell_seconds，框中心在区内连续超过阈值触发
- line_cross：绊线 — line_cross_line 两点，轨迹与线段相交触发
- zone_intrusion：禁区 — zone_intrusion_polygon，框中心进入多边形触发

若某类行为必须独立逻辑（如纯姿态/多模型融合），可另增 strategy 文件并在 YAML 中指定新策略类名。
"""

from __future__ import annotations

import threading
import time
from typing import Any, Dict, List, Tuple

from app.small_models.strategy._yolo_utils import bbox_center_xyxy, point_in_polygon, segments_cross
from app.small_models.strategy.base import Detection, SmallModelStrategy, StrategyResult
from app.small_models.strategy.object_detection import run_yolo_detection_pipeline

_state_lock = threading.Lock()
# 键：f"{channel_id}:{algor_type}:dwell|line|..."
_dwell_first_seen: Dict[str, Dict[str, float]] = {}
_line_prev_center: Dict[str, Dict[str, Tuple[float, float]]] = {}


def _slot_key(det: Detection) -> str:
    if det.track_id is not None:
        return f"t{det.track_id}"
    bb = det.bbox_xyxy
    if bb:
        cx, cy = bbox_center_xyxy(bb)
        return f"g{int(cx) // 30}_{int(cy) // 30}"
    return "x"


def _apply_dwell(
    dets: List[Detection],
    *,
    polygon: List[List[float]],
    seconds: float,
    scope: str,
) -> Tuple[List[Detection], bool]:
    poly = [(float(p[0]), float(p[1])) for p in polygon]
    if len(poly) < 3:
        return [], False

    key = f"{scope}:dwell"
    now = time.time()
    in_zone: List[Detection] = []
    for d in dets:
        bb = d.bbox_xyxy
        if not bb:
            continue
        cx, cy = bbox_center_xyxy(bb)
        if point_in_polygon(cx, cy, poly):
            in_zone.append(d)

    slots = {_slot_key(d) for d in in_zone}
    with _state_lock:
        fs = dict(_dwell_first_seen.get(key, {}))
        for sk in list(fs.keys()):
            if sk not in slots:
                del fs[sk]
        fired = False
        out: List[Detection] = []
        for d in in_zone:
            sk = _slot_key(d)
            if sk not in fs:
                fs[sk] = now
            if now - fs[sk] >= seconds:
                fired = True
                out.append(d)
        _dwell_first_seen[key] = fs

    # 未达滞留时长不告警、不回传框，避免与常规检测混淆
    if fired:
        return out, True
    return [], False


def _apply_line_cross(
    dets: List[Detection],
    *,
    line: List[List[float]],
    scope: str,
) -> Tuple[List[Detection], bool]:
    if len(line) != 2:
        return [], False
    p1 = (float(line[0][0]), float(line[0][1]))
    p2 = (float(line[1][0]), float(line[1][1]))
    key = f"{scope}:line"
    crossing: List[Detection] = []
    new_prev: Dict[str, Tuple[float, float]] = {}
    with _state_lock:
        prev_map = dict(_line_prev_center.get(key, {}))
    for d in dets:
        bb = d.bbox_xyxy
        if not bb:
            continue
        cx, cy = bbox_center_xyxy(bb)
        sk = _slot_key(d)
        new_prev[sk] = (cx, cy)
        old = prev_map.get(sk)
        if old is not None and segments_cross(old, (cx, cy), p1, p2):
            crossing.append(d)
    with _state_lock:
        _line_prev_center[key] = new_prev
    fired = len(crossing) > 0
    if fired:
        return crossing, True
    return [], False


def _apply_zone_intrusion(
    dets: List[Detection],
    *,
    polygon: List[List[float]],
) -> Tuple[List[Detection], bool]:
    poly = [(float(p[0]), float(p[1])) for p in polygon]
    if len(poly) < 3:
        return [], False
    inside: List[Detection] = []
    for d in dets:
        bb = d.bbox_xyxy
        if not bb:
            continue
        cx, cy = bbox_center_xyxy(bb)
        if point_in_polygon(cx, cy, poly):
            inside.append(d)
    fired = len(inside) > 0
    if fired:
        return inside, True
    return [], False


class ComplexBehaviorDetectionStrategy(SmallModelStrategy):
    """复杂行为：YOLO + 可选 dwell / 绊线 / 禁区。"""

    def infer(
        self,
        frame_bgr: Any,
        *,
        config: Dict[str, Any],
        context: Dict[str, Any] | None = None,
    ) -> StrategyResult:
        ctx = context or {}
        scope = f"{ctx.get('channel_id', '')}:{ctx.get('algor_type', '')}"

        dets = run_yolo_detection_pipeline(frame_bgr, config)
        mode = (config.get("complex_mode") or "none").lower().strip()
        triggered = len(dets) > 0
        extra: Dict[str, Any] = {
            "algorithm": "complex_behavior_detection",
            "complex_mode": mode,
            "yolo": "ultralytics",
        }

        if mode == "dwell":
            poly = config.get("dwell_polygon") or []
            sec = float(config.get("dwell_seconds") or 5.0)
            sec = max(0.05, sec)
            dets, triggered = _apply_dwell(dets, polygon=poly, seconds=sec, scope=scope)
        elif mode == "line_cross":
            line = config.get("line_cross_line") or []
            dets, triggered = _apply_line_cross(dets, line=line, scope=scope)
        elif mode == "zone_intrusion":
            poly = config.get("zone_intrusion_polygon") or []
            dets, triggered = _apply_zone_intrusion(dets, polygon=poly)
        else:
            triggered = len(dets) > 0

        return StrategyResult(triggered=triggered, detections=dets, extra=extra)
