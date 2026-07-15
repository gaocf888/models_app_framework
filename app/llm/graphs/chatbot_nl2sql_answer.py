"""智能客服 NL2SQL 分支：将 SQL 与结果行整理为自然语言回答。"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any, List

from app.core.logging import get_logger
from app.llm.graphs.chatbot_nl2sql_display import (
    CHATBOT_NL2SQL_SELECT_DISPLAY_RULES,
    filter_chatbot_nl2sql_display_rows,
)
from app.models.nl2sql import NL2SQLQueryRequest
from app.nl2sql.errors import NL2SQLExecutionError
from app.services.nl2sql_service import NL2SQLService

logger = get_logger(__name__)

_MAX_ROWS_IN_PROMPT = 80

_DEFAULT_USER_ERROR_MESSAGE = (
    "暂时无法完成本次数据查询，请尝试缩小范围（如指定锅炉、时间）或改用知识库问答。"
    "若问题持续，请联系管理员并提供提问时间。"
)

_USER_ERROR_MESSAGES: dict[str, str] = {
    "unknown_column": (
        "暂时无法完成本次数据查询（查询字段配置异常，已记录）。"
        "请尝试缩小查询范围或改用知识库问答。"
    ),
    "unknown_table": (
        "暂时无法完成本次数据查询（相关数据表未配置或不可用，已记录）。"
        "请尝试缩小查询范围或改用知识库问答。"
    ),
    "sql_syntax_error": (
        "暂时无法完成本次数据查询（查询语句未能正确生成，已记录）。"
        "请换一种方式描述要查的台账或记录条件。"
    ),
    "db_access_denied": (
        "暂时无法完成本次数据查询（数据访问权限异常，已记录）。请联系管理员。"
    ),
    "default": _DEFAULT_USER_ERROR_MESSAGE,
}


def _chatbot_expose_nl2sql_sql_in_meta() -> bool:
    return os.getenv("CHATBOT_EXPOSE_NL2SQL_SQL_IN_META", "false").lower() == "true"


def format_nl2sql_user_error(exc: NL2SQLExecutionError | None = None) -> str:
    """将 NL2SQL 执行失败映射为客服用户可见文案（无 SQL、无堆栈）。"""
    if exc is None:
        return _DEFAULT_USER_ERROR_MESSAGE
    key = exc.user_message_key if exc.user_message_key in _USER_ERROR_MESSAGES else "default"
    return _USER_ERROR_MESSAGES.get(key, _DEFAULT_USER_ERROR_MESSAGE)


@dataclass
class ChatbotNL2SQLOutcome:
    answer_text: str
    nl2sql_sql: str | None = None
    nl2sql_failed: bool = False
    nl2sql_error_code: str | None = None
    terminate_reason: str | None = None


async def run_chatbot_nl2sql_query(
    nl2sql: NL2SQLService,
    llm_client: Any,
    *,
    user_id: str,
    session_id: str,
    question: str,
) -> ChatbotNL2SQLOutcome:
    """智能客服 NL2SQL 统一入口：成功则整理结果，失败则友好文案且不向上抛异常。"""
    req = NL2SQLQueryRequest(
        user_id=user_id,
        session_id=session_id,
        question=question,
        sql_gen_extra_hint=CHATBOT_NL2SQL_SELECT_DISPLAY_RULES,
    )
    try:
        resp = await nl2sql.query(req, record_conversation=False)
        text = await summarize_nl2sql_with_llm(
            llm_client,
            user_query=question,
            sql=resp.sql,
            rows=list(resp.rows or []),
        )
        return ChatbotNL2SQLOutcome(answer_text=text, nl2sql_sql=resp.sql or None)
    except NL2SQLExecutionError as exc:
        logger.warning(
            "智能客服 NL2SQL 执行失败 error_code=%s question=%s detail=%s",
            exc.error_code,
            (question or "")[:400],
            exc.log_detail(),
        )
        if exc.sql:
            logger.info(
                "智能客服 NL2SQL 失败 SQL（仅日志）\n%s",
                exc.sql[:8000] + ("..." if len(exc.sql) > 8000 else ""),
            )
        sql_meta = (exc.sql or None) if _chatbot_expose_nl2sql_sql_in_meta() else None
        return ChatbotNL2SQLOutcome(
            answer_text=format_nl2sql_user_error(exc),
            nl2sql_sql=sql_meta,
            nl2sql_failed=True,
            nl2sql_error_code=exc.error_code,
            terminate_reason="nl2sql_exec_failed",
        )
    except RuntimeError as exc:
        if "SQL execution failed" not in str(exc):
            raise
        logger.warning(
            "智能客服 NL2SQL 执行失败（兼容 RuntimeError） question=%s err=%s",
            (question or "")[:400],
            str(exc)[:240],
        )
        return ChatbotNL2SQLOutcome(
            answer_text=format_nl2sql_user_error(),
            nl2sql_failed=True,
            nl2sql_error_code="sql_exec_failed",
            terminate_reason="nl2sql_exec_failed",
        )


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
    """智能客服 NL2SQL 结果整理：有数据时仅 Markdown 表；无行/无 SQL 时仅用户文案（SQL 由 SSE finished.meta.nl2sql_sql 下发，不写进 delta）。"""
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
            "若预期应有数据，请检查筛选条件或确认业务库是否已同步。"
        )

    display_rows = filter_chatbot_nl2sql_display_rows(
        [_row_to_mapping(r) for r in slice_rows]
    )
    out = _rows_to_markdown_table(display_rows, total_row_count=len(rows))
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
