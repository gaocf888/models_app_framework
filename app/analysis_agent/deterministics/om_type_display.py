"""运维仪器类型：库内 type → 报告模版展示名（静力水准仪等）。"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

from app.core.logging import get_logger

logger = get_logger(__name__)

_OM_TYPE_YAML = (
    Path(__file__).resolve().parents[3]
    / "configs"
    / "nl2sql_business"
    / "subsidence"
    / "semantic"
    / "dimensions"
    / "om_device_type.yaml"
)

_TYPE_KEYS = ("device_type", "type")


@lru_cache(maxsize=1)
def load_om_report_display_map() -> dict[str, str]:
    """om_type 原文 → report_display_name；无配置则回落原文。"""
    path = _OM_TYPE_YAML
    if not path.is_file():
        logger.warning("om_device_type.yaml missing path=%s", path)
        return {}
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except Exception:  # noqa: BLE001
        logger.warning("om_device_type.yaml load failed", exc_info=True)
        return {}
    out: dict[str, str] = {}
    for ent in data.get("entries") or []:
        if not isinstance(ent, dict):
            continue
        om_type = str(ent.get("om_type") or "").strip()
        if not om_type:
            continue
        display = str(ent.get("report_display_name") or "").strip() or om_type
        out[om_type] = display
        # 同义词也指向同一展示名，便于偶发同义入库
        for syn in ent.get("synonyms") or []:
            s = str(syn or "").strip()
            if s and s not in out:
                out[s] = display
    return out


def map_om_type_display(raw: Any, mapping: dict[str, str] | None = None) -> str:
    text = str(raw or "").strip()
    if not text:
        return text
    m = mapping if mapping is not None else load_om_report_display_map()
    return m.get(text, text)


def apply_om_type_display(
    rows: list[dict[str, Any]],
    *,
    keys: tuple[str, ...] = _TYPE_KEYS,
) -> list[dict[str, Any]]:
    """就地复制行，将仪器类型列替换为报告展示名。"""
    if not rows:
        return rows
    mapping = load_om_report_display_map()
    if not mapping:
        return rows
    key_set = {k for k in keys if k}
    out: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        new_row = dict(row)
        for key in key_set:
            if key not in new_row:
                continue
            new_row[key] = map_om_type_display(new_row.get(key), mapping)
        out.append(new_row)
    return out
