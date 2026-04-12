from __future__ import annotations

import threading
from dataclasses import dataclass, field
from queue import Empty, Queue
from typing import Dict, Optional

from app.core.logging import get_logger

logger = get_logger(__name__)


@dataclass
class ChannelConfig:
    """
    单个小模型通道的配置。

    - model_name: 通道展示名/指标标签；实际推理由 extra_params.algor_type + small_model_algorithms.yaml 决定；
    - queue_size: 内部消息队列容量；
    - video_source: 视频流/文件地址；解码线程每轮读取本字段，可在不停通道的情况下切换源；
    - extra_params: 算法相关配置（含 algor_type、roi、阈值等），推理线程每帧合并进推理覆盖项。
    """

    model_name: str = "yolo-placeholder"
    queue_size: int = 64
    video_source: str | None = None
    extra_params: dict | None = None


@dataclass
class ChannelContext:
    channel_id: str
    config: ChannelConfig
    message_queue: Queue
    stop_event: threading.Event
    channel_lock: threading.Lock = field(default_factory=threading.Lock)
    decoder_thread: Optional[threading.Thread] = None
    inference_thread: Optional[threading.Thread] = None


def _drain_queue(q: Queue) -> None:
    while True:
        try:
            q.get_nowait()
        except Empty:
            break


class ChannelManager:
    """
    小模型通道管理器。

    - 全局对象锁管理 channel_id -> ChannelContext；
    - 每通道 channel_lock 保护 start/stop/update 与队列重建；
    - 重复 start 等价于 update（幂等 upsert）；
    - 仅当 queue_size 变化时重启解码/推理线程；video_source 由解码线程热切换。
    """

    def __init__(self, *, worker_join_timeout: float = 15.0) -> None:
        self._objects_lock = threading.Lock()
        self._channels: Dict[str, ChannelContext] = {}
        self._worker_join_timeout = worker_join_timeout

    def _create_context(self, channel_id: str, config: ChannelConfig) -> ChannelContext:
        q: Queue = Queue(maxsize=config.queue_size)
        stop_event = threading.Event()
        return ChannelContext(
            channel_id=channel_id,
            config=config,
            message_queue=q,
            stop_event=stop_event,
        )

    def start_channel(self, channel_id: str, config: ChannelConfig) -> None:
        with self._objects_lock:
            if channel_id in self._channels:
                existed = True
                ctx: ChannelContext | None = None
            else:
                existed = False
                ctx = self._create_context(channel_id, config)
                self._channels[channel_id] = ctx

        if existed:
            self.update_channel(channel_id, config)
            return

        assert ctx is not None
        from app.small_models.workers import start_decoder_worker, start_inference_worker

        with ctx.channel_lock:
            ctx.decoder_thread = start_decoder_worker(ctx)
            ctx.inference_thread = start_inference_worker(ctx)
        logger.info("small-model channel started: %s", channel_id)

    def stop_channel(self, channel_id: str) -> None:
        with self._objects_lock:
            ctx = self._channels.get(channel_id)
        if not ctx:
            return

        ctx.stop_event.set()

        for th in (ctx.decoder_thread, ctx.inference_thread):
            if th and th.is_alive():
                th.join(timeout=self._worker_join_timeout)
                if th.is_alive():
                    logger.warning(
                        "small-model worker did not exit in time: channel=%s thread=%s",
                        channel_id,
                        th.name,
                    )

        with self._objects_lock:
            self._channels.pop(channel_id, None)
        logger.info("small-model channel stopped: %s", channel_id)

    def update_channel(self, channel_id: str, config: ChannelConfig) -> None:
        with self._objects_lock:
            ctx = self._channels.get(channel_id)
        if not ctx:
            self.start_channel(channel_id, config)
            return

        with ctx.channel_lock:
            old = ctx.config
            if old.queue_size != config.queue_size:
                logger.info(
                    "small-model channel queue_size changed, restarting workers: %s %s -> %s",
                    channel_id,
                    old.queue_size,
                    config.queue_size,
                )
                ctx.stop_event.set()
                for th in (ctx.decoder_thread, ctx.inference_thread):
                    if th and th.is_alive():
                        th.join(timeout=self._worker_join_timeout)
                        if th.is_alive():
                            logger.warning(
                                "small-model worker join timeout on restart: channel=%s thread=%s",
                                channel_id,
                                th.name,
                            )
                _drain_queue(ctx.message_queue)
                ctx.stop_event = threading.Event()
                ctx.message_queue = Queue(maxsize=config.queue_size)
                ctx.config = config
                from app.small_models.workers import start_decoder_worker, start_inference_worker

                ctx.decoder_thread = start_decoder_worker(ctx)
                ctx.inference_thread = start_inference_worker(ctx)
            else:
                ctx.config = config

    def get_status(self, channel_id: str) -> dict:
        with self._objects_lock:
            ctx = self._channels.get(channel_id)
        if not ctx:
            return {"exists": False}

        return {
            "exists": True,
            "model_name": ctx.config.model_name,
            "queue_size": ctx.message_queue.qsize(),
            "stopped": ctx.stop_event.is_set(),
        }
