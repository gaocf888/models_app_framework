from __future__ import annotations

from typing import Any

__all__ = ["get_agent_slots", "registry_available"]


def __getattr__(name: str) -> Any:
    if name in ("get_agent_slots", "registry_available"):
        from app.analysis_agent.slots import registry as _registry

        return getattr(_registry, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
