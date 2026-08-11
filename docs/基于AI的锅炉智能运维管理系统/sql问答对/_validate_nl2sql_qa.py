# -*- coding: utf-8 -*-
"""Validate generated NL2SQL QA pairs against schema template columns."""
from __future__ import annotations

import json
import re
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent
JSONL = ROOT / "nl2sql_qa_examples_锅炉四管_一期人工样例.jsonl"

SCHEMA = {
    "account_boiler": {
        "boiler_id", "plant_id", "boiler_code", "boiler_name", "boiler_type", "boiler_describe",
        "producer", "boiler_model", "capacity", "run_date", "boiler_intro", "file", "structure",
        "fire_type", "made_date", "sort_by", "edfh", "edzfl", "fh_point", "zfl_point",
        "stop_percent", "temp_point", "press_point",
    },
    "account_static_device": {
        "device_id", "boiler_id", "device_code", "device_name", "parent_device_id", "device_descrip",
        "device_type", "device_3d_code", "device_level", "sort_by", "device_img_src",
    },
    "account_device_piperow": {
        "id", "device_id", "piperow_name", "transverse_pitch", "longitudinal_pitch", "area",
        "row_count", "pipe_count", "model", "model_name", "actual_width", "actual_height",
        "piperow_diameter", "piperow_thickness", "piperow_spacing", "fins_thickness", "fins_width",
        "tilt_angle", "coordinate", "remark", "sort_by",
    },
    "account_device_pipebox": {
        "id", "device_id", "pipebox_name", "design_pressure", "design_temp", "work_pressure",
        "experiment_pressure", "model", "remark", "pipebox_type", "sort_by",
    },
    "account_device_weld": {
        "id", "device_id", "weld_name", "weld_count", "weld_type", "weld_model_front",
        "weld_model_end", "weld_location", "remark", "sort_by", "device_3d_code",
    },
    "base_archives": {
        "id", "device_id", "overhaul_id", "boiler_id", "file_name", "file_id", "file_catalogue",
        "file_type", "status", "view_count", "article_year", "create_dept", "create_by",
        "create_time", "update_by", "update_time",
    },
    "base_temp_device": {
        "id", "device_id", "sort_by", "boiler_id", "over_hot_limit", "status", "pipe_count",
        "row_count", "chart_type", "chart_position", "chrat_model", "direction",
        "temp_limit_pressure", "row_sort", "pipe_sort", "model_id", "header_point_id",
        "length_sczle", "change_second", "change_temperature",
    },
    "base_temp_point": {
        "id", "point_id", "point_code", "point_name", "device_id", "row_num", "pipe_num",
        "pipe_sort", "row_sort", "sort_by",
    },
    "monitor_hotarea_temp": {
        "id", "pi_code", "start_time", "end_time", "highest_temp", "limit_temp", "limit_duration",
        "create_time", "number", "mw_value", "steam_pressure_value", "boiler_id", "device_id",
    },
    "overhaul_boiler": {
        "overhaul_id", "boiler_id", "overhaul_name", "overhaul_level", "begin_date", "end_date",
        "overhaul_year", "status", "reserve1", "reserve2", "create_by", "create_time",
        "update_by", "update_time", "overhaul_num", "defect_num", "tubchage_num", "legacy_defect_num",
    },
    "overhaul_new_checklocation": {
        "id", "parent_id", "boiler_id", "device_id", "name", "hole_type", "type", "code",
        "row_count", "wall_thickness", "wall_thickness_limit", "out_thickness", "out_thickness_limit",
        "wall_thickness_rate", "out_thickness_rate", "del_flag", "create_by", "create_time",
        "update_by", "update_time", "row_pitch", "pipe_pitch", "direction", "model_info",
    },
    "overhaul_record": {
        "id", "overhaul_id", "device_id", "check_id", "mark_type", "defect_type", "mark_area",
        "row_num", "hole_code", "mark_time", "file_path", "create_by", "create_time",
        "update_by", "update_time", "del_flag", "reserve1", "reserve2", "data_type",
    },
    "overhaul_record_tubes": {
        "tube_id", "overhaul_record_id", "name", "tube_code", "tube_position", "tube_path",
        "is_change", "start", "end", "length", "thickness", "image", "create_by", "create_time",
        "update_by", "update_time", "reserve1", "reserve2", "location_id",
    },
    "overhaul_thickness_rate": {
        "id", "boiler_id", "device_id", "location_id", "overhaul_id", "row_num", "pipe_num",
        "check_type", "wall_thickness", "wall_thickness_limit", "wall_thickness_measure",
        "wall_thickness_rate", "out_thickness", "out_thickness_limit", "out_thickness_measure",
        "out_thickness_rate", "residual_life", "last_measure_date",
    },
    "monitor_boiler_start_stop": {
        "id", "boiler_id", "start_date", "stop_date", "status", "stop_reason", "sort_by",
        "create_by", "create_time", "update_by", "update_time",
    },
    "base_soot_blower": {
        "blower_id", "boiler_id", "device_id", "pi_code", "blower_name", "blower_code",
        "blower_type", "pipe_nums", "direction", "model", "location", "install_date",
        "putinto_date", "production_plant", "create_by", "create_time", "update_by", "update_time",
    },
    "monitor_soot_blower_run_record": {
        "id", "blower_id", "start_time", "end_time", "blowing_duration", "create_time", "current_a",
    },
    "base_coal_mill": {
        "mill_id", "boiler_id", "pi_code", "mill_name", "mill_code", "mill_type", "location",
        "install_date", "putinto_date", "production_plant", "create_by", "create_time",
        "update_by", "update_time",
    },
    "monitor_coal_mill_run_record": {
        "id", "mill_id", "record_time", "current_a", "coal_flow_tonh", "primary_air_flow",
        "boiler_mw", "type",
    },
    "account_group": {"id", "parent_id", "group_name", "group_type", "group_duty", "sort_by", "remark"},
    "account_group_member": {
        "id", "group_id", "member_role", "member_name", "telephone", "member_photo",
        "member_station", "member_duty", "remark", "sort_by", "parent_id",
    },
    "overhual_leakage": {
        "id", "boiler_id", "overhaul_id", "device_id", "check_date", "leakage_date",
        "leakage_descrip", "row_num", "pipe_num", "relative_pipe_num", "soot_blower_id",
        "leakage_reason", "handling_method", "reason_type", "is_abnormal_stop", "file_url",
        "file_name", "create_by", "create_time", "update_by", "update_time", "mark_info",
    },
}

