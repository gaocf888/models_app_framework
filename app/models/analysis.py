from __future__ import annotations

"""
综合分析（企业版 V2）请求 / 响应 / Trace 的 Pydantic 模型。

HTTP 契约见 `app/api/analysis.py`；编排与证据填充见 `AnalysisGraphRunner` / `AnalysisService`。
"""

from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field, field_validator, model_validator

from app.conversation.ids import validate_session_id, validate_user_id


class AnalysisInput(BaseModel):
    """
    综合分析请求的输入数据。

    目前以占位形式支持文本描述和多模态数据的引用 ID，后续可扩展为实际上传/存储方案。
    """

    user_id: str = Field(..., description="用户唯一标识（由调用方后台传入）")
    session_id: str = Field(..., description="会话唯一标识")
    query: str = Field(..., description="分析需求的自然语言描述")
    image_ids: List[str] = Field(default_factory=list, description="相关图像数据的标识符")
    video_clip_ids: List[str] = Field(default_factory=list, description="相关视频片段的标识符")
    gps_ids: List[str] = Field(default_factory=list, description="相关 GPS 数据的标识符")
    sensor_data_ids: List[str] = Field(default_factory=list, description="相关传感器数据的标识符")
    enable_rag: bool = Field(True, description="是否启用 RAG 检索")
    enable_context: bool = Field(True, description="是否启用会话上下文")

    @field_validator("user_id")
    @classmethod
    def _v_uid(cls, v: str) -> str:
        return validate_user_id(v)

    @field_validator("session_id")
    @classmethod
    def _v_sid(cls, v: str) -> str:
        return validate_session_id(v)


class AnalysisResult(BaseModel):
    """
    综合分析结果。
    """

    summary: str = Field(..., description="综合分析报告或结论的摘要")
    details: Optional[str] = Field(None, description="更详细的分析说明（可选）")
    used_rag: bool = Field(..., description="是否实际使用 RAG")
    context_snippets: List[str] = Field(default_factory=list, description="检索到的文本上下文片段摘要")


AnalysisType = Literal[
    "overheat_guidance",
    "maintenance_strategy",
    "four_tube_health_interpretation",
    "leakage_burst_analysis",
    "custom",
    "img_diag_defect_ident",
    "img_diag_leakage_burst",
]
ImgDiagSubtype = Literal["defect_ident", "leakage_burst"]
DataMode = Literal["payload", "nl2sql", "img_diag_defect_ident", "img_diag_leakage_burst"]


class AnalysisOptions(BaseModel):
    """两种分析入口共用的执行选项（报告形态、strict、NL2SQL 上限等）。"""

    enable_rag: bool = Field(True, description="是否启用 RAG 增强")
    enable_context: bool = Field(True, description="是否启用会话上下文")
    report_style: str = Field("standard", description="报告风格，如 standard/strict")
    max_suggestions: int = Field(8, ge=1, le=20, description="建议条目最大数量")
    max_nl2sql_calls: int = Field(15, ge=1, le=20, description="单次分析允许的 NL2SQL 调用上限")
    max_rows_per_query: int = Field(2000, ge=50, le=20000, description="单次 NL2SQL 查询行数建议上限")
    strict: bool = Field(False, description="是否启用严格模式（关键数据缺失时直接失败）")
    report_template: str = Field("standard", description="报告模板标识，如 standard/executive")
    chart_mode: Literal["auto", "minimal", "off"] = Field("auto", description="图表输出策略")


