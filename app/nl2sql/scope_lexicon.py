from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from app.core.logging import get_logger
from app.nl2sql.intent_config import scope_lexicon_file

logger = get_logger(__name__)

_DEFAULT_ABBREVIATIONS: dict[str, str] = {
    "低过": "低温过热器",
    "高过": "高温过热器",
    "高再": "高温再热器",
    "低再": "低温再热器",
    "屏过": "屏式过热器",
}

_DEFAULT_DEVICES: tuple[str, ...] = (
    "低温过热器",
    "高温过热器",
    "高温再热器",
    "低温再热器",
    "屏式过热器",
    "分隔屏过热器",
    "水冷壁前墙垂直段",
    "水冷壁后墙垂直段",
    "水冷壁左墙",
    "水冷壁右墙",
    "省煤器",
)

_DEFAULT_PIPEROW_ALIASES: dict[str, str] = {
    "前屏": "第一屏",
    "后屏": "第二屏",
}

_DEFAULT_WALL_ROW1_MARKERS: tuple[str, ...] = ("水冷壁", "包墙", "后竖井", "冷灰斗")

_DEVICE_CANONICAL: dict[str, str] = {
    "分隔屏过热器": "屏式过热器",
}


@dataclass(frozen=True)
class ScopeLexicon:
    abbreviations: dict[str, str]
    devices: tuple[str, ...]
    piperow_aliases: dict[str, str]
    wall_row1_markers: tuple[str, ...]
    device_canonical: dict[str, str]

    @property
    def devices_by_length(self) -> tuple[str, ...]:
        return tuple(sorted(self.devices, key=len, reverse=True))


def _default_lexicon_path() -> Path:
    return Path(__file__).resolve().parents[2] / "configs" / "nl2sql_scope_device_aliases.json"


def _builtin_lexicon() -> ScopeLexicon:
    return ScopeLexicon(
        abbreviations=dict(_DEFAULT_ABBREVIATIONS),
        devices=_DEFAULT_DEVICES,
        piperow_aliases=dict(_DEFAULT_PIPEROW_ALIASES),
        wall_row1_markers=_DEFAULT_WALL_ROW1_MARKERS,
        device_canonical=dict(_DEVICE_CANONICAL),
    )


def _parse_lexicon_payload(data: object) -> ScopeLexicon | None:
    if not isinstance(data, dict):
        return None
    abbrev = data.get("abbreviations") or {}
    devices = data.get("devices") or []
    piperow_aliases = data.get("piperow_aliases") or {}
    wall_markers = data.get("wall_row1_markers") or []
    if not isinstance(abbrev, dict) or not isinstance(devices, list):
        return None
    if not isinstance(piperow_aliases, dict):
        piperow_aliases = {}
    if not isinstance(wall_markers, list):
        wall_markers = list(_DEFAULT_WALL_ROW1_MARKERS)
    device_canonical = dict(_DEVICE_CANONICAL)
    extra_canonical = data.get("device_canonical") or {}
    if isinstance(extra_canonical, dict):
        device_canonical.update({str(k): str(v) for k, v in extra_canonical.items() if k and v})
    return ScopeLexicon(
        abbreviations={str(k): str(v) for k, v in abbrev.items() if k and v},
        devices=tuple(str(x) for x in devices if x),
        piperow_aliases={str(k): str(v) for k, v in piperow_aliases.items() if k and v},
        wall_row1_markers=tuple(str(x) for x in wall_markers if x),
        device_canonical=device_canonical,
    )


_cache_path: Path | None = None
_cache_mtime: float | None = None
_cache_lexicon: ScopeLexicon | None = None


def get_scope_lexicon() -> ScopeLexicon:
    """加载 scope 词典；进程内按文件 mtime 缓存，失败时回退内置默认。"""
    global _cache_path, _cache_mtime, _cache_lexicon

    env_path = (scope_lexicon_file() or "").strip()
    path = Path(env_path) if env_path else _default_lexicon_path()

    try:
        mtime = path.stat().st_mtime if path.is_file() else None
    except OSError:
        mtime = None

    if _cache_lexicon is not None and _cache_path == path and _cache_mtime == mtime:
        return _cache_lexicon

    lexicon = _builtin_lexicon()
    if path.is_file():
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            parsed = _parse_lexicon_payload(raw)
            if parsed and parsed.devices:
                lexicon = parsed
            else:
                logger.warning("NL2SQL scope lexicon invalid root shape: %s", path)
        except (OSError, json.JSONDecodeError) as exc:
            logger.warning("NL2SQL scope lexicon load failed path=%s err=%s", path, exc)
    elif env_path:
        logger.warning("NL2SQL_SCOPE_LEXICON_FILE not found: %s", path)

    _cache_path = path
    _cache_mtime = mtime
    _cache_lexicon = lexicon
    return lexicon


def reset_scope_lexicon_cache() -> None:
    """测试用：清空词典缓存。"""
    global _cache_path, _cache_mtime, _cache_lexicon
    _cache_path = None
    _cache_mtime = None
    _cache_lexicon = None
