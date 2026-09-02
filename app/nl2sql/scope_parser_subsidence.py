"""地面沉降范围规则解析（行政区 / 站点 / 监测类型）。"""

from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path

from app.nl2sql.nl2sql_business_profile import get_nl2sql_business_profile
from app.nl2sql.question_scope_models import QuestionScopeIntent

_PAREN_ALIAS_RE = re.compile(r"[（(]([^）)]+)[）)]")


@lru_cache(maxsize=2)
def _load_subsidence_lexicon(path: str) -> dict:
    p = Path(path)
    if not p.is_file():
        return {}
    return json.loads(p.read_text(encoding="utf-8"))


def _lexicon_path() -> Path | None:
    profile = get_nl2sql_business_profile()
    if not profile or not profile.scope_lexicon_file:
        return None
    root = Path(__file__).resolve().parents[2]
    p = Path(profile.scope_lexicon_file)
    if not p.is_absolute():
        p = root / p
    return p if p.is_file() else None


def _station_match_candidates(ent: dict) -> list[tuple[str, str]]:
    """返回 (匹配串, 站点展示名) 列表，长串优先匹配。"""
    name = str(ent.get("name") or ent.get("station_name") or "").strip()
    code = str(ent.get("code") or "").strip()
    sid = str(ent.get("id") or ent.get("station_id") or "").strip()
    out: list[tuple[str, str]] = []
    seen: set[str] = set()

    def add(token: str) -> None:
        t = (token or "").strip()
        if not t or t in seen:
            return
        seen.add(t)
        out.append((t, name or t))

    add(name)
    for alias in ent.get("aliases") or []:
        add(str(alias))
    for m in _PAREN_ALIAS_RE.finditer(name):
        add(m.group(1))
    if code and code != name:
        add(code)
    if sid and sid not in {name, code}:
        add(sid)
    return sorted(out, key=lambda x: len(x[0]), reverse=True)


def parse_scope_subsidence(question: str, *, lexicon_file: str | None = None) -> QuestionScopeIntent:
    q = (question or "").strip()
    path = Path(lexicon_file) if lexicon_file else _lexicon_path()
    if path is not None and not path.is_file():
        path = None
    data: dict = {}
    if path:
        data = _load_subsidence_lexicon(str(path))

    district: str | None = None
    for d in sorted(data.get("districts") or [], key=len, reverse=True):
        if d and d in q:
            district = d
            break

    device_type: str | None = None
    device_map = data.get("device_types") or {}
    for phrase, dtype in sorted(device_map.items(), key=lambda x: len(str(x[0])), reverse=True):
        if phrase and phrase in q:
            device_type = str(dtype)
            break

    station_id: str | None = None
    station_name: str | None = None
    best_len = 0
    for ent in data.get("stations") or []:
        if not isinstance(ent, dict):
            continue
        for token, display_name in _station_match_candidates(ent):
            if token in q and len(token) >= best_len:
                station_name = display_name
                sid = ent.get("id") or ent.get("station_id")
                station_id = str(sid) if sid else None
                best_len = len(token)
                break

    return QuestionScopeIntent(
        district=district,
        station_id=station_id,
        station_name=station_name,
        device_type=device_type,
    )
