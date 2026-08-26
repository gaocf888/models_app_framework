from __future__ import annotations

from typing import Any

__all__ = ["AnalysisAgentGraphRunner"]


def __getattr__(name: str) -> Any:
    if name == "AnalysisAgentGraphRunner":
        from app.analysis_agent.graph.runner import AnalysisAgentGraphRunner

        return AnalysisAgentGraphRunner
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