SQL_FUNCS = {
    "select", "from", "where", "and", "or", "on", "as", "join", "inner", "left", "right",
    "group", "by", "order", "limit", "case", "when", "then", "else", "end", "asc", "desc",
    "count", "sum", "avg", "max", "min", "round", "ifnull", "nullif", "concat", "date",
    "date_format", "date_sub", "date_add", "curdate", "now", "year", "weekday", "interval",
    "day", "month", "hour", "like", "in", "is", "not", "null", "between", "distinct",
    "exists", "having", "union", "all", "any", "true", "false",
}


def parse_alias_map(sql: str) -> dict[str, str]:
    amap: dict[str, str] = {}
    for m in re.finditer(
        r"(?:FROM|JOIN)\s+([a-zA-Z_][a-zA-Z0-9_]*)\s+(?:AS\s+)?([a-zA-Z_][a-zA-Z0-9_]*)\b",
        sql,
        re.I,
    ):
        amap[m.group(2)] = m.group(1).lower()
    # subquery aliases: FROM ( ... ) pt
    for m in re.finditer(r"\)\s*([a-zA-Z_][a-zA-Z0-9_]*)\b", sql):
        alias = m.group(1)
        if alias.lower() not in SQL_FUNCS and alias not in amap:
            amap[alias] = "__subquery__"
    return amap


def check_balance(sql: str) -> str | None:
    if sql.count("(") != sql.count(")"):
        return f"paren imbalance ({sql.count('(')} vs {sql.count(')')})"
    if sql.count("'") % 2 != 0:
        return "odd single quotes"
    return None


