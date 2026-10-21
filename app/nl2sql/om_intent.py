"""
地降运维（om_result）意图识别与 SQL 辅助。

- 问句运维 vs 沉降分流（收窄 allowlist / catalog）
- 强制最新 ``snapshot_time`` 批次过滤的检测与提示
"""
from __future__ import annotations

import re
from typing import Any, Iterable

# 明确运维指标 / 场景短语（避免裸「告警」「预警」误伤沉降阈值类问句）
_OPS_PHRASES: tuple[str, ...] = (
    "运维中心",
    "运维",
    "在线率",
    "在线情况",
    "完整率",
    "有效率",
    "三率",
    "完好率",
    "数据完整",
    "数据有效",
    "仪器告警",
    "设备告警",
    "数据告警",
    "告警仪器",
    "告警列表",
    "告警总览",
    "告警占比",
    "告警设备占比",
    "告警台数",
    "告警设备台数",
    "有警",
    "离线仪器",
    "离线设备",
    "离线列表",
    "离线时长",
    "离线天数",
    "离线多久",
    "月完整",
    "月有效",
    "不达标设备",
)

# 须与设备/仪器/数据等邻近才算运维的弱词
_OPS_WEAK_ALARM = ("告警", "预警", "报警")
_OPS_WEAK_CONTEXT = ("设备", "仪器", "运维", "在线", "离线", "监测站")

# 排除：沉降规范/阈值语境
_OPS_EXCLUDE_NEAR_ALARM = ("阈值", "机理", "规范", "成因", "原理", "预警值", "告警值")

_OM_TABLE_RE = re.compile(r"\b(t_om_[a-z0-9_]+)\b", re.IGNORECASE)
_OFFLINE_TABLE_RE = re.compile(r"\bt_om_offline_device_list\b", re.IGNORECASE)
_STATUS_ZERO_RE = re.compile(r"\bstatus\s*=\s*0\b", re.IGNORECASE)

# 最新批：snapshot_time = (SELECT max(snapshot_time) ...) 或 JOIN latest CTE
_LATEST_EQ_MAX_RE = re.compile(
    r"\bsnapshot_time\b\s*=\s*\(\s*SELECT\s+max\s*\(\s*snapshot_time\s*\)",
    re.IGNORECASE,
)
_JOIN_LATEST_RE = re.compile(
    r"\bJOIN\s+latest\b|\bINNER\s+JOIN\s+latest\b",
    re.IGNORECASE,
)
_LATEST_CTE_RE = re.compile(
    r"\blatest\s+AS\s*\(\s*SELECT\s+max\s*\(\s*snapshot_time\s*\)",
    re.IGNORECASE,
)

_TAIL_CLAUSE_RE = re.compile(
    r"(?is)\b(GROUP\s+BY|ORDER\s+BY|LIMIT|OFFSET|HAVING|UNION|INTERSECT|EXCEPT)\b"
)


def is_ops_question(question: str) -> bool:
    raw = (question or "").strip()
    if not raw:
        return False
    q = raw.lower()
    for p in _OPS_PHRASES:
        if p.lower() in q or p in raw:
            return True
    # 弱「告警/预警」：需设备语境且非阈值/规范
    for weak in _OPS_WEAK_ALARM:
        if weak not in raw and weak.lower() not in q:
            continue
        if any(ex in raw for ex in _OPS_EXCLUDE_NEAR_ALARM):
            continue
        if any(ctx in raw for ctx in _OPS_WEAK_CONTEXT):
            return True
    # 「离线」单独出现且带设备语境
    if ("离线" in raw) and any(ctx in raw for ctx in ("设备", "仪器", "站点", "监测")):
        return True
    return False


def sql_mentions_om_tables(sql: str) -> bool:
    return bool(_OM_TABLE_RE.search(sql or ""))


def list_om_tables_in_sql(sql: str) -> list[str]:
    return sorted({m.group(1).lower() for m in _OM_TABLE_RE.finditer(sql or "")})


