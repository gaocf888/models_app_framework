from __future__ import annotations

from app.models.small_model import (
    SmallModelChannelConfig,
    SmallModelChannelStatus,
    parse_small_model_roi,
    serialize_small_model_roi,
)
from app.small_models.channel_manager import ChannelConfig, ChannelManager


def _merge_channel_extra_params(cfg: SmallModelChannelConfig) -> dict:
    """合并顶层字段到 extra_params；校验 ROI。"""
    extra_params = dict(cfg.extra_params or {})
    if cfg.algor_type:
        extra_params["algor_type"] = cfg.algor_type
    if cfg.weights_path:
        extra_params["weights_path"] = cfg.weights_path
    if cfg.callback_url:
        extra_params["callback_url"] = cfg.callback_url
    if cfg.evidence_dir:
        extra_params["evidence_dir"] = cfg.evidence_dir
    if cfg.device:
        extra_params["device"] = cfg.device
    if cfg.imgsz is not None:
        extra_params["imgsz"] = cfg.imgsz
    if cfg.conf is not None:
        extra_params["conf"] = cfg.conf
    if cfg.iou is not None:
        extra_params["iou"] = cfg.iou
    if cfg.cooldown_seconds is not None:
        extra_params["cooldown_seconds"] = cfg.cooldown_seconds
    if cfg.clip_seconds is not None:
        extra_params["clip_seconds"] = cfg.clip_seconds
    if cfg.roi is not None:
        extra_params["roi"] = serialize_small_model_roi(cfg.roi)
    elif extra_params.get("roi") is not None:
        extra_params["roi"] = serialize_small_model_roi(parse_small_model_roi(extra_params["roi"]))
    if cfg.class_filter is not None:
        extra_params["class_filter"] = cfg.class_filter
    if cfg.complex_mode is not None:
        extra_params["complex_mode"] = cfg.complex_mode
    if cfg.dwell_seconds is not None:
        extra_params["dwell_seconds"] = cfg.dwell_seconds
    if cfg.dwell_polygon is not None:
        extra_params["dwell_polygon"] = cfg.dwell_polygon
    if cfg.line_cross_line is not None:
        extra_params["line_cross_line"] = cfg.line_cross_line
    if cfg.zone_intrusion_polygon is not None:
        extra_params["zone_intrusion_polygon"] = cfg.zone_intrusion_polygon
    if cfg.gallery_id is not None:
        extra_params["gallery_id"] = cfg.gallery_id
    if cfg.match_threshold is not None:
        extra_params["match_threshold"] = cfg.match_threshold
    if cfg.face_model_pack is not None:
        extra_params["face_model_pack"] = cfg.face_model_pack
    if cfg.face_model_root is not None:
        extra_params["face_model_root"] = cfg.face_model_root
    if cfg.face_gallery_dir is not None:
        extra_params["face_gallery_dir"] = cfg.face_gallery_dir
    if cfg.det_size is not None:
        extra_params["det_size"] = cfg.det_size
    if cfg.min_face_size is not None:
        extra_params["min_face_size"] = cfg.min_face_size
    if cfg.max_faces is not None:
        extra_params["max_faces"] = cfg.max_faces
    if cfg.unknown_alert is not None:
        extra_params["unknown_alert"] = cfg.unknown_alert
    if cfg.draw_boxes is not None:
        extra_params["draw_boxes"] = cfg.draw_boxes
    if cfg.face_alert_mode is not None:
        extra_params["face_alert_mode"] = cfg.face_alert_mode
    if cfg.unknown_cooldown_seconds is not None:
        extra_params["unknown_cooldown_seconds"] = cfg.unknown_cooldown_seconds
    if cfg.calling_mode is not None:
        extra_params["calling_mode"] = cfg.calling_mode
    if cfg.calling_person_class_id is not None:
        extra_params["calling_person_class_id"] = cfg.calling_person_class_id
    if cfg.calling_phone_class_id is not None:
        extra_params["calling_phone_class_id"] = cfg.calling_phone_class_id
    if cfg.calling_upper_body_ratio is not None:
        extra_params["calling_upper_body_ratio"] = cfg.calling_upper_body_ratio
    if cfg.calling_min_phone_conf is not None:
        extra_params["calling_min_phone_conf"] = cfg.calling_min_phone_conf
    if cfg.calling_fallback_end_to_end is not None:
        extra_params["calling_fallback_end_to_end"] = cfg.calling_fallback_end_to_end
    if cfg.calling_fallback_weights_path is not None:
        extra_params["calling_fallback_weights_path"] = cfg.calling_fallback_weights_path
    if cfg.calling_fallback_class_filter is not None:
        extra_params["calling_fallback_class_filter"] = cfg.calling_fallback_class_filter
    return extra_params


class SmallModelChannelService:
    """
    小模型通道管理服务。

    封装 ChannelManager，对外提供简化的 Pydantic 模型接口。
    """

    def __init__(self, manager: ChannelManager | None = None) -> None:
        self._manager = manager or ChannelManager()

    def start(self, cfg: SmallModelChannelConfig) -> None:
        extra_params = _merge_channel_extra_params(cfg)
        self._manager.start_channel(
            cfg.channel_id,
            ChannelConfig(
                model_name=cfg.model_name,
                queue_size=cfg.queue_size,
                video_source=cfg.video_source,
                extra_params=extra_params,
            ),
        )

    def stop(self, channel_id: str) -> None:
        self._manager.stop_channel(channel_id)

    def update(self, cfg: SmallModelChannelConfig) -> None:
        extra_params = _merge_channel_extra_params(cfg)
        self._manager.update_channel(
            cfg.channel_id,
            ChannelConfig(
                model_name=cfg.model_name,
                queue_size=cfg.queue_size,
                video_source=cfg.video_source,
                extra_params=extra_params,
            ),
        )

    def status(self, channel_id: str) -> SmallModelChannelStatus:
        raw = self._manager.get_status(channel_id)
        return SmallModelChannelStatus(
            exists=raw.get("exists", False),
            model_name=raw.get("model_name"),
            queue_size=raw.get("queue_size"),
            stopped=raw.get("stopped"),
        )
