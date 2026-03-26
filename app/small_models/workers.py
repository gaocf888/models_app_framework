from __future__ import annotations

import threading
import time
from queue import Empty, Full

from app.core.logging import get_logger
from app.small_models.channel_manager import ChannelContext
from app.small_models.inference_engine import SmallModelInferenceEngine
from app.core.metrics import SMALL_MODEL_FRAMES_PROCESSED

logger = get_logger(__name__)


def _decoder_loop(ctx: ChannelContext) -> None:
    """
    解码线程占位循环。

    当前实现：
    - 从 ChannelContext.config.video_source 读取视频源配置（如 rtsp/http/file 路径）；
    - 示例环境下仍然只写入占位“帧”，用于打通通道架构；
    - 生产环境中应在此位置接入实际的视频解码逻辑（如 OpenCV/FFmpeg），完全基于配置 video_source 拉流。
    """
    logger.info("decoder started for channel %s", ctx.channel_id)
    src = ctx.config.video_source
    use_dummy = src is None

    cap = None
    if src:
        try:
            import cv2  # type: ignore[import-not-found]

            cap = cv2.VideoCapture(src)
            if not cap.isOpened():
                logger.warning("failed to open video source %s, fallback to dummy frames", src)
                use_dummy = True
        except Exception as exc:  # noqa: BLE001
            logger.warning("cv2 not available or error opening source %s: %s, fallback to dummy frames", src, exc)
            use_dummy = True

    try:
        while not ctx.stop_event.is_set():
            try:
                frame_payload = {
                    "video_source": src,
                    "algor_type": (ctx.config.extra_params or {}).get("algor_type"),
                }

                if not use_dummy and cap is not None:
                    ret, frame = cap.read()
                    if not ret:
                        logger.warning("end of stream or read error for source %s, fallback to dummy frames", src)
                        use_dummy = True
                    else:
                        frame_payload["frame"] = frame
                if use_dummy:
                    frame_payload["frame"] = f"dummy_frame_{time.time()}"

                # 方案 A：有界队列满时使用阻塞 put + timeout 形成背压，避免 full()+sleep 忙等。
                while not ctx.stop_event.is_set():
                    try:
                        ctx.message_queue.put(frame_payload, timeout=0.1)
                        break
                    except Full:
                        continue
            except Exception as exc:
                logger.exception("decoder error on channel %s: %s", ctx.channel_id, exc)
                break
            time.sleep(0.02)
    finally:
        try:
            if cap is not None:
                cap.release()
        except Exception:  # noqa: BLE001
            pass
    logger.info("decoder stopped for channel %s", ctx.channel_id)


def _inference_loop(ctx: ChannelContext, engine: SmallModelInferenceEngine) -> None:
    """
    推理线程占位循环。

    当前仅从队列读取“帧”并调用占位推理函数。
    """
    logger.info("inference worker started for channel %s", ctx.channel_id)
    while not ctx.stop_event.is_set():
        try:
            item = ctx.message_queue.get(timeout=0.2)
        except Empty:
            continue
        try:
            api_overrides = {
                "algor_type": (ctx.config.extra_params or {}).get("algor_type"),
                "weights_path": (ctx.config.extra_params or {}).get("weights_path"),
                "callback_url": (ctx.config.extra_params or {}).get("callback_url"),
                "evidence_dir": (ctx.config.extra_params or {}).get("evidence_dir"),
                "device": (ctx.config.extra_params or {}).get("device"),
                "imgsz": (ctx.config.extra_params or {}).get("imgsz"),
                "conf": (ctx.config.extra_params or {}).get("conf"),
                "iou": (ctx.config.extra_params or {}).get("iou"),
                "cooldown_seconds": (ctx.config.extra_params or {}).get("cooldown_seconds"),
                "clip_seconds": (ctx.config.extra_params or {}).get("clip_seconds"),
            }
            engine.infer(ctx.channel_id, ctx.config.model_name, item, api_overrides=api_overrides)
            SMALL_MODEL_FRAMES_PROCESSED.labels(model_name=ctx.config.model_name).inc()
        except Exception as exc:
            logger.exception("inference error on channel %s: %s", ctx.channel_id, exc)
    logger.info("inference worker stopped for channel %s", ctx.channel_id)


def start_decoder_worker(ctx: ChannelContext) -> threading.Thread:
    th = threading.Thread(target=_decoder_loop, args=(ctx,), daemon=True)
    th.start()
    return th


def start_inference_worker(ctx: ChannelContext) -> threading.Thread:
    engine = SmallModelInferenceEngine()
    th = threading.Thread(target=_inference_loop, args=(ctx, engine,), daemon=True)
    th.start()
    return th

