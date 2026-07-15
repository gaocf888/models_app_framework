"""智能客服 NL2SQL：用户可见列过滤（隐藏主键/技术 ID）。"""

from __future__ import annotations

import re
from typing import Any

from app.core.logging import get_logger

logger = get_logger(__name__)

# 注入 NL2SQL 生成 prompt（仅客服 data_query；不写死业务枚举）
CHATBOT_NL2SQL_SELECT_DISPLAY_RULES = """
【智能客服·SELECT 用户可读性（本场景强制）】
1) JOIN / WHERE / ON 可使用 id、外键等技术列做关联与过滤。
2) SELECT 列表（面向最终用户展示）：
   - 用户未明确要求「编号/ID/主键」时，禁止在 SELECT 中输出无业务含义的主键、UUID、纯技术 id（如 id、*_id、uuid 等）；
   - 若 catalog 中同一实体同时存在名称类列（如 *_name、*_label、*名称、*标题）与码值列，优先 SELECT 名称类列，不要只选码值列；
   - 仅当无名称类列可用时，才允许 SELECT 码值/状态码列；若同时用 CASE 转中文别名，必须依据 catalog/字段注释中的枚举说明，禁止臆造码表含义。
3) 优先输出业务可读列：名称、时间、描述、类型名称等；列别名用简短中文便于展示。
""".strip()

_EXACT_HIDE_NAMES = frozenset(
    {
        "id",
        "uuid",
        "guid",
        "pk",
        "row_id",
        "record_id",
        "primary_key",
    }
)

_HIDE_NAME_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"^id$"),
    re.compile(r".+_id$"),
    re.compile(r"^id_.+"),
    re.compile(r".+_uuid$"),
    re.compile(r"^uuid_.+"),
    re.compile(r".+_guid$"),
    re.compile(r"^pk_.+"),
    re.compile(r".+_pk$"),
)

_UUID_RE = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)
_HEX32_RE = re.compile(r"^[0-9a-fA-F]{32}$")


def _norm_col(name: str) -> str:
    return str(name or "").strip().lower().replace(" ", "").replace("　", "")


def is_technical_id_column_name(col_name: str) -> bool:
    """按列名判断是否为应对最终用户隐藏的技术 ID 列。"""
    raw = str(col_name or "").strip()
    if not raw:
        return False
    low = _norm_col(raw)
    if low in _EXACT_HIDE_NAMES:
        return True
    for pat in _HIDE_NAME_PATTERNS:
        if pat.match(low):
            return True
    # 中文表头：检修计划ID / 记录ID / 主键 …
    if low.endswith("计划id") or low.endswith("记录id") or low.endswith("主键"):
        return True
    if "主键" in low or "唯一标识" in low:
        return True
    if low.endswith("id") and any(tok in low for tok in ("计划", "记录", "标识")):
        return True
    return False


def _looks_like_uuid_or_hex_id(val: Any) -> bool:
    if val is None:
        return False
    s = str(val).strip()
    if not s:
        return False
    return bool(_UUID_RE.match(s) or _HEX32_RE.match(s))


def is_uuid_like_display_column(col_name: str, values: list[Any]) -> bool:
    """样本值几乎全是 UUID/32hex 时，也视为技术标识列。"""
    samples = [v for v in values if v is not None and str(v).strip()]
    if len(samples) < 2:
        return False
    hits = sum(1 for v in samples if _looks_like_uuid_or_hex_id(v))
    return hits / len(samples) >= 0.8


def should_hide_chatbot_nl2sql_column(col_name: str, sample_values: list[Any] | None = None) -> bool:
    if is_technical_id_column_name(col_name):
        return True
    if sample_values and is_uuid_like_display_column(col_name, sample_values):
        return True
    return False


def filter_chatbot_nl2sql_display_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """
    过滤智能客服 NL2SQL 结果中的技术 ID 列（不改 SQL，只改展示）。
    若过滤后无剩余列，则回退为原行，避免空表。
    """
    if not rows:
        return rows
    col_order: list[str] = []
    seen: set[str] = set()
    for r in rows:
        if not isinstance(r, dict):
            continue
        for k in r.keys():
            sk = str(k)
            if sk not in seen:
                seen.add(sk)
                col_order.append(sk)
    if not col_order:
        return rows

    hide: set[str] = set()
    for col in col_order:
        samples = [r.get(col) for r in rows if isinstance(r, dict)]
        if should_hide_chatbot_nl2sql_column(col, samples):
            hide.add(col)

    keep = [c for c in col_order if c not in hide]
    if not keep:
        logger.info(
            "chatbot.nl2sql_display_filter all columns hidden; keep original cols=%s",
            col_order,
        )
        return rows

    if hide:
        logger.info(
            "chatbot.nl2sql_display_filter hidden_cols=%s kept_cols=%s",
            sorted(hide),
            keep,
        )

    out: list[dict[str, Any]] = []
    for r in rows:
        if not isinstance(r, dict):
            out.append(r)  # type: ignore[arg-type]
            continue
        out.append({c: r.get(c) for c in keep})
    return out
