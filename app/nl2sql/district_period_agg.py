"""行政区周期沉降对比：共享约束与最小骨架（客服 hint / RAG 注入 / refine）。

对应大流程：NL2SQL 生成提示(③b)、RAG 召回增强(③a)、执行失败 refine(⑥)。
"""

from __future__ import annotations

import re

# 识别 Postgres「列须出现在 GROUP BY」类执行错误（含 refine 喂入的 executor 文案）
_GROUPING_ERROR_RE = re.compile(
    r"GroupingError|must appear in the GROUP BY clause|用于聚合函数",
    re.IGNORECASE,
)

# 坏 SQL 特征：窗口层误加 GROUP BY / 虚构 settle_*_time
_BAD_DISTRICT_SQL_RE = re.compile(
    r"ROW_NUMBER\s*\(|settle_start_time|settle_end_time",
    re.IGNORECASE,
)

DISTRICT_PERIOD_AGG_RULES = """
【行政区周期沉降·强制骨架】
目标：各行政区对「层位0站级 period_settle_mm」求 AVG，再按沉降偏大排序。
必遵步骤（PostgreSQL）：
A) WITH ranked：从 t_data_wash_fcb 取窗内行，JOIN t_station ON project_name=s.name；
   仅 station_name IN (层位0名单)；ROW_NUMBER PARTITION BY project_name,station_name 升/降序得 rn_asc/rn_desc。
   ★ 含窗口函数的 SELECT 禁止再写 GROUP BY。
B) WITH period：对 ranked 按 project_name,station_name,area 聚合：
   period_settle_mm = MAX(CASE WHEN rn_asc=1 THEN total_settle END)
                    - MAX(CASE WHEN rn_desc=1 THEN total_settle END)；
   HAVING 初末均非空。禁止虚构 settle_start_time/settle_end_time 列。
C) 最终：SELECT area, COUNT(DISTINCT project_name), AVG(period_settle_mm) FROM period
   GROUP BY area ORDER BY AVG(period_settle_mm) ASC（越负=沉降越多）。
禁止：
- 在窗口子查询里 GROUP BY project_name,station_name 却 SELECT data_time/total_settle；
- project_name IN ('F1','F2') 短码（须完整场地名如 F2(望京)，或省略 project_name IN、只靠层位0+JOIN）；
- 相关子查询逐站算初末；用站级列表冒充区汇总。
""".strip()

DISTRICT_PERIOD_AGG_MIN_SKELETON = """
-- 最小可运行骨架（时间窗按问句；层位0名单由改写注入）
WITH ranked AS (
  SELECT f.project_name, f.station_name, s.area, f.total_settle, f.data_time,
         ROW_NUMBER() OVER (PARTITION BY f.project_name, f.station_name ORDER BY f.data_time ASC) AS rn_asc,
         ROW_NUMBER() OVER (PARTITION BY f.project_name, f.station_name ORDER BY f.data_time DESC) AS rn_desc
  FROM t_data_wash_fcb f
  JOIN t_station s ON f.project_name = s.name
  WHERE f.data_time >= (NOW() - INTERVAL '7 days') AND f.data_time < NOW()
    AND f.station_name IN (/* 层位0 */)
),
period AS (
  SELECT project_name, station_name, area,
         MAX(CASE WHEN rn_asc = 1 THEN total_settle END)
           - MAX(CASE WHEN rn_desc = 1 THEN total_settle END) AS period_settle_mm
  FROM ranked
  GROUP BY project_name, station_name, area
  HAVING MAX(CASE WHEN rn_asc = 1 THEN total_settle END) IS NOT NULL
     AND MAX(CASE WHEN rn_desc = 1 THEN total_settle END) IS NOT NULL
)
SELECT area,
       COUNT(DISTINCT project_name) AS station_cnt,
       AVG(period_settle_mm) AS avg_period_settle_mm
FROM period
GROUP BY area
ORDER BY avg_period_settle_mm ASC
""".strip()

DISTRICT_PERIOD_AGG_REFINE_EXTRA = """
【执行失败·区级周期修正·强制】
当前错误多为非法 GROUP BY / 窗口混用。禁止把报错列简单塞进 GROUP BY。
请丢弃错误内层，按下列骨架整段重写：窗口层(ranked)不要 GROUP BY；用 rn_asc/rn_desc=1 的 MAX(CASE) 算初末差；
再按 area 做 AVG(period_settle_mm) ORDER BY ASC。不要使用 settle_start_time/settle_end_time。
""".strip()


def is_district_grain_question(question: str) -> bool:
    q = (question or "").strip()
    if not q:
        return False
    try:
        from app.data_query_agent.scope_intent import infer_result_grain

        return infer_result_grain(q) == "district"
    except Exception:  # noqa: BLE001
        return bool(re.search(r"哪些行政区|各区平均|按行政区", q))


def is_grouping_executor_error(error_message: str | None) -> bool:
    return bool(_GROUPING_ERROR_RE.search(error_message or ""))


def needs_district_period_refine(question: str, bad_sql: str, error_message: str | None) -> bool:
    """区级问句，或坏 SQL/报错呈现区级周期错误形态时，注入 CTE 重写指引。"""
    if is_district_grain_question(question):
        return True
    if is_grouping_executor_error(error_message) and _BAD_DISTRICT_SQL_RE.search(bad_sql or ""):
        return True
    return False


def district_period_prompt_block(*, include_skeleton: bool = True) -> str:
    if include_skeleton:
        return f"{DISTRICT_PERIOD_AGG_RULES}\n\n{DISTRICT_PERIOD_AGG_MIN_SKELETON}"
    return DISTRICT_PERIOD_AGG_RULES


def district_period_rag_snippet() -> str:
    """注入 NL2SQL RAG 上下文的高优先级 QA/biz 正例文本。"""
    return (
        "【nl2sql_qa_examples·区均周期沉降正例】问句：哪些行政区近期沉降相对偏大？\n"
        + district_period_prompt_block(include_skeleton=True)
    )