def sql_has_latest_snapshot_filter(sql: str) -> bool:
    """
    要求「最新批次」形态之一：

    - ``snapshot_time = (SELECT max(snapshot_time) …)``
    - ``WITH latest AS (SELECT max(snapshot_time) …)`` 且 ``JOIN latest``
    """
    s = sql or ""
    if _LATEST_EQ_MAX_RE.search(s):
        return True
    if _LATEST_CTE_RE.search(s) and (
        _JOIN_LATEST_RE.search(s)
        or re.search(r"\bsnapshot_time\s*=\s*l(?:atest)?\.snapshot_time\b", s, re.IGNORECASE)
        or re.search(r"\bo\.snapshot_time\s*=\s*l\.snapshot_time\b", s, re.IGNORECASE)
        or re.search(r"\.snapshot_time\s*=\s*\w+\.snapshot_time\b", s, re.IGNORECASE)
    ):
        return True
    return False


def ops_snapshot_prompt_hint() -> str:
    return (
        "【运维库 om_result】查询 t_om_* 时必须过滤最新批次："
        "WHERE snapshot_time = (SELECT max(snapshot_time) FROM om_result.<当前主查询的 t_om_* 本表>) "
        "（8 表同批 snapshot_time 相同；禁止为取 max 额外引用未在白名单中的表，"
        "尤其不要写死 t_om_data_alarm_summary，除非主查询本身就是该表）；"
        "或 WITH latest AS (SELECT max(snapshot_time) AS snapshot_time FROM 本表) JOIN latest。"
        "表名建议写成 om_result.t_om_*；仪器类型列名为 type（中文，如 GNSS、地下水位井）；"
        "查 t_om_offline_device_list 必须加 status=0（当前离线）；"
        "离线时长=now()-data_collect_time（可再 /86400 得天数）；"
        "告警占比查 t_om_data_alarm_summary 本表：alarm_count/total_count*100；"
        "告警持续时间用 now()-off_time；月完整/有效列表仅含低于阈值设备。"
        "禁止用沉降 wash 表或观测天数估算三率/在线率。"
    )


def sql_mentions_offline_list(sql: str) -> bool:
    return bool(_OFFLINE_TABLE_RE.search(sql or ""))


def sql_has_offline_status_filter(sql: str) -> bool:
    """离线列表须 ``status = 0``（当前离线）。"""
    return bool(_STATUS_ZERO_RE.search(sql or ""))


def settlement_only_prompt_hint() -> str:
    return (
        "【沉降域】本问为沉降/站点读数；使用 t_data_wash_* / t_station；"
        "不要查询 om_result / t_om_* 运维表。"
    )


def qualify_bare_om_tables(sql: str, *, om_schema: str = "om_result") -> str:
    """
    将裸 ``t_om_*`` 改为 ``{om_schema}.t_om_*``（已有任意 ``ident.`` 前缀则跳过）。
    """
    if not sql or not om_schema:
        return sql
    schema = om_schema.strip()
    parts: list[str] = []
    last = 0
    for m in _OM_TABLE_RE.finditer(sql):
        start = m.start()
        prefix = sql[max(0, start - 80) : start]
        if re.search(r"[A-Za-z_][A-Za-z0-9_]*\s*\.\s*$", prefix):
            continue
        parts.append(sql[last:start])
        parts.append(f"{schema}.{m.group(1)}")
        last = m.end()
    parts.append(sql[last:])
    return "".join(parts)


def filter_allowlist_for_question(
    question: str,
    *,
    settlement_tables: Iterable[str],
    om_tables: Iterable[str],
) -> set[str]:
    """按问句意图收窄表白名单（互斥优先：运维问句只用 om）。"""
    settle = {t.lower() for t in settlement_tables if t}
    om = {t.lower() for t in om_tables if t}
    if is_ops_question(question) and om:
        return set(om)
    return set(settle) if settle else (settle | om)


def is_ops_nl2sql_path(question: str, sql: str | None = None) -> bool:
    """运维分流判定：问句运维，或 SQL 已落在 t_om_*（用于改写阶段隔离沉降规则）。"""
    if is_ops_question(question):
        return True
    return sql_mentions_om_tables(sql or "")


def ops_safe_suggested_filters(filters: Iterable[Any] | None) -> list[Any]:
    """运维路径仅保留仪器 type 过滤；剥离 fcb_layer0 / 覆盖站名等沉降名单。"""
    out: list[Any] = []
    for raw in filters or []:
        if not isinstance(raw, dict):
            continue
        if str(raw.get("source") or "").strip() == "om_device_type":
            out.append(raw)
    return out


