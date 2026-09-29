from __future__ import annotations

from typing import Any

from app.nl2sql.intent_config import (
    inject_parsed_intent_enabled,
    response_include_parsed_intent,
    trace_include_question_intent,
)
from app.nl2sql.question_scope_models import QuestionIntent
from app.nl2sql.time_intent_display import resolve_statistical_time_range_display

__all__ = [
    "format_parsed_intent_prompt_block",
    "inject_parsed_intent_enabled",
    "question_intent_to_dict",
    "response_include_parsed_intent",
    "trace_include_question_intent",
]


def question_intent_to_dict(intent: QuestionIntent) -> dict[str, Any]:
    """结构化问句意图 JSON 可序列化 dict（trace / API / 日志）。"""
    scope = intent.scope
    time_window: dict[str, str] | None = None
    if intent.time_window is not None:
        start, end, tag = intent.time_window
        time_window = {"start_expr": start, "end_expr": end, "tag": tag}
    time_anchor: dict[str, str] | None = None
    if intent.time_anchor is not None:
        end_expr, tag = intent.time_anchor
        time_anchor = {"end_expr": end_expr, "tag": tag}
    stat_range = resolve_statistical_time_range_display(intent.scope_question)
    return {
        "parse_mode": intent.parse_mode,
        "scope_question": intent.scope_question,
        "time_window_tag": intent.time_window_tag,
        "time_window": time_window,
        "time_anchor": time_anchor,
        "time_anchor_tag": intent.time_anchor_tag,
        "statistical_time_range": {"start": stat_range[0], "end": stat_range[1]},
        "scope": {
            "boiler": scope.boiler,
            "device_name": scope.device_name,
            "check_location_name": scope.check_location_name,
            "piperow_name": scope.piperow_name,
            "row_no": scope.row_no,
            "tube_no": scope.tube_no,
            "station_id": scope.station_id,
            "station_name": scope.station_name,
            "district": scope.district,
            "device_type": scope.device_type,
        },
    }


def merge_parsed_intent_extensions(
    base: dict[str, Any],
    *,
    semantic: dict[str, Any] | None = None,
    linked_schema: dict[str, Any] | None = None,
    gen_fail_reason: str | None = None,
) -> dict[str, Any]:
    out = dict(base)
    if semantic is not None:
        out["semantic"] = semantic
    if linked_schema is not None:
        out["linked_schema"] = linked_schema
    if gen_fail_reason:
        out["gen_fail_reason"] = gen_fail_reason
    return out


