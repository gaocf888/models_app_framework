"""超温分析 q5：把 sis_pi_data 上的 UPPER(tag) LIKE 扫表改成维表驱动。

现网 qa_replay=strict 会按槽位直接回放旧 SQL。仅改参考 SQL / 问句不够，
必须在后处理里把「事实表 LIKE」改写成「base_dev_pi_b 筛测点后再等值 JOIN」。
"""

from __future__ import annotations

import re

_OVERHEAT_TYPE = "overheat_guidance"
_PLAN_ITEM_Q5 = "q5"

# LIKE 只允许打在维表 pi_code / pi_name_ch 上，禁止再扫 sis_pi_data.tag。
_TAG_CLASS_CASE = """
    CASE
      WHEN UPPER(pi.pi_code) LIKE '%FW%'
        OR UPPER(pi.pi_code) LIKE '%CWF%'
        OR UPPER(pi.pi_code) LIKE '%DSW%'
        OR UPPER(pi.pi_code) LIKE '%DESUP%'
        OR IFNULL(pi.pi_name_ch, '') LIKE '%减温水%' THEN '减温水'
      WHEN UPPER(pi.pi_code) LIKE '%FGT%'
        OR UPPER(pi.pi_code) LIKE '%FLUE%'
        OR UPPER(pi.pi_code) LIKE '%GAS%TEMP%'
        OR UPPER(pi.pi_code) LIKE '%GGT%'
        OR IFNULL(pi.pi_name_ch, '') LIKE '%炉膛烟温%'
        OR IFNULL(pi.pi_name_ch, '') LIKE '%烟温%' THEN '烟温'
      WHEN UPPER(pi.pi_code) LIKE '%EXH%'
        OR UPPER(pi.pi_code) LIKE '%STACK%'
        OR UPPER(pi.pi_code) LIKE '%ESP%'
        OR IFNULL(pi.pi_name_ch, '') LIKE '%排烟%' THEN '排烟'
      WHEN UPPER(pi.pi_code) LIKE '%TAF%'
        OR UPPER(pi.pi_code) LIKE '%PAF%'
        OR UPPER(pi.pi_code) LIKE '%SAF%'
        OR UPPER(pi.pi_code) LIKE '%AIR%FLOW%'
        OR UPPER(pi.pi_code) LIKE '%TOTAL%AIR%'
        OR pi.pi_name_ch = '总风量' THEN '总风量'
      WHEN UPPER(pi.pi_code) LIKE '%O2%'
        OR UPPER(pi.pi_code) LIKE '%OXY%'
        OR UPPER(pi.pi_code) LIKE '%OXYGEN%'
        OR pi.pi_name_ch = '炉膛出口氧量' THEN '氧量'
      WHEN UPPER(pi.pi_code) LIKE '%DRFT%'
        OR UPPER(pi.pi_code) LIKE '%FDRAFT%'
        OR UPPER(pi.pi_code) LIKE '%FURN%P%'
        OR UPPER(pi.pi_code) LIKE '%FURNACE%PRESS%'
        OR IFNULL(pi.pi_name_ch, '') LIKE '%炉膛负压%' THEN '炉膛负压'
      ELSE '其他关联参数'
    END
""".strip()

_TAG_INCLUDE_PRED = """
      UPPER(pi.pi_code) LIKE '%FW%'
      OR UPPER(pi.pi_code) LIKE '%CWF%'
      OR UPPER(pi.pi_code) LIKE '%DSW%'
      OR UPPER(pi.pi_code) LIKE '%DESUP%'
      OR UPPER(pi.pi_code) LIKE '%FGT%'
      OR UPPER(pi.pi_code) LIKE '%FLUE%'
      OR UPPER(pi.pi_code) LIKE '%GAS%TEMP%'
      OR UPPER(pi.pi_code) LIKE '%GGT%'
      OR UPPER(pi.pi_code) LIKE '%EXH%'
      OR UPPER(pi.pi_code) LIKE '%STACK%'
      OR UPPER(pi.pi_code) LIKE '%ESP%'
      OR UPPER(pi.pi_code) LIKE '%TAF%'
      OR UPPER(pi.pi_code) LIKE '%PAF%'
      OR UPPER(pi.pi_code) LIKE '%SAF%'
      OR UPPER(pi.pi_code) LIKE '%AIR%FLOW%'
      OR UPPER(pi.pi_code) LIKE '%TOTAL%AIR%'
      OR UPPER(pi.pi_code) LIKE '%O2%'
      OR UPPER(pi.pi_code) LIKE '%OXY%'
      OR UPPER(pi.pi_code) LIKE '%OXYGEN%'
      OR UPPER(pi.pi_code) LIKE '%DRFT%'
      OR UPPER(pi.pi_code) LIKE '%FDRAFT%'
      OR UPPER(pi.pi_code) LIKE '%FURN%P%'
      OR UPPER(pi.pi_code) LIKE '%FURNACE%PRESS%'
      OR IFNULL(pi.pi_name_ch, '') LIKE '%减温水%'
      OR IFNULL(pi.pi_name_ch, '') LIKE '%炉膛烟温%'
      OR IFNULL(pi.pi_name_ch, '') LIKE '%烟温%'
      OR IFNULL(pi.pi_name_ch, '') LIKE '%排烟%'
      OR IFNULL(pi.pi_name_ch, '') LIKE '%炉膛负压%'
      OR pi.pi_name_ch IN ('炉膛出口氧量', '总风量')
""".strip()

_WALL_TEMP_PRED = """
      UPPER(pi.pi_code) LIKE '%CT%'
      AND (
        UPPER(pi.pi_code) LIKE '%HAD%'
        OR UPPER(pi.pi_code) LIKE '%HAC%'
        OR UPPER(pi.pi_code) LIKE '%HAH%'
      )
""".strip()

