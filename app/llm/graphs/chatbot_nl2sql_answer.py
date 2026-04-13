"""智能客服 NL2SQL 分支：将 SQL 与结果行整理为自然语言回答。"""

from __future__ import annotations

import json
from typing import Any, List

from app.core.logging import get_logger

logger = get_logger(__name__)

_MAX_ROWS_IN_PROMPT = 80
_MAX_JSON_CHARS = 14000


async def summarize_nl2sql_with_llm(
    llm_client: Any,
    *,
    user_query: str,
    sql: str,
    rows: List[dict],
) -> str:
    sql = (sql or "").strip()
    if not sql:
        return "未能生成有效的 SQL 查询。请换一种方式描述要查的台账或记录条件，或改用知识库问答。"

    slice_rows = rows[:_MAX_ROWS_IN_PROMPT]
    try:
        payload = json.dumps(slice_rows, ensure_ascii=False)
    except TypeError:
        payload = str(slice_rows)
    if len(payload) > _MAX_JSON_CHARS:
        payload = payload[:_MAX_JSON_CHARS] + "…（结果已截断）"

    if not slice_rows:
        return (
            "查询已执行，当前条件下没有返回数据行。\n\n"
            f"```sql\n{sql}\n```\n\n"
            "若预期应有数据，请检查筛选条件或确认业务库是否已同步。"
        )

    sys_msg = (
        "你是电厂数据助手。用户通过自然语言查库，你根据 SQL 与结果行用简洁中文总结："
        "先给结论（条数或要点），再列关键字段；表格感强时可简要分点。"
        "不要编造结果中没有的字段；若结果过多说明仅展示部分。"
    )
    user_msg = f"用户问题：{user_query}\n\nSQL：\n{sql}\n\n结果（JSON 数组）：\n{payload}"

    try:
        text = await llm_client.chat(
            model=None,
            messages=[
                {"role": "system", "content": sys_msg},
                {"role": "user", "content": user_msg},
            ],
        )
        out = (text or "").strip()
        if out:
            return out
    except Exception as exc:  # noqa: BLE001
        logger.warning("nl2sql naturalize LLM failed: %s", exc)

    # 降级：纯文本列举前几行
    lines = [f"共 {len(rows)} 行（展示前 {len(slice_rows)} 行）："]
    for i, r in enumerate(slice_rows[:10], 1):
        lines.append(f"{i}. {r}")
    lines.append(f"\n```sql\n{sql}\n```")
    return "\n".join(lines)
