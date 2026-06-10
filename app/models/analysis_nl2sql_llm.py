from __future__ import annotations

"""
综合分析 nl2sql 路径下「意图 / 数据计划」两阶段 LLM 的结构化输出模型与 JSON 抽取工具。

与 `configs/prompts_bak_new.yaml` 中 `analysis_intent`、`analysis_data_plan` 提示词配合使用。
"""

import json
import re
from typing import Any

from pydantic import BaseModel, Field, field_validator


def _slug_item_id(raw: str) -> str:
    """将 LLM 给出的 item_id 规范为安全短字符串。"""
    s = re.sub(r"[^a-zA-Z0-9_-]+", "_", (raw or "").strip())[:64].strip("_")
    return s or "llm_task"


class AnalysisIntentLLMOutput(BaseModel):
    """综合分析 NL2SQL 路径：意图阶段结构化输出（与 prompts 中 JSON 约定一致）。"""

    goals: list[str] = Field(default_factory=list, max_length=12)
    key_entities: list[str] = Field(default_factory=list, max_length=24)
    time_scope_hint: str = Field("", max_length=500)
    output_focus: list[str] = Field(default_factory=list, max_length=12)
    data_domains: list[str] = Field(default_factory=list, max_length=16)
    # 检修策略专项（maintenance_strategy intent 模板输出；其它 analysis_type 可留空）
    response_mode: str = Field(
        "",
        max_length=32,
        description="FULL|SCOPED|WINDOW|FEASIBILITY|RUN_ADVICE",
    )
    scope_devices: list[str] = Field(default_factory=list, max_length=16)
    target_window: str = Field("", max_length=300)
    focus_entities: list[str] = Field(default_factory=list, max_length=16)
    resource_hints: list[str] = Field(default_factory=list, max_length=12)

    @field_validator(
        "goals",
        "key_entities",
        "output_focus",
        "data_domains",
        "scope_devices",
        "focus_entities",
        "resource_hints",
        mode="before",
    )
    @classmethod
    def _coerce_str_list(cls, v: Any) -> list[str]:
        if v is None:
            return []
        if isinstance(v, str):
            return [v.strip()] if v.strip() else []
        if isinstance(v, list):
            return [str(x).strip() for x in v if str(x).strip()]
        return []

    @field_validator("response_mode", mode="before")
    @classmethod
    def _norm_response_mode(cls, v: Any) -> str:
        s = str(v or "").strip().upper()
        allowed = {"FULL", "SCOPED", "WINDOW", "FEASIBILITY", "RUN_ADVICE"}
        return s if s in allowed else ""


class AnalysisPlanTaskLLMItem(BaseModel):
    """LLM 数据计划单条（与 JSON 模板项字段对齐）。"""

    item_id: str = Field(..., min_length=1, max_length=64)
    purpose: str = Field(..., min_length=1, max_length=300)
    question: str = Field(..., min_length=4, max_length=4000)
    mandatory: bool = True
    dependency_ids: list[str] = Field(default_factory=list, max_length=16)

    @field_validator("item_id")
    @classmethod
    def _norm_id(cls, v: str) -> str:
        return _slug_item_id(str(v))

    @field_validator("dependency_ids", mode="before")
    @classmethod
    def _deps(cls, v: Any) -> list[str]:
        if v is None:
            return []
        if isinstance(v, list):
            return [str(x).strip() for x in v if str(x).strip()]
        return []


class AnalysisPlanLLMOutput(BaseModel):
    """综合分析 NL2SQL 路径：数据计划阶段结构化输出。"""

    tasks: list[AnalysisPlanTaskLLMItem] = Field(default_factory=list, max_length=16)


def extract_json_object_from_llm_text(raw: str) -> dict[str, Any] | None:
    """
    从模型回复中尽量解析出单个 JSON 对象（支持裸 JSON 或 ```json 围栏）。
    解析失败返回 None。
    """
    text = (raw or "").strip()
    if not text:
        return None
    fence = re.search(r"```(?:json)?\s*([\s\S]*?)```", text, re.IGNORECASE)
    if fence:
        text = fence.group(1).strip()
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end <= start:
        return None
    blob = text[start : end + 1]
    try:
        obj = json.loads(blob)
    except json.JSONDecodeError:
        return None
    return obj if isinstance(obj, dict) else None
