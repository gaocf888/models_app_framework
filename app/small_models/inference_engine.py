from __future__ import annotations

import time
from dataclasses import replace
from pathlib import Path
from typing import Any, Dict, List, Type

from app.core.logging import get_logger
from app.small_models.algorithm_registry import (
    AlgorithmConfig,
    SmallModelAlgorithmRegistry,
    merge_algorithm_config,
    resolve_path,
)
from app.small_models.callback_client import CallbackClient
from app.small_models.evidence import ClipRecorder, EvidenceStore
from app.small_models.registry import SmallModelRegistry
from app.small_models.strategy.base import SmallModelStrategy, StrategyResult
from app.small_models.strategy.calling_detection import (
    CallingDetectionStrategy,
    resolve_strategy_name_for_algor,
)
from app.small_models.strategy.complex_behavior_detection import ComplexBehaviorDetectionStrategy
from app.small_models.strategy.object_detection import ObjectDetectionStrategy
from app.small_models.strategy.face_recognition import FaceRecognitionStrategy
from app.small_models.strategy.regular_behavior_detection import RegularBehaviorDetectionStrategy

logger = get_logger(__name__)

# YAML 中的规范策略名 -> 实现类
_STRATEGY_CLASSES: Dict[str, Type[SmallModelStrategy]] = {
    "ObjectDetectionStrategy": ObjectDetectionStrategy,
    "RegularBehaviorDetectionStrategy": RegularBehaviorDetectionStrategy,
    "ComplexBehaviorDetectionStrategy": ComplexBehaviorDetectionStrategy,
    "FaceRecognitionStrategy": FaceRecognitionStrategy,
    "CallingDetectionStrategy": CallingDetectionStrategy,
}

# 历史/兼容别名 → 与规范名共用同一策略单例（避免重复加载模型）
_STRATEGY_ALIASES: Dict[str, str] = {
    # 旧 YAML / 历史名 → 接打电话专用策略（spatial + 可回落 end_to_end）
    "CallingStrategy": "CallingDetectionStrategy",
}


def _canonical_strategy_name(name: str) -> str:
    return _STRATEGY_ALIASES.get(name, name)


def _default_strategy_for_cfg(cfg: AlgorithmConfig) -> AlgorithmConfig:
    if cfg.strategy:
        return cfg
    resolved = resolve_strategy_name_for_algor(None, cfg.algor_type)
    if resolved:
        return replace(cfg, strategy=resolved)
    return replace(cfg, strategy="ObjectDetectionStrategy")


def _config_to_infer_dict(cfg: AlgorithmConfig) -> Dict[str, Any]:
    return {
        "weights_path": cfg.weights_path,
        "device": cfg.device,
        "imgsz": cfg.imgsz,
        "conf": cfg.conf,
        "iou": cfg.iou,
        "roi": cfg.roi,
        "class_filter": cfg.class_filter,
        "complex_mode": cfg.complex_mode,
        "dwell_seconds": cfg.dwell_seconds,
        "dwell_polygon": cfg.dwell_polygon,
        "line_cross_line": cfg.line_cross_line,
        "zone_intrusion_polygon": cfg.zone_intrusion_polygon,
        "gallery_id": cfg.gallery_id,
        "match_threshold": cfg.match_threshold,
        "face_model_pack": cfg.face_model_pack,
        "face_model_root": cfg.face_model_root,
        "face_gallery_dir": cfg.face_gallery_dir,
        "det_size": cfg.det_size,
        "min_face_size": cfg.min_face_size,
        "max_faces": cfg.max_faces,
        "unknown_alert": cfg.unknown_alert,
        "draw_boxes": cfg.draw_boxes,
        "face_alert_mode": cfg.face_alert_mode,
        "unknown_cooldown_seconds": cfg.unknown_cooldown_seconds,
        "calling_mode": cfg.calling_mode,
        "calling_person_class_id": cfg.calling_person_class_id,
        "calling_phone_class_id": cfg.calling_phone_class_id,
        "calling_upper_body_ratio": cfg.calling_upper_body_ratio,
        "calling_min_phone_conf": cfg.calling_min_phone_conf,
        "calling_fallback_end_to_end": cfg.calling_fallback_end_to_end,
        "calling_fallback_weights_path": cfg.calling_fallback_weights_path,
        "calling_fallback_class_filter": cfg.calling_fallback_class_filter,
    }


