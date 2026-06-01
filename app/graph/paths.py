from __future__ import annotations

from pathlib import Path


def project_root() -> Path:
    """仓库根目录（app/graph/paths.py → app → root）。"""
    return Path(__file__).resolve().parents[2]


def resolve_project_path(path: str | Path) -> Path:
    p = Path(path)
    if p.is_absolute():
        return p
    return project_root() / p
