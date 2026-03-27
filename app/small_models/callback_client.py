from __future__ import annotations

from typing import Any, Dict, Optional

from app.core.logging import get_logger

logger = get_logger(__name__)


class CallbackClient:
    def __init__(self, timeout_seconds: float = 2.0) -> None:
        self._timeout = timeout_seconds

    def post(self, url: str, payload: Dict[str, Any]) -> None:
        if not url:
            return
        try:
            import httpx

            httpx.post(url, json=payload, timeout=self._timeout)
        except Exception as exc:  # noqa: BLE001
            logger.warning("callback failed: url=%s err=%s", url, exc)