class AnalysisPayloadRequest(BaseModel):
    """payload 模式：调用方直接提供 `payload` 字典，不经 NL2SQL 取数。"""

    user_id: str = Field(..., description="用户唯一标识（由调用方后台传入）")
    session_id: str = Field(..., description="会话唯一标识")
    analysis_type: AnalysisType = Field(..., description="分析类型")
    query: str = Field(..., description="分析需求的自然语言描述")
    payload: Dict[str, Any] = Field(default_factory=dict, description="由调用方直接提供的分析数据载荷")
    options: AnalysisOptions = Field(default_factory=AnalysisOptions, description="分析执行选项")

    @field_validator("user_id")
    @classmethod
    def _v2_uid(cls, v: str) -> str:
        return validate_user_id(v)

    @field_validator("session_id")
    @classmethod
    def _v2_sid(cls, v: str) -> str:
        return validate_session_id(v)


class AnalysisImgDiagRequest(BaseModel):
    """看图诊断（缺陷识别 / 泄爆分析）：视觉理解 ‖ NL2SQL 取数（并行）→ 业务 RAG（串行）→ 合成。

    业务入参：**`query`（用户问题，承载时间/位置/范围语义）** 与 **`image_urls`（缺陷/爆口图片）**；
    机组/锅炉、受热面、管排、排数、管数由 NL2SQL 基座从 `query` 解析并改写 SQL。

    - `defect_ident`：缺陷识别，**图片必填**；
    - `leakage_burst`：泄爆分析，**图片可选**（无图时仅依赖 query + NL2SQL + RAG）。

    HTTP：**`POST /analysis/run-img-diag`** / **`POST /analysis/run-img-diag-stream`** 共用本模型。
    """

    user_id: str = Field(..., description="用户唯一标识（由调用方后台传入）")
    session_id: str = Field(..., description="会话唯一标识")
    img_diag_subtype: ImgDiagSubtype = Field(
        default="defect_ident",
        description="看图诊断子场景：defect_ident（缺陷识别）| leakage_burst（泄爆分析）",
    )
    query: str = Field(
        ...,
        description="用户自然语言问题（须含或可解析区域/管段位置；泄爆须含或可解析事故发生时间）",
    )
    image_urls: List[str] = Field(
        default_factory=list,
        description="缺陷/爆口现场照片 URL 列表（defect_ident 必填；leakage_burst 可选）",
    )
    data_requirements_hint: List[str] = Field(
        default_factory=list,
        description="可选的补充数据维度提示，合并进 NL2SQL 计划任务",
    )
    options: AnalysisOptions = Field(
        default_factory=AnalysisOptions,
        description="执行选项；看图诊断默认开启 enable_rag",
    )

    @field_validator("user_id")
    @classmethod
    def _v_uid(cls, v: str) -> str:
        return validate_user_id(v)

    @field_validator("session_id")
    @classmethod
    def _v_sid(cls, v: str) -> str:
        return validate_session_id(v)

    @field_validator("query")
    @classmethod
    def _v_query(cls, v: str) -> str:
        text = (v or "").strip()
        if not text:
            raise ValueError("query must not be empty")
        return text

    @field_validator("image_urls")
    @classmethod
    def _v_images(cls, v: List[str]) -> List[str]:
        return [u.strip() for u in v if isinstance(u, str) and u.strip()]

    @model_validator(mode="after")
    def _validate_subtype_images(self) -> AnalysisImgDiagRequest:
        if self.img_diag_subtype == "defect_ident" and not self.image_urls:
            raise ValueError("defect_ident requires at least one image in image_urls")
        return self


class AnalysisNL2SQLRequest(BaseModel):
    """nl2sql 模式：由编排器按数据计划多次调用 NL2SQL 服务拉取事实数据。"""

    user_id: str = Field(..., description="用户唯一标识（由调用方后台传入）")
    session_id: str = Field(..., description="会话唯一标识")
    analysis_type: AnalysisType = Field(..., description="分析类型")
    query: str = Field(..., description="分析需求的自然语言描述")
    data_requirements_hint: List[str] = Field(default_factory=list, description="建议查询的数据需求提示")
    options: AnalysisOptions = Field(default_factory=AnalysisOptions, description="分析执行选项")
    confirmed_scope: Optional[Dict[str, Any]] = Field(
        default=None,
        description="看图诊断 HITL 确认后的结构化范围（可选）",
    )
    scope_intent_text: Optional[str] = Field(
        default=None,
        description="看图诊断 HITL 合成的 scope 解析短句（可选）",
    )

    @field_validator("user_id")
    @classmethod
    def _v2_uid(cls, v: str) -> str:
        return validate_user_id(v)

    @field_validator("session_id")
    @classmethod
    def _v2_sid(cls, v: str) -> str:
        return validate_session_id(v)


