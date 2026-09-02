from __future__ import annotations

from typing import Any, List

from pydantic import BaseModel, Field, field_validator

from app.conversation.ids import validate_session_id, validate_user_id


class NL2SQLQueryRequest(BaseModel):
    user_id: str = Field(..., description="用户唯一标识（由调用方后台传入）")
    session_id: str = Field(..., description="会话唯一标识")
    question: str = Field(..., description="自然语言问题")
    analysis_type: str | None = Field(default=None, description="可选：分析场景类型")
    analysis_request_id: str | None = Field(
        default=None,
        description="可选：综合分析等上层编排的 request_id，用于日志关联",
    )
    plan_item_id: str | None = Field(
        default=None,
        description="可选：数据计划子任务 item_id（如 q1、q2）",
    )
    plan_template_version: str | None = Field(
        default=None,
        description=(
            "可选：NL2SQL 数据计划模板版本（prompts_bak_new.yaml analysis_plan_<type> 的 version，如 v1/v2）；"
            "综合分析 acquire_data 传入，用于 QA 向量闭环五元组去重"
        ),
    )
    time_intent_text: str | None = Field(
        default=None,
        description=(
            "可选：动态时间窗等规则从该文本抽取时间语义；未设置时与 question 一致。"
            "综合分析等场景可设为上层用户原句，避免任务 question 末尾 RAG 附录污染时间抽取。"
        ),
    )
    confirmed_scope: dict[str, Any] | None = Field(
        default=None,
        description="可选：人工确认后的结构化范围（看图诊断 HITL）；仅 img_diag 传入。",
    )
    scope_intent_text: str | None = Field(
        default=None,
        description="可选：由结构化 scope 合成的解析短句，作为 time_intent 优先输入（看图诊断 HITL）。",
    )
    original_query: str | None = Field(
        default=None,
        description="可选：用户原始问句，confirmed_scope 模式下时间解析兜底来源。",
    )
    on_link_failure: str | None = Field(
        default=None,
        description="链接失败策略：refuse | best_effort；默认取部署配置 NL2SQL_ON_LINK_FAILURE。",
    )
    structured_filters: dict[str, Any] | None = Field(
        default=None,
        description="可选：已确认结构化过滤条件（站点/行政区等），合并入 scope SQL 改写。",
    )
    sql_gen_extra_hint: str | None = Field(
        default=None,
        description="可选：追加到 NL2SQL 生成 prompt 的场景说明（如智能客服 SELECT 可读性约束）。",
    )
    disable_qa_slot_replay: bool | None = Field(
        default=None,
        description=(
            "可选：为 true 时跳过 QA 槽位 strict SQL 回放（仍可走 RAG 召回示例）。"
            "综合分析智能体默认由 ANALYSIS_AGENT_NL2SQL_DISABLE_QA_SLOT_REPLAY 注入。"
        ),
    )
    forced_tables: list[str] | None = Field(
        default=None,
        description=(
            "可选锁表：非空时本请求 catalog 仅保留这些表（外加 t_station，且须在表白名单内）。"
            "省略或空列表 = 现网行为（全量白名单 + 按问句语义链接）。"
            "数据查询智能体在库锁定后传入，例如 [\"t_data_wash_fcb\"]。"
        ),
    )

    @field_validator("user_id")
    @classmethod
    def _v_uid(cls, v: str) -> str:
        return validate_user_id(v)

    @field_validator("session_id")
    @classmethod
    def _v_sid(cls, v: str) -> str:
        return validate_session_id(v)


class NL2SQLQueryResponse(BaseModel):
    sql: str = Field(..., description="生成的 SQL 语句")
    rows: List[dict[str, Any]] = Field(default_factory=list, description="查询结果行列表")
    gen_fail_reason: str | None = Field(
        default=None,
        description="生成失败机读原因（如 link_failed:...）；与 parsed_intent.gen_fail_reason 一致。",
    )
    parsed_intent: dict[str, Any] | None = Field(
        default=None,
        description=(
            "结构化问句意图（时间窗 + 锅炉/受热面/管排/排数/管数）。"
            "默认不返回；设置环境变量 NL2SQL_RESPONSE_INCLUDE_PARSED_INTENT=true 后包含。"
            "字段含 parse_mode、scope_question、time_window_tag、time_window、statistical_time_range、scope。"
        ),
    )

