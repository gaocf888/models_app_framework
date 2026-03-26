from __future__ import annotations

import time
from pathlib import Path
from typing import Any, Dict

from app.core.logging import get_logger
from app.small_models.algorithm_registry import SmallModelAlgorithmRegistry, merge_algorithm_config, resolve_path
from app.small_models.callback_client import CallbackClient
from app.small_models.evidence import ClipRecorder, EvidenceStore
from app.small_models.registry import SmallModelRegistry
from app.small_models.strategy.CallingStrategy import CallingStrategy
from app.small_models.strategy.base import SmallModelStrategy

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
        self._algo_registry = SmallModelAlgorithmRegistry()
        self._callback = CallbackClient()
        self._strategies: Dict[str, SmallModelStrategy] = {}
        self._last_trigger_ts: Dict[str, float] = {}
        self._clip = ClipRecorder()

    def _get_strategy(self, name: str) -> SmallModelStrategy:
        if name in self._strategies:
            return self._strategies[name]
        if name == "CallingStrategy":
            self._strategies[name] = CallingStrategy()
            return self._strategies[name]
        raise ValueError(f"unknown strategy: {name}")

    def infer(self, channel_id: str, model_name: str, frame_item: dict, *, api_overrides: dict | None = None) -> None:
        """
        推理入口（非占位）：按 algor_type 选择策略 → 推理 → 证据保存 → 回调。
        """
        meta = self._registry.get(model_name)
        algor_type = str(
            (api_overrides or {}).get("algor_type")
            or frame_item.get("algor_type")
            or ((meta.task_type if meta else None) or "")
        )
        base = self._algo_registry.get(algor_type)

        overrides = dict(api_overrides or {})
        if meta and meta.weights_path and overrides.get("weights_path") is None:
            overrides["weights_path"] = meta.weights_path

        cfg = merge_algorithm_config(
            base,
            {
                "algor_type": algor_type,
                "name": overrides.get("name"),
                "description": overrides.get("description"),
                "strategy": overrides.get("strategy"),
                "model_name": overrides.get("model_name"),
                "weights_path": overrides.get("weights_path"),
                "device": overrides.get("device"),
                "imgsz": overrides.get("imgsz"),
                "conf": overrides.get("conf"),
                "iou": overrides.get("iou"),
                "cooldown_seconds": overrides.get("cooldown_seconds"),
                "evidence_dir": overrides.get("evidence_dir"),
                "clip_seconds": overrides.get("clip_seconds"),
                "callback_url": overrides.get("callback_url"),
            },
        )
        if not cfg.strategy:
            raise ValueError(f"missing strategy for algor_type={algor_type}")

        frame = frame_item.get("frame")
        if frame is None:
            return

        # clip recorder: if active, keep writing regardless of trigger
        if self._clip.active:
            try:
                self._clip.write(frame)
            except Exception:
                pass

        strategy = self._get_strategy(cfg.strategy)
        result = strategy.infer(
            frame,
            config={
                "weights_path": cfg.weights_path,
                "device": cfg.device,
                "imgsz": cfg.imgsz,
                "conf": cfg.conf,
                "iou": cfg.iou,
            },
        )

        # 冷却控制（按 channel_id + algor_type）
        cooldown = int(cfg.cooldown_seconds or 0)
        key = f"{channel_id}:{algor_type}"
        now = time.time()
        if result.triggered and cooldown > 0:
            last = self._last_trigger_ts.get(key)
            if last is not None and (now - last) < cooldown:
                result = type(result)(triggered=False, detections=result.detections, extra=result.extra)

        if not result.triggered:
            return

        self._last_trigger_ts[key] = now

        evidence_dir = resolve_path(cfg.evidence_dir or "data/small_model_evidence") or "data/small_model_evidence"
        store = EvidenceStore(evidence_dir)
        image_path = None
        try:
            image_path = store.save_frame_jpg(frame, channel_id=channel_id, algor_type=algor_type)
        except Exception as exc:  # noqa: BLE001
            logger.warning("save frame failed: channel=%s algor=%s err=%s", channel_id, algor_type, exc)

        video_path = None
        clip_seconds = int(cfg.clip_seconds or 0)
        if clip_seconds > 0:
            try:
                out_dir = Path(evidence_dir) / str(channel_id) / str(algor_type)
                out_dir.mkdir(parents=True, exist_ok=True)
                ts = time.strftime("%Y%m%d_%H%M%S")
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
                {"label": d.label, "score": d.score, "bbox_xyxy": d.bbox_xyxy} for d in result.detections
            ],
            "evidence": {"image_path": image_path, "video_path": video_path},
            "extra": result.extra,
        }
        if cfg.callback_url:
            self._callback.post(cfg.callback_url, payload)

