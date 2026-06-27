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

            resp = httpx.post(url, json=payload, timeout=self._timeout)
            if resp.status_code >= 400:
                logger.warning(
                    "callback non-success: url=%s status=%s body=%s",
                    url,
                    resp.status_code,
                    (resp.text or "")[:500],
                )
        except Exception as exc:  # noqa: BLE001
            logger.warning("callback failed: url=%s err=%s", url, exc)