class AnalysisNL2SQLCall(BaseModel):
    """单次 NL2SQL 子调用在证据中的记录（与 trace.data_plan_trace 对应）。"""

    item_id: str = Field(..., description="查询计划项 ID")
    purpose: str = Field(..., description="该次查询的目的")
    question: str = Field(..., description="该次查询向 NL2SQL 提交的问题")
    sql: str = Field("", description="NL2SQL 生成并执行的 SQL")
    row_count: int = Field(0, description="返回行数")
    status: Literal["success", "failed", "skipped"] = Field(..., description="调用状态")
    error: Optional[str] = Field(None, description="失败原因（若有）")
    attempts: int = Field(1, description="该调用执行尝试次数")
    dependency_ids: List[str] = Field(default_factory=list, description="该调用依赖的计划项 ID")
    question_intent: Optional[Dict[str, Any]] = Field(
        default=None,
        description="问句结构化意图（时间+范围），供 trace 排障",
    )


class AnalysisEvidence(BaseModel):
    used_rag: bool = Field(False, description="是否使用了 RAG")
    rag_sources: List[Dict[str, Any]] = Field(default_factory=list, description="RAG 证据来源（审计简表）")
    rag_citations: List[Dict[str, Any]] = Field(
        default_factory=list,
        description="知识来源引用（与智能客服 finished.meta.rag_citations 同形，含 original_content_url 等）",
    )
    nl2sql_calls: List[AnalysisNL2SQLCall] = Field(default_factory=list, description="NL2SQL 调用明细")
    data_coverage: Dict[str, Any] = Field(default_factory=dict, description="数据覆盖率与质量摘要")
    vision_findings: Optional[Dict[str, Any]] = Field(
        default=None,
        description="看图诊断：视觉结构化理解结果（若无则为 None）",
    )


class AnalysisTrace(BaseModel):
    """单次分析执行遥测：节点耗时、模板版本、降级原因、数据计划执行轨迹等。"""

    plan_id: str = Field(..., description="本次分析计划 ID")
    node_latency_ms: Dict[str, int] = Field(default_factory=dict, description="节点耗时（毫秒）")
    template_versions: Dict[str, str] = Field(default_factory=dict, description="模板版本信息")
    execution_summary: Dict[str, Any] = Field(default_factory=dict, description="执行概览（模式、阶段状态、计数）")
    node_status: Dict[str, str] = Field(default_factory=dict, description="节点状态（success/failed/skipped）")
    data_plan_trace: List[Dict[str, Any]] = Field(default_factory=list, description="数据计划执行轨迹")
    degrade_reasons: List[str] = Field(default_factory=list, description="降级原因列表")


class AnalysisV2Result(BaseModel):
    """综合分析统一成功响应体，亦为 trace 存储与列表接口的持久化形态。"""

    request_id: str = Field(..., description="请求 ID")
    analysis_type: AnalysisType = Field(..., description="分析类型")
    summary: str = Field(..., description="分析摘要结论")
    structured_report: Dict[str, Any] = Field(default_factory=dict, description="结构化报告体")
    evidence: AnalysisEvidence = Field(default_factory=AnalysisEvidence, description="证据与取数信息")
    trace: AnalysisTrace = Field(..., description="执行链路追踪信息")