class SmallModelInferenceEngine:
    """合并算法配置 → 选择策略类 → infer → 证据与回调。"""

    def __init__(self) -> None:
        self._registry = SmallModelRegistry()
        self._algo_registry = SmallModelAlgorithmRegistry()
        self._callback = CallbackClient()
        self._strategy_singletons: Dict[str, SmallModelStrategy] = {}
        self._last_trigger_ts: Dict[str, float] = {}
        self._clip = ClipRecorder()

    def _get_strategy(self, name: str) -> SmallModelStrategy:
        canonical = _canonical_strategy_name(name)
        cls = _STRATEGY_CLASSES.get(canonical)
        if cls is None:
            known = ", ".join(sorted(_STRATEGY_CLASSES))
            aliases = ", ".join(f"{k}→{v}" for k, v in sorted(_STRATEGY_ALIASES.items()))
            raise ValueError(
                f"unknown strategy: {name!r}. Known classes: {known}. Aliases: {aliases}"
            )
        if canonical not in self._strategy_singletons:
            self._strategy_singletons[canonical] = cls()
        return self._strategy_singletons[canonical]

    def infer(self, channel_id: str, model_name: str, frame_item: dict, *, api_overrides: dict | None = None) -> None:
        meta = self._registry.get(model_name)
        algor_type = str(
            (api_overrides or {}).get("algor_type")
            or frame_item.get("algor_type")
            or ((meta.task_type if meta else None) or "")
        )
        base = self._algo_registry.get(algor_type)

        overrides = dict(api_overrides or {})
        overrides["algor_type"] = algor_type
        if meta and meta.weights_path and overrides.get("weights_path") is None:
            overrides["weights_path"] = meta.weights_path

        try:
            cfg = merge_algorithm_config(base, overrides)
        except ValueError as exc:
            logger.warning(
                "skip infer channel=%s: %s (set algor_type in channel config or small_model_algorithms.yaml)",
                channel_id,
                exc,
            )
            return
        cfg = _default_strategy_for_cfg(cfg)
        if not cfg.strategy:
            raise ValueError(f"missing strategy for algor_type={algor_type}")

        frame = frame_item.get("frame")
        if frame is None:
            return
        try:
            import numpy as np

            if not isinstance(frame, np.ndarray) or frame.ndim < 2:
                return
        except ImportError:
            if not hasattr(frame, "shape") or len(getattr(frame, "shape", ())) < 2:
                return

        if self._clip.active:
            try:
                self._clip.write(frame)
            except Exception:  # noqa: BLE001
                pass

        strategy = self._get_strategy(cfg.strategy)
        strategy_cfg = _config_to_infer_dict(cfg)
        ctx = {"channel_id": channel_id, "algor_type": algor_type}
        try:
            result = strategy.infer(frame, config=strategy_cfg, context=ctx)
        except Exception as exc:  # noqa: BLE001
            logger.exception("strategy infer failed channel=%s algor=%s: %s", channel_id, algor_type, exc)
            return

        result = self._apply_cooldown(channel_id, algor_type, cfg, result)

        if not result.triggered:
            return

        if result.extra.get("algorithm") != "face_recognition":
            self._last_trigger_ts[f"{channel_id}:{algor_type}"] = time.time()

        evidence_dir = resolve_path(cfg.evidence_dir or "data/small_model_evidence") or "data/small_model_evidence"
        store = EvidenceStore(evidence_dir)
        image_path = None
        evidence_frame = result.extra.get("annotated_frame") or frame
        try:
            image_path = store.save_frame_jpg(evidence_frame, channel_id=channel_id, algor_type=algor_type)
        except Exception as exc:  # noqa: BLE001
            logger.warning("save frame failed: channel=%s algor=%s err=%s", channel_id, algor_type, exc)

        video_path = None
        clip_seconds = int(cfg.clip_seconds or 0)
        if clip_seconds > 0:
            try:
                out_dir = Path(evidence_dir) / str(channel_id) / str(algor_type)
                out_dir.mkdir(parents=True, exist_ok=True)
                ts = time.strftime("%Y%m%d_%H%M%S")
                now = time.time()
                video_path = str((out_dir / f"{ts}_{int(now*1000)}.mp4").resolve())
                h, w = frame.shape[:2]
                self._clip.start(video_path=video_path, frame_size=(w, h), fps=15, seconds=clip_seconds)
                self._clip.write(frame)
            except Exception as exc:  # noqa: BLE001
                logger.warning("start clip failed: channel=%s algor=%s err=%s", channel_id, algor_type, exc)

        payload: Dict[str, Any] = {
            "channel_id": channel_id,
            "algor_type": algor_type,
            "model_name": cfg.model_name or model_name,
            "weights_path": resolve_path(cfg.weights_path),
            "detections": [
                {
                    "label": d.label,
                    "score": d.score,
                    "bbox_xyxy": d.bbox_xyxy,
                    **({"person_id": d.person_id} if d.person_id is not None else {}),
                }
                for d in result.detections
            ],
            "evidence": {"image_path": image_path, "video_path": video_path},
            "extra": result.extra,
        }
        if result.extra.get("algorithm") == "face_recognition":
            payload["alert_types"] = result.extra.get("alert_types") or []
            payload["face_alerts"] = result.extra.get("face_alerts") or []
            payload["face_alert_mode"] = result.extra.get("face_alert_mode")
        if result.extra.get("algorithm") == "calling_detection":
            payload["calling_mode"] = result.extra.get("calling_mode")
            payload["calling_fallback_used"] = bool(result.extra.get("calling_fallback_used"))
            payload["calling_match_count"] = result.extra.get("calling_match_count")
        if cfg.callback_url:
            self._callback.post(cfg.callback_url, payload)

    def _apply_cooldown(
        self, channel_id: str, algor_type: str, cfg: AlgorithmConfig, result: StrategyResult
    ) -> StrategyResult:
        if result.extra.get("algorithm") == "face_recognition":
            return self._apply_face_cooldown(channel_id, algor_type, cfg, result)

        cooldown = int(cfg.cooldown_seconds or 0)
        key = f"{channel_id}:{algor_type}"
        now = time.time()
        if result.triggered and cooldown > 0:
            last = self._last_trigger_ts.get(key)
            if last is not None and (now - last) < cooldown:
                return StrategyResult(triggered=False, detections=result.detections, extra=result.extra)
        return result

    def _apply_face_cooldown(
        self, channel_id: str, algor_type: str, cfg: AlgorithmConfig, result: StrategyResult
    ) -> StrategyResult:
        """人脸识别：白名单与陌生人分键冷却。"""
        if not result.triggered:
            return result

        alert_types: List[str] = list(result.extra.get("alert_types") or [])
        if not alert_types:
            return result

        now = time.time()
        allowed: List[str] = []
        for at in alert_types:
            key = f"{channel_id}:{algor_type}:{at}"
            if at == "unknown":
                cooldown = int(cfg.unknown_cooldown_seconds if cfg.unknown_cooldown_seconds is not None else cfg.cooldown_seconds or 0)
            else:
                cooldown = int(cfg.cooldown_seconds or 0)
            if cooldown > 0:
                last = self._last_trigger_ts.get(key)
                if last is not None and (now - last) < cooldown:
                    continue
            allowed.append(at)

        if not allowed:
            return StrategyResult(triggered=False, detections=result.detections, extra=result.extra)

        for at in allowed:
            self._last_trigger_ts[f"{channel_id}:{algor_type}:{at}"] = now

        extra = dict(result.extra)
        face_alerts = [a for a in (extra.get("face_alerts") or []) if a.get("match_type") in allowed]
        extra["alert_types"] = allowed
        extra["face_alerts"] = face_alerts
        return StrategyResult(triggered=True, detections=result.detections, extra=extra)
