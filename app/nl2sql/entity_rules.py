from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.core.logging import get_logger

logger = get_logger(__name__)


@dataclass(frozen=True)
class EntityRule:
    """当用户问题命中关键词且 SQL 匹配给定正则时，判为语义高风险。"""

    question_contains_any: tuple[str, ...]
    sql_pattern: re.Pattern[str]
    message: str


def _parse_rule(obj: Any) -> EntityRule | None:
    if not isinstance(obj, dict):
        return None
    keys = obj.get("question_contains_any") or obj.get("question_contains")
    if not keys:
        return None
    if isinstance(keys, str):
        keys_t = (keys,)
    elif isinstance(keys, list):
        keys_t = tuple(str(x) for x in keys if x is not None)
    else:
        return None
    pat = obj.get("sql_pattern") or obj.get("sql_regex")
    if not pat or not isinstance(pat, str):
        return None
    try:
        cre = re.compile(pat, re.IGNORECASE | re.DOTALL)
    except re.error as e:
        logger.warning("NL2SQL entity rule invalid regex %r: %s", pat, e)
        return None
    msg = str(obj.get("message") or "entity / business rule violation")
    return EntityRule(question_contains_any=keys_t, sql_pattern=cre, message=msg)


def load_entity_rules_from_env() -> list[EntityRule]:
    """
    从环境变量加载业务实体规则（可选）。

    - NL2SQL_ENTITY_RULES：JSON 数组字符串；
    - 或 NL2SQL_ENTITY_RULES_FILE：指向 JSON 文件（数组）。
    """
    raw_path = os.getenv("NL2SQL_ENTITY_RULES_FILE", "").strip()
    raw_inline = os.getenv("NL2SQL_ENTITY_RULES", "").strip()
    text = ""
    if raw_path:
        p = Path(raw_path)
        if p.is_file():
            text = p.read_text(encoding="utf-8")
        else:
            logger.warning("NL2SQL_ENTITY_RULES_FILE not found: %s", raw_path)
    elif raw_inline:
        text = raw_inline
    if not text:
        return []
    try:
        data = json.loads(text)
    except json.JSONDecodeError as e:
        logger.warning("NL2SQL entity rules JSON decode error: %s", e)
        return []
    if not isinstance(data, list):
        logger.warning("NL2SQL entity rules root must be a JSON array")
        return []
    out: list[EntityRule] = []
    for item in data:
        r = _parse_rule(item)
        if r:
            out.append(r)
    return out


def check_entity_rules(question: str, sql: str, rules: list[EntityRule]) -> tuple[bool, str | None]:
    """
    若命中任一规则返回 (False, message)，否则 (True, None)。
    """
    q = question or ""
    s = sql or ""
    for rule in rules:
        if not any(k in q for k in rule.question_contains_any):
            continue
        if rule.sql_pattern.search(s):
            return False, rule.message
    return True, None
