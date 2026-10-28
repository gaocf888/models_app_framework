from app.nl2sql.chain import NL2SQLChain
from app.nl2sql.overheat_q5_rewrite import rewrite_overheat_q5_dim_drive
from app.nl2sql.validator import SQLValidator

_OLD_Q5_SQL = """
SELECT
  CASE
    WHEN UPPER(spd.tag) LIKE '%FW%'
      OR IFNULL(pi.pi_name_ch, '') LIKE '%减温水%' THEN '减温水'
    WHEN UPPER(spd.tag) LIKE '%O2%' THEN '氧量'
    ELSE '其他关联参数'
  END AS 参数类型,
  spd.tag AS 测点编码,
  MAX(IFNULL(pi.pi_name_ch, spd.tag)) AS 测点名称,
  COUNT(*) AS 采样点数
FROM sis_pi_data spd
LEFT JOIN base_dev_pi_b pi ON pi.pi_code = spd.tag
INNER JOIN (
  SELECT
    MIN(DATE_SUB(t.start_time, INTERVAL 30 MINUTE)) AS evt_min,
    MAX(DATE_ADD(IFNULL(t.end_time, t.start_time), INTERVAL 30 MINUTE)) AS evt_max
  FROM monitor_hotarea_temp t
  INNER JOIN account_boiler ab ON t.boiler_id = ab.boiler_id
  WHERE t.start_time >= DATE_SUB(CURDATE(), INTERVAL 2 DAY)
    AND t.start_time < DATE_SUB(CURDATE(), INTERVAL 1 DAY)
    AND t.highest_temp > t.limit_temp
    AND ab.boiler_name LIKE CONCAT('%', '1号锅炉', '%')
) evt ON spd.data_time >= evt.evt_min AND spd.data_time <= evt.evt_max
WHERE NOT (
  UPPER(spd.tag) LIKE '%CT%'
  AND UPPER(spd.tag) LIKE '%HAD%'
)
AND (
  UPPER(spd.tag) LIKE '%FW%'
  OR UPPER(spd.tag) LIKE '%O2%'
  OR IFNULL(pi.pi_name_ch, '') LIKE '%减温水%'
)
GROUP BY 参数类型, spd.tag
HAVING 参数类型 <> '其他关联参数'
ORDER BY 参数类型, 采样点数 DESC, 测点编码
"""


def test_rewrites_fact_table_like_scan_to_dim_drive() -> None:
    sql, notes = rewrite_overheat_q5_dim_drive(
        _OLD_Q5_SQL,
        plan_item_id="q5",
        analysis_type="overheat_guidance",
    )
    assert notes == ["overheat_q5_dim_drive", "overheat_q5_sargable_time"]
    assert "UPPER(spd.tag) LIKE" not in sql
    assert "INNER JOIN sis_pi_data spd ON spd.tag = tags.pi_code" in sql
    assert "FROM base_dev_pi_b pi" in sql
    assert "FROM sis_pi_data spd" not in sql.split("INNER JOIN sis_pi_data", 1)[0]
    assert "DATE_SUB(CURDATE(), INTERVAL 2 DAY)" in sql
    assert "ab.boiler_name LIKE CONCAT('%', '1号锅炉', '%')" in sql
    assert "DATE_SUB((DATE_SUB(CURDATE(), INTERVAL 2 DAY)), INTERVAL 30 MINUTE)" in sql
    assert "DATE_ADD((DATE_SUB(CURDATE(), INTERVAL 1 DAY)), INTERVAL 30 MINUTE)" not in sql
    assert "HAVING 参数类型" not in sql
    assert "WHERE tags.参数类型 <> '其他关联参数'" in sql
    assert "GROUP BY tags.参数类型, spd.tag" in sql


def test_already_dim_driven_sql_is_noop() -> None:
    first, _ = rewrite_overheat_q5_dim_drive(
        _OLD_Q5_SQL,
        plan_item_id="q5",
        analysis_type="overheat_guidance",
    )
    second, notes = rewrite_overheat_q5_dim_drive(
        first,
        plan_item_id="q5",
        analysis_type="overheat_guidance",
    )
    assert notes == []
    assert second == first


def test_skips_other_plan_items_and_analysis_types() -> None:
    sql, notes = rewrite_overheat_q5_dim_drive(
        _OLD_Q5_SQL,
        plan_item_id="q1",
        analysis_type="overheat_guidance",
    )
    assert notes == []
    assert sql == _OLD_Q5_SQL

    sql, notes = rewrite_overheat_q5_dim_drive(
        _OLD_Q5_SQL,
        plan_item_id="q5",
        analysis_type="img_diag_leakage_burst",
    )
    assert notes == []
    assert sql == _OLD_Q5_SQL


def test_placeholder_evt_window_becomes_sargable_bound() -> None:
    sql = _OLD_Q5_SQL.replace(
        "DATE_SUB(CURDATE(), INTERVAL 2 DAY)",
        "@t_start",
    ).replace(
        "DATE_SUB(CURDATE(), INTERVAL 1 DAY)",
        "@t_end",
    )
    out, notes = rewrite_overheat_q5_dim_drive(
        sql,
        plan_item_id="q5",
        analysis_type="overheat_guidance",
    )
    assert "overheat_q5_sargable_time" in notes
    assert "spd.data_time >= DATE_SUB((@t_start), INTERVAL 30 MINUTE)" in out
    assert "spd.data_time < DATE_ADD((@t_end), INTERVAL 30 MINUTE)" not in out
    assert "t.start_time >= @t_start" in out
    assert "t.start_time < @t_end" in out


def test_query_filters_hook_rewrites_overheat_q5() -> None:
    chain = object.__new__(NL2SQLChain)
    chain._validator = SQLValidator()
    rewritten, notes = chain._rewrite_query_filters(
        _OLD_Q5_SQL,
        question="请帮我分析1号锅炉前天的超温情况",
        plan_item_id="q5",
        analysis_type="overheat_guidance",
    )
    assert "overheat_q5_dim_drive" in notes
    assert "UPPER(spd.tag) LIKE" not in rewritten
    assert "INNER JOIN sis_pi_data spd ON spd.tag = tags.pi_code" in rewritten
