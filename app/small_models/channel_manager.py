from __future__ import annotations

import threading
from dataclasses import dataclass, field
from queue import Queue
from typing import Dict, Optional


@dataclass
class ChannelConfig:
    """
    单个小模型通道的配置。

    - model_name: 小模型算法名称（如 yolo_v8、cls_xx 等）；
    - queue_size: 内部消息队列容量；
    - video_source: 视频流/文件地址（例如 rtsp/http/file 路径），用于解码线程拉流；
    - extra_params: 其他与算法相关的配置，可由上层以 JSON 形式传入。
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


class ChannelManager:
    """
    小模型通道管理器（V1 骨架版）。

    - 使用全局对象锁管理 channel_id -> ChannelContext 的映射；
    - 每个通道有独立的 channel_lock 管理其 start/stop/update；
    - 当前 decoder/inference 线程仅为占位循环，不接真实视频流与模型。
    """

    def __init__(self) -> None:
        self._objects_lock = threading.Lock()
        self._channels: Dict[str, ChannelContext] = {}

    def _create_context(self, channel_id: str, config: ChannelConfig) -> ChannelContext:
        q: Queue = Queue(maxsize=config.queue_size)
        stop_event = threading.Event()
        ctx = ChannelContext(
            channel_id=channel_id,
            config=config,
            message_queue=q,
            stop_event=stop_event,
        )
        return ctx

    def start_channel(self, channel_id: str, config: ChannelConfig) -> None:
        with self._objects_lock:
            if channel_id in self._channels:
                # 已存在则不重复启动
                return
            ctx = self._create_context(channel_id, config)
            self._channels[channel_id] = ctx

        # 在上下文外启动线程
        from app.small_models.workers import start_decoder_worker, start_inference_worker

        with ctx.channel_lock:
            ctx.decoder_thread = start_decoder_worker(ctx)
            ctx.inference_thread = start_inference_worker(ctx)

    def stop_channel(self, channel_id: str) -> None:
        with self._objects_lock:
            ctx = self._channels.get(channel_id)
        if not ctx:
            return

        # 设置停止事件
        ctx.stop_event.set()

        # 等待线程退出
        for th in (ctx.decoder_thread, ctx.inference_thread):
            if th and th.is_alive():
                th.join(timeout=2.0)

        # 从全局映射中移除
        with self._objects_lock:
            self._channels.pop(channel_id, None)

    def update_channel(self, channel_id: str, config: ChannelConfig) -> None:
        with self._objects_lock:
            ctx = self._channels.get(channel_id)
        if not ctx:
            # 若不存在，则等同于 start
            self.start_channel(channel_id, config)
            return

        with ctx.channel_lock:
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