class AnalysisTraceView(BaseModel):
    """面向运维查询的 trace 视图（在 `AnalysisV2Result` 上裁剪/展平部分字段）。"""

    request_id: str = Field(..., description="请求 ID")
    analysis_type: AnalysisType = Field(..., description="分析类型")
    summary: str = Field(..., description="摘要结论")
    data_mode: DataMode = Field(..., description="执行模式")
    trace: AnalysisTrace = Field(..., description="执行链路追踪信息")
    data_coverage: Dict[str, Any] = Field(default_factory=dict, description="数据覆盖摘要")


class AnalysisTraceListItem(BaseModel):
    """trace 列表单行摘要（不含完整 trace 体）。"""

    request_id: str = Field(..., description="请求 ID")
    analysis_type: AnalysisType = Field(..., description="分析类型")
    data_mode: DataMode = Field(..., description="执行模式")
    summary_preview: str = Field(..., description="摘要预览")
    created_at: str = Field(..., description="创建时间（ISO8601）")
    used_rag: bool = Field(False, description="是否使用了 RAG")


class AnalysisTraceListResponse(BaseModel):
    """分页列出历史分析 trace 的 API 响应。"""

    ok: bool = Field(True, description="是否成功")
    limit: int = Field(..., description="分页大小")
    offset: int = Field(..., description="分页偏移")
    total: int = Field(..., description="总条数")
    items: List[AnalysisTraceListItem] = Field(default_factory=list, description="trace 列表")


class AnalysisTraceStatsResponse(BaseModel):
    """按类型 / 模式 / 降级原因聚合的 trace 统计。"""

    ok: bool = Field(True, description="是否成功")
    total: int = Field(0, description="命中 trace 总数")
    by_analysis_type: Dict[str, int] = Field(default_factory=dict, description="按分析类型聚合")
    by_data_mode: Dict[str, int] = Field(default_factory=dict, description="按执行模式聚合")
    degrade_reasons: Dict[str, int] = Field(default_factory=dict, description="降级原因聚合统计")


class AnalysisTraceTrendPoint(BaseModel):
    """时间趋势图单个时间桶的计数。"""

    bucket_start: str = Field(..., description="桶起始时间（ISO8601）")
    total: int = Field(0, description="该桶 trace 数")
    by_data_mode: Dict[str, int] = Field(default_factory=dict, description="该桶按执行模式计数")


class AnalysisTraceTrendResponse(BaseModel):
    ok: bool = Field(True, description="是否成功")
    bucket: Literal["minute", "hour"] = Field("hour", description="聚合粒度")
    points: List[AnalysisTraceTrendPoint] = Field(default_factory=list, description="时间序列点")


class AnalysisTraceDegradeItem(BaseModel):
    """单一降级原因及其出现次数。"""

    reason: str = Field(..., description="降级原因")
    count: int = Field(0, description="出现次数")


class AnalysisTraceDegradeTopNResponse(BaseModel):
    """降级原因 TopN 排行（运维排障）。"""

    ok: bool = Field(True, description="是否成功")
    total_unique: int = Field(0, description="降级原因去重总数")
    items: List[AnalysisTraceDegradeItem] = Field(default_factory=list, description="按次数排序的 TopN 列表")


class ImgDiagScopeResumeRequest(BaseModel):
    """看图诊断 scope HITL resume 请求。"""

    resume_token: str = Field(..., description="scope interrupt 返回的 resume_token")
    user_id: str = Field(..., description="用户 ID")
    session_id: str = Field(..., description="会话 ID")
    action: str = Field(default="confirm_scope", description="confirm_scope|edit_scope|abort")
    payload: Optional[Dict[str, Any]] = Field(default=None, description="user_supplement / scope_patch 等")

    @field_validator("user_id")
    @classmethod
    def _v_uid(cls, v: str) -> str:
        return validate_user_id(v)

    @field_validator("session_id")
    @classmethod
    def _v_sid(cls, v: str) -> str:
        return validate_session_id(v)

