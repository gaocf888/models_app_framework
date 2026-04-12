from __future__ import annotations

import threading
import time
from queue import Empty, Full

from app.core.logging import get_logger
from app.core.metrics import SMALL_MODEL_FRAMES_PROCESSED
from app.small_models.channel_manager import ChannelContext
from app.small_models.inference_engine import SmallModelInferenceEngine

logger = get_logger(__name__)

# 无有效视频源时避免刷屏（每通道最多每 60s 一条 WARNING）
_last_idle_log_ts: dict[str, float] = {}


def _log_decoder_idle(channel_id: str, reason: str) -> None:
    now = time.time()
    last = _last_idle_log_ts.get(channel_id, 0.0)
    if now - last >= 60.0:
        _last_idle_log_ts[channel_id] = now
        logger.warning("decoder idle channel=%s: %s", channel_id, reason)


def _decoder_loop(ctx: ChannelContext) -> None:
    """
    解码线程：按 ctx.config.video_source 拉流（OpenCV），支持运行中切换地址与断流重连。

    仅将 **BGR numpy 帧**（OpenCV 读入）放入队列；无有效源时不入队占位字符串，避免推理线程空转解析垃圾数据。
    """
    threading.current_thread().name = f"sm-decoder-{ctx.channel_id}"
    logger.info("decoder started for channel %s", ctx.channel_id)

    cap = None
    bound_src: object | str | None = object()  # sentinel != any real src
    read_fail_streak = 0

    try:
        while not ctx.stop_event.is_set():
            src = ctx.config.video_source

            if src != bound_src:
                bound_src = src
                read_fail_streak = 0
                if cap is not None:
                    try:
                        cap.release()
                    except Exception:  # noqa: BLE001
                        pass
                    cap = None

            use_dummy = src is None or str(src).strip() == ""
            if not use_dummy and cap is None:
                try:
                    import cv2  # type: ignore[import-not-found]

                    cap = cv2.VideoCapture(src)
                    if not cap.isOpened():
                        logger.warning("failed to open video source %s", src)
                        use_dummy = True
                except Exception as exc:  # noqa: BLE001
                    logger.warning("cv2 not available or error opening source %s: %s", src, exc)
                    use_dummy = True

            if use_dummy:
                _log_decoder_idle(
                    ctx.channel_id,
                    "no video_source or capture failed; not enqueueing frames",
                )
                time.sleep(0.5)
                continue

            try:
                frame_payload: dict = {
                    "video_source": src,
                    "algor_type": (ctx.config.extra_params or {}).get("algor_type"),
                }

                assert cap is not None
                ret, frame = cap.read()
                if not ret:
                    read_fail_streak += 1
                    logger.warning(
                        "decode read failed (streak=%d) for source=%s",
                        read_fail_streak,
                        src,
                    )
                    if read_fail_streak >= 3 and src:
                        try:
                            cap.release()
                        except Exception:  # noqa: BLE001
                            pass
                        cap = None
                        time.sleep(min(0.5 * read_fail_streak, 5.0))
                        try:
                            import cv2  # type: ignore[import-not-found]

                            cap = cv2.VideoCapture(src)
                            if not cap.isOpened():
                                logger.error("reconnect failed for %s, decoder will idle", src)
                                bound_src = object()  # force re-open path on next iteration
                                read_fail_streak = 0
                                time.sleep(1.0)
                                continue
                            read_fail_streak = 0
                        except Exception as exc:  # noqa: BLE001
                            logger.error("reconnect error for %s: %s", src, exc)
                            bound_src = object()
                            read_fail_streak = 0
                            time.sleep(1.0)
                            continue
                    else:
                        time.sleep(0.02)
                    continue

                read_fail_streak = 0
                frame_payload["frame"] = frame

                while not ctx.stop_event.is_set():
                    try:
                        ctx.message_queue.put(frame_payload, timeout=0.1)
                        break
                    except Full:
                        continue
            except Exception as exc:  # noqa: BLE001
                logger.exception("decoder error on channel %s: %s", ctx.channel_id, exc)
                time.sleep(0.5)
            time.sleep(0.02)
    finally:
        try:
            if cap is not None:
                cap.release()
        except Exception:  # noqa: BLE001
            pass
    logger.info("decoder stopped for channel %s", ctx.channel_id)


def _inference_loop(ctx: ChannelContext, engine: SmallModelInferenceEngine) -> None:
    """推理线程：有界队列取帧，整包 extra_params 作为算法覆盖项传入引擎。"""
    threading.current_thread().name = f"sm-infer-{ctx.channel_id}"
    logger.info("inference worker started for channel %s", ctx.channel_id)
    while not ctx.stop_event.is_set():
        try:
            item = ctx.message_queue.get(timeout=0.2)
        except Empty:
            continue
        try:
            api_overrides = dict(ctx.config.extra_params or {})
            engine.infer(ctx.channel_id, ctx.config.model_name, item, api_overrides=api_overrides)
            SMALL_MODEL_FRAMES_PROCESSED.labels(model_name=ctx.config.model_name).inc()
        except Exception as exc:  # noqa: BLE001
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