_SPD_TAG_LIKE = re.compile(r"UPPER\s*\(\s*spd\.tag\s*\)\s*LIKE", re.IGNORECASE)
_DIM_DRIVE_JOIN = re.compile(
    r"INNER\s+JOIN\s+sis_pi_data\s+spd\s+ON\s+spd\.tag\s*=\s*tags\.pi_code",
    re.IGNORECASE,
)
_INNER_JOIN_OPEN = re.compile(r"INNER\s+JOIN\s*\(", re.IGNORECASE)
_EVT_TIME_BOUNDS = re.compile(
    r"t\.start_time\s*>=\s*(.+?)\s+AND\s+t\.start_time\s*<\s*(.+?)\s+AND",
    re.IGNORECASE | re.DOTALL,
)


def rewrite_overheat_q5_dim_drive(
    sql: str,
    *,
    plan_item_id: str | None = None,
    analysis_type: str | None = None,
) -> tuple[str, list[str]]:
    """将超温 q5 的事实表 LIKE 扫表改成 base_dev_pi_b 驱动。不匹配则原样返回。"""
    if (plan_item_id or "").strip() != _PLAN_ITEM_Q5:
        return sql, []
    if (analysis_type or "").strip() != _OVERHEAT_TYPE:
        return sql, []
    text = (sql or "").strip()
    if not text:
        return sql, []
    if _DIM_DRIVE_JOIN.search(text) and not _SPD_TAG_LIKE.search(text):
        return sql, []
    if not _SPD_TAG_LIKE.search(text):
        return sql, []
    if not re.search(r"\bsis_pi_data\b", text, re.IGNORECASE):
        return sql, []
    if not re.search(r"\bbase_dev_pi_b\b", text, re.IGNORECASE):
        return sql, []

    evt_body = _extract_evt_subquery(text)
    if not evt_body:
        return sql, []

    extra_time_pred = _sargable_lower_time_pred(evt_body)
    rewritten = _build_dim_drive_sql(evt_body, extra_time_pred)
    notes = ["overheat_q5_dim_drive"]
    if extra_time_pred:
        notes.append("overheat_q5_sargable_time")
    return rewritten, notes


def _extract_evt_subquery(sql: str) -> str | None:
    for match in _INNER_JOIN_OPEN.finditer(sql):
        open_idx = match.end() - 1
        close_idx = _matching_paren(sql, open_idx)
        if close_idx is None:
            continue
        after = sql[close_idx + 1 : close_idx + 24]
        if re.match(r"\s*evt\b", after, re.IGNORECASE):
            return sql[open_idx + 1 : close_idx].strip()
    return None


def _matching_paren(sql: str, open_idx: int) -> int | None:
    depth = 0
    in_single = False
    in_double = False
    i = open_idx
    while i < len(sql):
        ch = sql[i]
        if in_single:
            if ch == "\\" and i + 1 < len(sql):
                i += 2
                continue
            if ch == "'":
                if i + 1 < len(sql) and sql[i + 1] == "'":
                    i += 2
                    continue
                in_single = False
            i += 1
            continue
        if in_double:
            if ch == "\\" and i + 1 < len(sql):
                i += 2
                continue
            if ch == '"':
                in_double = False
            i += 1
            continue
        if ch == "'":
            in_single = True
            i += 1
            continue
        if ch == '"':
            in_double = True
            i += 1
            continue
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
            if depth == 0:
                return i
        i += 1
    return None


def _sargable_lower_time_pred(evt_body: str) -> str:
    """evt_min >= @t_start-30min 恒成立；不上界，避免截断 start 在窗内但 end 更晚的事件。"""
    match = _EVT_TIME_BOUNDS.search(evt_body)
    if not match:
        return ""
    start_expr = re.sub(r"\s+", " ", match.group(1)).strip()
    if not start_expr:
        return ""
    return f" AND spd.data_time >= DATE_SUB(({start_expr}), INTERVAL 30 MINUTE)"


def _build_dim_drive_sql(evt_body: str, extra_time_pred: str) -> str:
    return f"""SELECT
  tags.参数类型,
  spd.tag AS 测点编码,
  MAX(IFNULL(tags.pi_name_ch, spd.tag)) AS 测点名称,
  COUNT(*) AS 采样点数,
  COUNT(DISTINCT DATE(spd.data_time)) AS 采样天数,
  ROUND(MIN(spd.value), 2) AS 最小值,
  ROUND(MAX(spd.value), 2) AS 最大值,
  ROUND(AVG(spd.value), 2) AS 平均值,
  DATE_FORMAT(MIN(spd.data_time), '%Y.%m.%d %H:%i:%s') AS 首次采样时间,
  DATE_FORMAT(MAX(spd.data_time), '%Y.%m.%d %H:%i:%s') AS 末次采样时间
FROM (
  SELECT
    pi.pi_code,
    pi.pi_name_ch,
    {_TAG_CLASS_CASE} AS 参数类型
  FROM base_dev_pi_b pi
  WHERE NOT (
{_WALL_TEMP_PRED}
  )
  AND (
{_TAG_INCLUDE_PRED}
  )
) tags
INNER JOIN sis_pi_data spd ON spd.tag = tags.pi_code
INNER JOIN (
{evt_body}
) evt ON spd.data_time >= evt.evt_min AND spd.data_time <= evt.evt_max
WHERE tags.参数类型 <> '其他关联参数'{extra_time_pred}
GROUP BY tags.参数类型, spd.tag
ORDER BY tags.参数类型, 采样点数 DESC, 测点编码"""
