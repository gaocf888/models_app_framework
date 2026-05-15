"""智能客服 NL2SQL 分支：将 SQL 与结果行整理为自然语言回答。"""

from __future__ import annotations

from typing import Any, List

from app.core.logging import get_logger

logger = get_logger(__name__)

_MAX_ROWS_IN_PROMPT = 80


def _row_to_mapping(row: Any) -> dict[str, Any]:
    if isinstance(row, dict):
        return dict(row)
    if isinstance(row, (list, tuple)):
        return {f"列{i + 1}": v for i, v in enumerate(row)}
    return {"值": row}


def _markdown_escape_cell(val: Any) -> str:
    if val is None:
        return ""
    s = str(val).replace("\r\n", "\n").replace("\r", "\n")
    s = s.replace("|", "\\|").replace("\n", "<br>")
    return s


def _rows_to_markdown_table(slice_rows: List[Any], *, total_row_count: int) -> str:
    """
    将查询结果行渲染为 GFM 风格 Markdown 表格（无前言、无 SQL 块）。
    列顺序：首行字段顺序优先，后续行出现的新字段依次追加在表尾。
    """
    dict_rows = [_row_to_mapping(r) for r in slice_rows]
    col_order: list[str] = []
    seen: set[str] = set()
    for dr in dict_rows:
        for k in dr.keys():
            if k not in seen:
                seen.add(k)
                col_order.append(str(k))
    for dr in dict_rows:
        for k in dr.keys():
            sk = str(k)
            if sk not in seen:
                seen.add(sk)
                col_order.append(sk)
    # 上面第二轮实际与首轮重复；仅依赖 dict 插入序时首行已够。补全可能缺失的非字符串键：
    for dr in dict_rows:
        for k in dr.keys():
            sk = str(k)
            if sk not in seen:
                seen.add(sk)
                col_order.append(sk)

    header = "| " + " | ".join(_markdown_escape_cell(c) for c in col_order) + " |"
    sep = "| " + " | ".join("---" for _ in col_order) + " |"
    body: list[str] = []
    for dr in dict_rows:
        line = "| " + " | ".join(_markdown_escape_cell(dr.get(c)) for c in col_order) + " |"
        body.append(line)
    out = "\n".join([header, sep, *body])
    if total_row_count > len(slice_rows):
        out += f"\n\n> 共 {total_row_count} 行，以下展示前 {len(slice_rows)} 行。"
    return out


async def summarize_nl2sql_with_llm(
    llm_client: Any,
    *,
    user_query: str,
    sql: str,
    rows: List[dict],
) -> str:
    """智能客服 NL2SQL 结果整理：有数据时仅 Markdown 表（可带行数说明）；无 SQL/无行等保持原用户文案逻辑。"""
    _ = llm_client  # 保留参数以兼容既有调用方；有数据路径不再调用 LLM。

    sql = (sql or "").strip()
    if not sql:
        logger.info(
            "智能客服 NL2SQL：未生成有效 SQL（仅日志）。用户问题摘要=%s",
            (user_query or "")[:400],
        )
        return "未能生成有效的 SQL 查询。请换一种方式描述要查的台账或记录条件，或改用知识库问答。"

    slice_rows = rows[:_MAX_ROWS_IN_PROMPT]

    if not slice_rows:
        logger.info(
            "智能客服 NL2SQL：查询已执行但无数据行（仅日志）。用户问题摘要=%s\n本次生成用 SQL=\n%s",
            (user_query or "")[:400],
            sql[:8000] + ("..." if len(sql) > 8000 else ""),
        )
        return (
            "查询已执行，当前条件下没有返回数据行。\n\n"
            f"```sql\n{sql}\n```\n\n"
            "若预期应有数据，请检查筛选条件或确认业务库是否已同步。"
        )

    out = _rows_to_markdown_table(slice_rows, total_row_count=len(rows))
    logger.info(
        "智能客服 NL2SQL：已向用户返回 Markdown 表格（总行数=%s，本表展示行数=%s）。用户问题摘要=%s",
        len(rows),
        len(slice_rows),
        (user_query or "")[:400],
    )
    logger.info(
        "智能客服 NL2SQL：本次查询使用的 SQL（仅日志，不写入用户可见内容）\n%s",
        sql[:8000] + ("..." if len(sql) > 8000 else ""),
    )
    return out