def format_parsed_intent_prompt_block(
    intent: QuestionIntent,
    *,
    semantic: dict[str, Any] | None = None,
    linked_schema: dict[str, Any] | None = None,
) -> str:
    """供 NL2SQL SQL 生成 Prompt 追加的「已识别问句意图」块。"""
    from app.nl2sql.latest_per_station import (
        LATEST_PER_STATION_TAG,
        format_latest_per_station_prompt_rule,
        resolve_latest_grain,
    )

    lines = ["【已识别问句意图】"]

    stat = resolve_statistical_time_range_display(intent.scope_question)
    tag = intent.time_window_tag or "yesterday_fallback"
    if tag == LATEST_PER_STATION_TAG:
        lines.append(f"- 时间窗：{tag}（{stat[0]}；勿写日历日窗）")
    else:
        lines.append(f"- 时间窗：{tag}（{stat[0]} ~ {stat[1]}）")

    if intent.time_anchor is not None:
        _end, anchor_tag = intent.time_anchor
        lines.append(f"- 事故锚点：{anchor_tag}（plan 含「锚点向前*天」时用于 SQL 回溯窗上界）")
    else:
        lines.append("- 事故锚点：未识别")

    scope = intent.scope
    from app.nl2sql.intent_config import business_domain

    if business_domain() == "subsidence":
        lines.append(f"- 行政区：{scope.district or '未指定'}")
        if scope.station_name:
            lines.append(f"- 监测站点：{scope.station_name}")
        if scope.station_id:
            lines.append(f"- 站点ID：{scope.station_id}")
        if scope.device_type:
            lines.append(f"- 监测类型：{scope.device_type}")
    else:
        lines.append(f"- 锅炉：{scope.boiler or '全厂/未指定'}")
        if scope.device_name:
            lines.append(f"- 受热面：{scope.device_name}")
        if scope.piperow_name:
            lines.append(f"- 管排：{scope.piperow_name}")
        if scope.row_no is not None:
            lines.append(f"- 排数：{scope.row_no}")
        if scope.tube_no is not None:
            lines.append(f"- 管数：{scope.tube_no}")

    if semantic:
        sem_ver = semantic.get("version") or "unknown"
        lines.append(f"- 语义版本：{sem_ver}")
        metrics = semantic.get("metrics") or []
        if metrics:
            metric_bits = []
            for m in metrics[:5]:
                if not isinstance(m, dict):
                    continue
                mid = m.get("id") or ""
                name = m.get("name") or mid
                unit = m.get("unit") or ""
                cols = m.get("preferred_columns") or []
                tbls = m.get("preferred_tables") or []
                col_hint = ",".join(cols[:2]) if cols else ""
                tbl_hint = ",".join(tbls[:2]) if tbls else ""
                metric_bits.append(f"{name}({mid},{unit}→{tbl_hint}.{col_hint})")
            if metric_bits:
                lines.append(f"- 命中指标：{'；'.join(metric_bits)}")
        dims = semantic.get("dimensions") if isinstance(semantic.get("dimensions"), dict) else {}
        project_names = list((dims or {}).get("project_names") or [])
        coverage_names = list((dims or {}).get("device_coverage_project_names") or [])
        preferred_marks = list((dims or {}).get("preferred_station_names") or [])
        compress_pairs = list((dims or {}).get("compress_pairs") or [])
        if project_names:
            lines.append(
                f"- 用户点名站点场地(project_name)：共{len(project_names)}个"
                f"（示例：{'、'.join(str(x) for x in project_names[:3])}）；"
                "完整名单由系统按 suggested_filters 注入，生成 SQL 时可省略该谓词，"
                "禁止写 IN (SELECT …) 凑名单或只写预览子集"
            )
        elif coverage_names:
            lines.append(
                f"- 监测方式官方站点覆盖：共{len(coverage_names)}个 project_name；"
                "生成 SQL 时可省略 project_name/station_name 谓词，由系统按 suggested_filters 强制注入完整字面量；"
                "禁止写 IN (SELECT …) 或截断 IN 名单"
            )
        if compress_pairs:
            bits = []
            for p in compress_pairs[:4]:
                if not isinstance(p, dict):
                    continue
                bits.append(
                    f"{p.get('project_name')} 层位{p.get('layer_from')}→{p.get('layer_to')} "
                    f"({p.get('mark_from')}/{p.get('mark_to')})"
                )
            more = f" 等共{len(compress_pairs)}对" if len(compress_pairs) > 4 else ""
            lines.append(
                f"- 压缩层边界标：{'；'.join(bits)}{more}；"
                "公式 compress(i→i+1)=Δ(i)−Δ(i+1)，非单标累计当层沉降"
            )
        if preferred_marks:
            hint = (
                "压缩层边界 station_name"
                if compress_pairs
                else "优选标编号(station_name，站点沉降用层位0)"
            )
            lines.append(
                f"- {hint}：共{len(preferred_marks)}个；"
                "完整名单由系统按 suggested_filters 注入，生成 SQL 时可省略该谓词；"
                "禁止写 IN (SELECT …) 凑名单或只写预览子集；禁止对同 project_name 下全部标聚合"
            )
        warnings = semantic.get("warnings") or []
        if warnings:
            lines.append(f"- 语义告警：{'；'.join(str(w) for w in warnings[:5])}")

    suggested_filters: list[Any] = []
    if linked_schema:
        status = linked_schema.get("status") or "ok"
        tables = linked_schema.get("tables") or []
        if tables:
            tbl_names = []
            for t in tables[:4]:
                if isinstance(t, dict):
                    tbl_names.append(str(t.get("name") or ""))
                else:
                    tbl_names.append(str(getattr(t, "name", t)))
            tbl_names = [n for n in tbl_names if n]
            if tbl_names:
                lines.append(f"- 链接主表（{status}）：{', '.join(tbl_names)}")
        filters = linked_schema.get("suggested_filters") or []
        if isinstance(filters, list):
            suggested_filters = [f for f in filters if isinstance(f, dict)]
        if suggested_filters:
            lines.append(
                "- 链接建议过滤（权威；生成 SQL 时可省略对应谓词，"
                "禁止为 suggested_filters 写 IN (SELECT …)；系统会强制注入完整字面量）："
            )
            for f in suggested_filters:
                col = f.get("column")
                op = f.get("op")
                src = f.get("source")
                table = f.get("table") or ""
                val = f.get("value")
                if op == "in" and isinstance(val, list):
                    lines.append(
                        f"  · {table}.{col} IN (共{len(val)}个，source={src})"
                    )
                else:
                    lines.append(f"  · {table}.{col} {op} {val!r} (source={src})")
        fail_reason = linked_schema.get("fail_reason")
        if fail_reason and status == "failed":
            lines.append(f"- 链接失败原因：{fail_reason}")

    if tag == LATEST_PER_STATION_TAG:
        grain = resolve_latest_grain(suggested_filters)
        lines.append(format_latest_per_station_prompt_rule(grain=grain))

    return "\n".join(lines)