def main() -> None:
    rows = [json.loads(l) for l in JSONL.open(encoding="utf-8") if l.strip()]
    issues: list[tuple[str, str, str, str]] = []
    logic: list[tuple[str, str, str]] = []

    for r in rows:
        rid, q, sql = r["id"], r["question"], r["sql"]
        bal = check_balance(sql)
        if bal:
            issues.append((rid, "syntax", bal, q))
        if re.search(r"\bWITH\b", sql, re.I):
            issues.append((rid, "policy", "WITH/CTE not preferred", q))
        if ";" in sql.rstrip(";"):
            issues.append((rid, "syntax", "multiple statements / mid semicolon", q))

        amap = parse_alias_map(sql)
        tables = {t for t in amap.values() if t != "__subquery__"}
        for t in tables:
            if t not in SCHEMA:
                issues.append((rid, "schema", f"unknown table {t}", q))

        for m in re.finditer(r"\b([a-zA-Z_][a-zA-Z0-9_]*)\.([a-zA-Z_][a-zA-Z0-9_]*)\b", sql):
            a, c = m.group(1), m.group(2)
            if a not in amap:
                # bare table.col?
                if a.lower() in SCHEMA and c not in SCHEMA[a.lower()]:
                    issues.append((rid, "schema", f"unknown column {a}.{c}", q))
                continue
            tbl = amap[a]
            if tbl == "__subquery__":
                continue
            if tbl in SCHEMA and c not in SCHEMA[tbl]:
                issues.append((rid, "schema", f"unknown column {a}.{c} on {tbl}", q))

        # intent / time window checks
        if "前天" in q and "INTERVAL 2 DAY" not in sql.upper().replace("  ", " "):
            if "DATE_SUB(CURDATE(), INTERVAL 2 DAY)" not in sql.replace(" ", "").upper().replace("INTERVAL2DAY", "INTERVAL 2 DAY"):
                # normalize spaces
                compact = re.sub(r"\s+", " ", sql.upper())
                if "INTERVAL 2 DAY" not in compact:
                    logic.append((rid, "前天应使用 INTERVAL 2 DAY 窗口", q))
        if "上月" in q:
            compact = re.sub(r"\s+", " ", sql.upper())
            if "INTERVAL 1 MONTH" not in compact and "DATE_FORMAT(DATE_SUB" not in compact:
                logic.append((rid, "上月时间窗可疑", q))
        if ("号炉" in q or "号机组" in q or "一号" in q) and "号锅炉" not in sql and "boiler_name" in sql:
            # oral question should still filter 号锅炉 in SQL - good if present
            if "LIKE '%" not in sql and "boiler_name" in sql:
                logic.append((rid, "口语机组问法但 SQL 过滤可能不足", q))
        if "非停" in q and "is_abnormal_stop" not in sql:
            logic.append((rid, "非停意图未过滤 is_abnormal_stop", q))
        if "换管" in q and "is_change" not in sql and "tubchage_num" not in sql and "换管" not in sql:
            logic.append((rid, "换管意图字段可能缺失", q))

        # risky: ORDER BY Chinese alias after GROUP BY - usually OK in MySQL/TiDB
        # risky nested MAX without correlating boiler for 最近一次检修发现了多少缺陷
        if "最近一次检修发现了多少缺陷" in q and "GROUP BY boiler_id" not in sql:
            logic.append(
                (
                    rid,
                    "最近一次检修未按锅炉取 MAX(begin_date)，语义可能偏差",
                    q,
                )
            )

        # correlated subquery 换管明细: MAX begin_date per 1号锅炉 - OK
        # DATE_SUB(CURDATE(), INTERVAL 1 DAY) + INTERVAL 20 HOUR - valid in MySQL
        # CURDATE() + INTERVAL 1 DAY - valid

        # GROUP BY incomplete: select non-agg cols not in group by
        # Heuristic skip - hard

    # print summary
    by = defaultdict(list)
    for item in issues:
        by[item[1]].append(item)
    print(f"total_pairs={len(rows)}")
    print(f"schema_syntax_issues={len(issues)}")
    print(f"logic_notes={len(logic)}")
    for kind, items in by.items():
        print(f"\n== {kind} ({len(items)}) ==")
        for it in items:
            print(f"- {it[0]}: {it[2]} | {it[3][:48]}")
    if logic:
        print("\n== logic ==")
        for it in logic:
            print(f"- {it[0]}: {it[1]} | {it[2][:48]}")

    # try sqlparse if available
    try:
        import sqlparse  # type: ignore

        parse_fail = 0
        for r in rows:
            try:
                list(sqlparse.parse(r["sql"]))
            except Exception as exc:  # noqa: BLE001
                parse_fail += 1
                print("sqlparse fail", r["id"], exc)
        print(f"\nsqlparse_ok={len(rows) - parse_fail}/{len(rows)}")
    except ImportError:
        print("\nsqlparse not installed; skipped")


if __name__ == "__main__":
    main()
