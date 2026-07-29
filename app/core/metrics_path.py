"""HTTP 指标 path 标签归一化，降低 Prometheus 高基数风险。"""

from __future__ import annotations

import re
from typing import Any

from starlette.requests import Request

# UUID / 纯数字 / 长 hex 段折叠为占位符
_UUID_RE = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)
_NUMERIC_RE = re.compile(r"^\d+$")
_HEX_RE = re.compile(r"^[0-9a-fA-F]{16,}$")


def collapse_dynamic_path(path: str) -> str:
    """将路径中的动态段折叠为 ``{id}``（无路由模板时的兜底）。"""
    if not path:
        return "/"
    parts = path.split("/")
    out: list[str] = []
    for part in parts:
        if not part:
            out.append(part)
            continue
        if _UUID_RE.match(part) or _NUMERIC_RE.match(part) or _HEX_RE.match(part):
            out.append("{id}")
        else:
            out.append(part)
    collapsed = "/".join(out)
    return collapsed if collapsed.startswith("/") else f"/{collapsed}"


def metrics_path_label(request: Request) -> str:
    """
    优先使用 FastAPI/Starlette 路由模板（如 ``/rag/jobs/{job_id}``），
    否则对实际 URL path 做动态段折叠。
    """
    route: Any = request.scope.get("route")
    template = getattr(route, "path", None) if route is not None else None
    if isinstance(template, str) and template:
        return template
    return collapse_dynamic_path(request.url.path or "/")