# 沉降「每站最新」误灌进运维 SQL 的痕迹
_OPS_STRIP_DISTINCT_ON_RE = re.compile(
    r"(?is)\bSELECT\s+DISTINCT\s+ON\s*\(\s*(?:[a-zA-Z_][\w]*\.)?(?:station_name|project_name)\s*\)"
)
_OPS_STRIP_ORDER_LATEST_RE = re.compile(
    r"(?is)\bORDER\s+BY\s+(?:[a-zA-Z_][\w]*\.)?(?:station_name|project_name)\s*,\s*"
    r"(?:[a-zA-Z_][\w]*\.)?data_time\s+DESC\b"
)


def sanitize_ops_sql_settlement_artifacts(sql: str) -> tuple[str, list[str]]:
    """
    运维 SQL 清洗：去掉误注入的沉降站名/场地谓词与 DISTINCT ON (station_name) 等。

    不影响沉降问句路径（仅在调用方判定运维后调用）。
    """
    from app.nl2sql.suggested_filters_rewrite import _remove_column_predicate

    notes: list[str] = []
    rewritten = (sql or "").strip()
    if not rewritten or not sql_mentions_om_tables(rewritten):
        return rewritten, notes

    if _OPS_STRIP_DISTINCT_ON_RE.search(rewritten):
        rewritten = _OPS_STRIP_DISTINCT_ON_RE.sub("SELECT", rewritten, count=1)
        notes.append("ops_strip_distinct_on_settlement_grain")
    if _OPS_STRIP_ORDER_LATEST_RE.search(rewritten):
        rewritten = _OPS_STRIP_ORDER_LATEST_RE.sub("", rewritten, count=1)
        notes.append("ops_strip_order_by_settlement_latest")

    for col in ("station_name", "project_name"):
        while True:
            rewritten2, removed = _remove_column_predicate(rewritten, col)
            if not removed:
                break
            rewritten = rewritten2
            notes.append(f"ops_strip_settlement_pred:{col}")

    rewritten = re.sub(r"(?is)\bWHERE\s+(AND|OR)\b", "WHERE ", rewritten)
    rewritten = re.sub(r"(?is)\bWHERE\s*(?=(?:GROUP|ORDER|LIMIT)\b|$)", " ", rewritten)
    rewritten = re.sub(r"[ \t]+", " ", rewritten)
    rewritten = re.sub(r"\s+\n", "\n", rewritten)
    return rewritten.strip(), notes


def _inject_where_predicate(sql: str, pred: str) -> str | None:
    """在主查询 WHERE 追加谓词；无 WHERE 则在 FROM 后插入。失败返回 None。"""
    s = sql
    where_m = re.search(r"(?is)\bWHERE\b", s)
    if where_m:
        start = where_m.end()
        tail_m = _TAIL_CLAUSE_RE.search(s, start)
        end = tail_m.start() if tail_m else len(s)
        body = s[start:end].rstrip()
        if body:
            return s[:start] + " " + body + " AND " + pred + " " + s[end:]
        return s[:start] + " " + pred + " " + s[end:]
    from_m = re.search(r"(?is)\bFROM\b", s)
    if not from_m:
        return None
    tail_m = _TAIL_CLAUSE_RE.search(s, from_m.end())
    insert_at = tail_m.start() if tail_m else len(s)
    return s[:insert_at].rstrip() + " WHERE " + pred + " " + s[insert_at:]


_OM_FROM_RE = re.compile(
    r"(?is)\bFROM\s+(?:([a-zA-Z_][\w]*)\s*\.\s*)?(t_om_[a-z0-9_]+)\b"
)
_SNAPSHOT_MAX_FROM_RE = re.compile(
    r"(?is)(\(\s*SELECT\s+max\s*\(\s*snapshot_time\s*\)\s+FROM\s+)"
    r"(?:([a-zA-Z_][\w]*)\s*\.\s*)?(t_om_[a-z0-9_]+)"
)
_SNAPSHOT_MAX_CTE_FROM_RE = re.compile(
    r"(?is)(\blatest\s+AS\s*\(\s*SELECT\s+max\s*\(\s*snapshot_time\s*\)"
    r"(?:\s+AS\s+[a-zA-Z_][\w]*)?\s+FROM\s+)"
    r"(?:([a-zA-Z_][\w]*)\s*\.\s*)?(t_om_[a-z0-9_]+)"
)
_SNAPSHOT_MAX_SUBQUERY_BLANK_RE = re.compile(
    r"(?is)\(\s*SELECT\s+max\s*\(\s*snapshot_time\s*\)\s+FROM\s+"
    r"(?:[a-zA-Z_][\w]*\s*\.\s*)?t_om_[a-z0-9_]+\s*\)"
)
_LATEST_CTE_BLANK_RE = re.compile(
    r"(?is)\bWITH\s+latest\s+AS\s*\([^)]*\)"
)


