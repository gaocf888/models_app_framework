from __future__ import annotations

import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional


@dataclass
class EvidenceItem:
    image_path: str | None = None
    video_path: str | None = None


class EvidenceStore:
    def __init__(self, base_dir: str) -> None:
        self._base_dir = Path(base_dir)
        self._base_dir.mkdir(parents=True, exist_ok=True)

    def _ensure_dir(self, channel_id: str, algor_type: str) -> Path:
        d = self._base_dir / str(channel_id) / str(algor_type)
        d.mkdir(parents=True, exist_ok=True)
        return d

    def save_frame_jpg(self, frame_bgr: Any, *, channel_id: str, algor_type: str) -> str:
        # frame_bgr: np.ndarray
        import cv2  # type: ignore[import-not-found]

        out_dir = self._ensure_dir(channel_id, algor_type)
        ts = time.strftime("%Y%m%d_%H%M%S")
        out_path = out_dir / f"{ts}_{int(time.time() * 1000)}.jpg"
        ok = cv2.imwrite(str(out_path), frame_bgr)
        if not ok:
            raise OSError(f"cv2.imwrite failed: {out_path}")
        return str(out_path)


class ClipRecorder:
    """
    触发后保存后续 N 秒视频（简化版：仅 post-roll）。
    """

    def __init__(self) -> None:
        self._writer = None
        self._remaining_frames = 0
        self._video_path: str | None = None
        self._fps: int = 15

    @property
    def active(self) -> bool:
        return self._writer is not None and self._remaining_frames > 0

    @property
    def video_path(self) -> str | None:
        return self._video_path

    def start(self, *, video_path: str, frame_size: tuple[int, int], fps: int, seconds: int) -> None:
        import cv2  # type: ignore[import-not-found]

        self.close()
        self._fps = max(1, int(fps))
        self._remaining_frames = max(1, int(seconds) * self._fps)
        self._video_path = video_path
        fourcc = cv2.VideoWriter_fourcc(*"mp4v")
        w, h = frame_size
        self._writer = cv2.VideoWriter(video_path, fourcc, float(self._fps), (w, h))
        if not self._writer.isOpened():
            self._writer = None
            self._remaining_frames = 0
            self._video_path = None
            raise OSError(f"VideoWriter failed to open: {video_path}")

    def write(self, frame_bgr: Any) -> None:
        if not self.active:
            return
        self._writer.write(frame_bgr)
        self._remaining_frames -= 1
        if self._remaining_frames <= 0:
            self.close()

    def close(self) -> None:
        if self._writer is not None:
            try:
                self._writer.release()
            except Exception:
                pass
        self._writer = None
        self._remaining_frames = 0

