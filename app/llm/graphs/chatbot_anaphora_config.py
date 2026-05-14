"""
加载 `configs/chatbot_anaphora.yaml`（§4.5）。

校验：文件内 `types[].code` 必须与 §3.2 封闭枚举全集一致，否则启动失败（fail-fast）。
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, List

import yaml

from app.core.logging import get_logger
from app.llm.graphs.chatbot_anaphora_types import ANAPHORA_TYPE_CODES, AnaphoraType

logger = get_logger(__name__)

_DEFAULT_REL = Path("configs") / "chatbot_anaphora.yaml"


@dataclass(frozen=True)
class AnaphoraTypeRow:
    code: str
    display_name: str
    keywords: tuple[str, ...]
    regex: tuple[str, ...]
    p0_retrieval_fusion: bool
    p1_anchor_block: bool


@dataclass(frozen=True)
class AnaphoraP3Config:
    tau: float
    delta: float
    disambiguation_types: frozenset[str]
    coref_cache_ttl_sec: int
    assistant_tail_hash_chars: int
    query_normalize_lowercase: bool


@dataclass(frozen=True)
class AnaphoraRuntimeConfig:
    schema_version: int
    thresholds: Dict[str, int]
    types: Dict[str, AnaphoraTypeRow]
    p3: AnaphoraP3Config


def _project_configs_dir() -> Path:
    return Path(__file__).resolve().parents[3] / "configs"


def _default_yaml_path() -> Path:
    return _project_configs_dir() / "chatbot_anaphora.yaml"


def _parse_types(raw: Any) -> Dict[str, AnaphoraTypeRow]:
    out: Dict[str, AnaphoraTypeRow] = {}
    for item in raw or []:
        if not isinstance(item, dict):
            continue
        code = str(item.get("code", "") or "").strip()
        if not code:
            continue
        out[code] = AnaphoraTypeRow(
            code=code,
            display_name=str(item.get("display_name", "") or code),
            keywords=tuple(str(x).strip() for x in (item.get("keywords") or []) if str(x).strip()),
            regex=tuple(str(x).strip() for x in (item.get("regex") or []) if str(x).strip()),
            p0_retrieval_fusion=bool(item.get("p0_retrieval_fusion", False)),
            p1_anchor_block=bool(item.get("p1_anchor_block", False)),
        )
    return out


def _parse_p3(raw: Any) -> AnaphoraP3Config:
    d = raw if isinstance(raw, dict) else {}
    dis = d.get("disambiguation_types") or []
    return AnaphoraP3Config(
        tau=max(0.0, min(1.0, float(d.get("tau", 0.72)))),
        delta=max(0.0, min(1.0, float(d.get("delta", 0.08)))),
        disambiguation_types=frozenset(str(x).strip() for x in dis if str(x).strip()),
        coref_cache_ttl_sec=max(10, int(d.get("coref_cache_ttl_sec", 120))),
        assistant_tail_hash_chars=max(200, int(d.get("assistant_tail_hash_chars", 800))),
        query_normalize_lowercase=bool(d.get("query_normalize_lowercase", False)),
    )


def load_anaphora_config_from_path(path: Path) -> AnaphoraRuntimeConfig:
    if not path.exists():
        raise FileNotFoundError(f"chatbot anaphora config not found: {path}")
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    types = _parse_types(data.get("types"))
    missing = ANAPHORA_TYPE_CODES - set(types.keys())
    extra = set(types.keys()) - ANAPHORA_TYPE_CODES
    if missing:
        raise ValueError(f"chatbot_anaphora.yaml missing type rows for codes: {sorted(missing)}")
    if extra:
        raise ValueError(f"chatbot_anaphora.yaml has unknown codes (not in §3.2): {sorted(extra)}")
    th = data.get("thresholds") if isinstance(data.get("thresholds"), dict) else {}
    thresholds = {
        "single_entity_max_chars": max(10, int(th.get("single_entity_max_chars", 40))),
        "ellipsis_max_chars": max(8, int(th.get("ellipsis_max_chars", 16))),
        "meta_confirm_max_chars": max(8, int(th.get("meta_confirm_max_chars", 36))),
    }
    return AnaphoraRuntimeConfig(
        schema_version=int(data.get("schema_version", 1)),
        thresholds=thresholds,
        types=types,
        p3=_parse_p3(data.get("p3")),
    )


@lru_cache(maxsize=2)
def get_anaphora_runtime_config(config_path: str | None) -> AnaphoraRuntimeConfig:
    """
    :param config_path: 绝对路径或相对 cwd 的路径；None 时使用 CHATBOT_ANAPHORA_CONFIG_PATH 或默认 configs/chatbot_anaphora.yaml。
    """
    env_path = (os.getenv("CHATBOT_ANAPHORA_CONFIG_PATH") or "").strip()
    chosen = (config_path or env_path or str(_default_yaml_path())).strip()
    path = Path(chosen)
    if not path.is_absolute():
        path = Path.cwd() / path
    if not path.exists():
        fallback = _default_yaml_path()
        if path.resolve() != fallback.resolve() and fallback.exists():
            logger.warning("anaphora config path missing, fallback to bundled default: %s -> %s", chosen, fallback)
            path = fallback
    return load_anaphora_config_from_path(path)