def resolve_om_primary_table(sql: str, *, preferred: str | None = None) -> str | None:
    """解析运维 SQL 主查询 t_om_*（优先 preferred / SchemaLink primary）。"""
    pref = (preferred or "").strip().lower()
    if pref.startswith("t_om_"):
        return pref
    s = sql or ""
    # 屏蔽 max(snapshot_time) 子查询与 latest CTE，避免误把批次表当主表
    s2 = _SNAPSHOT_MAX_SUBQUERY_BLANK_RE.sub("(SELECT max(snapshot_time) FROM __ops_snap__)", s)
    s2 = _LATEST_CTE_BLANK_RE.sub("WITH latest AS (__ops_cte__)", s2)
    m = _OM_FROM_RE.search(s2)
    if m:
        return m.group(2).lower()
    hit = _OM_TABLE_RE.search(s)
    return hit.group(1).lower() if hit else None


def rewrite_snapshot_max_to_primary(
    sql: str,
    *,
    primary: str,
    om_schema: str = "om_result",
) -> tuple[str, list[str]]:
    """将 max(snapshot_time) 子查询/CTE 的 FROM 改写为当前主表。"""
    notes: list[str] = []
    primary_l = (primary or "").strip().lower()
    if not sql or not primary_l.startswith("t_om_"):
        return sql, notes
    schema = (om_schema or "om_result").strip() or "om_result"
    target = f"{schema}.{primary_l}"

    def _repl(m: re.Match[str]) -> str:
        table = (m.group(3) or "").lower()
        if table == primary_l:
            # 统一补 schema 前缀，便于与注入形态一致
            return f"{m.group(1)}{target}"
        notes.append(f"ops_snapshot_max_rewritten:{table}->{primary_l}")
        return f"{m.group(1)}{target}"

    out = _SNAPSHOT_MAX_FROM_RE.sub(_repl, sql)
    out = _SNAPSHOT_MAX_CTE_FROM_RE.sub(_repl, out)
    return out, notes


def ensure_latest_snapshot_filter(
    sql: str,
    *,
    om_schema: str = "om_result",
    primary_table: str | None = None,
) -> tuple[str, list[str]]:
    """
    运维 t_om_*：保证最新批次过滤，且 max(snapshot_time) 取自**当前主表**。

    - 已有过滤但 FROM 指向其它 t_om_*（常见写死 summary）：改写为本表；
    - 缺少过滤：注入 ``snapshot_time = (SELECT max(snapshot_time) FROM {schema}.{primary})``。

    交付口径：8 表同批 snapshot_time 相同，对本表取 max 与对 summary 等价，且不扩大表白名单。
    """
    notes: list[str] = []
    s = (sql or "").strip()
    if not s or not sql_mentions_om_tables(s):
        return s, notes
    schema = (om_schema or "om_result").strip() or "om_result"
    primary = resolve_om_primary_table(s, preferred=primary_table) or "t_om_data_alarm_summary"

    s, rewrite_notes = rewrite_snapshot_max_to_primary(s, primary=primary, om_schema=schema)
    notes.extend(rewrite_notes)

    if sql_has_latest_snapshot_filter(s):
        return s.strip(), notes

    pred = f"snapshot_time = (SELECT max(snapshot_time) FROM {schema}.{primary})"
    injected = _inject_where_predicate(s, pred)
    if injected is None:
        return s, notes
    notes.append(f"injected_latest_snapshot_filter:{primary}")
    return injected.strip(), notes


def ensure_offline_status_filter(sql: str) -> tuple[str, list[str]]:
    """
    若 SQL 含 ``t_om_offline_device_list`` 且缺少 ``status = 0``，则注入。

    与报告离线明细口径一致（当前离线设备）。
    """
    notes: list[str] = []
    s = (sql or "").strip()
    if not s or not sql_mentions_offline_list(s):
        return s, notes
    if sql_has_offline_status_filter(s):
        return s, notes
    injected = _inject_where_predicate(s, "status = 0")
    if injected is None:
        return s, notes
    notes.append("injected_offline_status_eq_0")
    return injected.strip(), notes