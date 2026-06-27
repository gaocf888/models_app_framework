"""
接打电话专用策略 — 独立算法实现，内部可回落 L2 端到端检测。

calling_mode:
  - spatial（推荐/默认）：COCO 或通用权重检 person + cell phone，再做人-机空间约束
  - end_to_end：自训 call.pt 等单类行为头，等价于 RegularBehaviorDetectionStrategy 管线

高可用：
  - spatial 无命中且 calling_fallback_end_to_end=true 时，尝试 fallback 权重端到端检测
  - 单帧异常由引擎捕获，本策略尽量不抛未处理异常
"""

from __future__ import annotations

from typing import Any, Dict, List

from app.core.logging import get_logger
from app.small_models.algorithm_registry import resolve_path
from app.small_models.common.roi import filter_detections_by_roi
from app.small_models.strategy.base._spatial_rules import match_calling_by_phone_spatial
from app.small_models.strategy.base._yolo_utils import get_yolo_model, predict_detections
from app.small_models.strategy.base.base import Detection, SmallModelStrategy, StrategyResult
from app.small_models.strategy.l1.object_detection import run_yolo_detection_pipeline

logger = get_logger(__name__)

# 历史接打电话 algor_type；未显式配 strategy 时可默认走 CallingDetectionStrategy
CALLING_ALGOR_TYPE_DEFAULTS = frozenset({"40417", "41101", "40422"})


def resolve_calling_mode(config: Dict[str, Any]) -> str:
    raw = (config.get("calling_mode") or "spatial").lower().strip()
    if raw in ("spatial", "end_to_end", "e2e"):
        return "end_to_end" if raw in ("end_to_end", "e2e") else "spatial"
    return "spatial"


class CallingDetectionStrategy(SmallModelStrategy):
    """接打电话：spatial 人+手机空间规则；可回落 end_to_end 自训头。"""

    def infer(
        self,
        frame_bgr: Any,
        *,
        config: Dict[str, Any],
        context: Dict[str, Any] | None = None,
    ) -> StrategyResult:
        ctx = context or {}
        mode = resolve_calling_mode(config)
        try:
            if mode == "end_to_end":
                return self._infer_end_to_end(frame_bgr, config, ctx, via="primary")
            result = self._infer_spatial(frame_bgr, config, ctx)
            if result.triggered:
                return result
            if bool(config.get("calling_fallback_end_to_end", True)):
                fb_path = config.get("calling_fallback_weights_path") or config.get("weights_path")
                if fb_path and resolve_path(str(fb_path)):
                    fb_config = dict(config)
                    fb_config["weights_path"] = fb_path
                    fb_config.setdefault(
                        "class_filter",
                        config.get("calling_fallback_class_filter") or config.get("class_filter"),
                    )
                    fb = self._infer_end_to_end(frame_bgr, fb_config, ctx, via="fallback")
                    if fb.triggered:
                        extra = dict(fb.extra)
                        extra["calling_fallback_used"] = True
                        return StrategyResult(triggered=True, detections=fb.detections, extra=extra)
            return result
        except Exception as exc:  # noqa: BLE001
            logger.exception(
                "calling infer failed channel=%s algor=%s mode=%s: %s",
                ctx.get("channel_id"),
                ctx.get("algor_type"),
                mode,
                exc,
            )
            return StrategyResult(
                triggered=False,
                detections=[],
                extra={"algorithm": "calling_detection", "error": str(exc)},
            )

    def _infer_end_to_end(
        self,
        frame_bgr: Any,
        config: Dict[str, Any],
        ctx: Dict[str, Any],
        *,
        via: str,
    ) -> StrategyResult:
        dets = run_yolo_detection_pipeline(frame_bgr, config)
        return StrategyResult(
            triggered=len(dets) > 0,
            detections=dets,
            extra={
                "algorithm": "calling_detection",
                "calling_mode": "end_to_end",
                "calling_via": via,
                "yolo": "ultralytics",
            },
        )

    def _infer_spatial(
        self,
        frame_bgr: Any,
        config: Dict[str, Any],
        ctx: Dict[str, Any],
    ) -> StrategyResult:
        wp = resolve_path(str(config.get("weights_path") or ""))
        if not wp:
            raise ValueError("weights_path is required for calling spatial mode")

        person_cid = int(config.get("calling_person_class_id") or 0)
        phone_cid = int(config.get("calling_phone_class_id") or 67)
        upper_ratio = float(config.get("calling_upper_body_ratio") or 0.45)
        min_phone_conf = float(config.get("calling_min_phone_conf") or 0.5)

        model = get_yolo_model(wp)
        device = config.get("device")
        imgsz = int(config.get("imgsz") or 640)
        conf = float(config.get("conf") or 0.25)
        iou = float(config.get("iou") or 0.7)

        dets = predict_detections(
            model,
            frame_bgr,
            device=device,
            imgsz=imgsz,
            conf=conf,
            iou=iou,
            classes=[person_cid, phone_cid],
        )
        roi_cfg = config.get("roi")
        if roi_cfg:
            dets = filter_detections_by_roi(dets, roi_cfg, frame_bgr.shape)

        persons = [d for d in dets if d.class_id == person_cid]
        phones = [d for d in dets if d.class_id == phone_cid]

        spatial_matches = match_calling_by_phone_spatial(
            persons,
            phones,
            upper_body_ratio=upper_ratio,
            min_phone_conf=min_phone_conf,
        )

        out_dets: List[Detection] = []
        for m in spatial_matches:
            bb = m.person.bbox_xyxy
            out_dets.append(
                Detection(
                    label="calling",
                    score=float(m.match_score),
                    bbox_xyxy=bb,
                    class_id=person_cid,
                )
            )

        return StrategyResult(
            triggered=len(out_dets) > 0,
            detections=out_dets,
            extra={
                "algorithm": "calling_detection",
                "calling_mode": "spatial",
                "person_count": len(persons),
                "phone_count": len(phones),
                "calling_match_count": len(spatial_matches),
                "calling_upper_body_ratio": upper_ratio,
                "calling_min_phone_conf": min_phone_conf,
                "yolo": "ultralytics",
            },
        )


def resolve_strategy_name_for_algor(cfg_strategy: str | None, algor_type: str | None) -> str | None:
    """
    策略名解析：显式 strategy 优先；接打电话类 algor_type 默认 CallingDetectionStrategy。
    供引擎在 YAML 未写 strategy 时的兜底（独立配置仍优先）。
    """
    if cfg_strategy:
        return cfg_strategy
    if algor_type and str(algor_type) in CALLING_ALGOR_TYPE_DEFAULTS:
        return "CallingDetectionStrategy"
    return None
