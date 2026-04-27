from __future__ import annotations

"""
综合分析 HTTP 接口（企业版 V2）。

职责：
    - 提供企业级双入口：payload / nl2sql；
    - 提供 trace 回放、统计、趋势、降级 TopN 运维接口。

鉴权与身份：
    - 请求头须携带 `Authorization: Bearer <SERVICE_API_KEY>`（密钥生成与配置见 `app/auth/keygen.py` 与
      `app/app-deploy/README-simple-deploy.md`「Service API Key」）；
    - `user_id`、`session_id` 由调用方在请求体传入，用于会话记录与 Prompt 分流。

OpenAPI 说明：各接口的字段释义、必填与约束以 **请求/响应模型的 Schema**（`Field(description=…)`）为准；
本模块路由的 docstring 提供速查；路由上使用 `response_description` 描述响应语义（勿在装饰器上写
`description=`，以免覆盖函数 docstring 导致长说明不出现在 Swagger）。
"""

from typing import Annotated

from fastapi import APIRouter, HTTPException, Path, Query

from app.models.analysis import (
    AnalysisNL2SQLRequest,
    AnalysisPayloadRequest,
    AnalysisTraceDegradeTopNResponse,
    AnalysisTraceListItem,
    AnalysisTraceListResponse,
    AnalysisTraceStatsResponse,
    AnalysisTraceTrendResponse,
    AnalysisTraceView,
    AnalysisV2Result,
)
from app.services.analysis_service import AnalysisService

router = APIRouter()
service = AnalysisService()


@router.post(
    "/run-with-payload",
    summary="综合分析执行（payload 模式）",
    response_model=AnalysisV2Result,
    response_description="执行成功返回结构化报告、证据与 trace；失败时 HTTP 4xx/5xx（如 strict 质量门未过可能 500）。",
)
async def run_analysis_with_payload(data: AnalysisPayloadRequest) -> AnalysisV2Result:
    """
    综合分析 V2（payload）：调用方自带事实数据，不经 NL2SQL 查库。编排见 `AnalysisGraphRunner.run_with_payload`。

    **鉴权**：`Authorization: Bearer <SERVICE_API_KEY>`（与其它业务路由一致）。

    **路径 / Query**：无。

    **请求体 `AnalysisPayloadRequest`（Schema 为准，以下为速查）**
    - `user_id`：**必填**。后台用户标识；须通过 `validate_user_id` 规则。
    - `session_id`：**必填**。会话标识；须通过 `validate_session_id` 规则。
    - `analysis_type`：**必填**。`overheat_guidance` | `maintenance_strategy` | `custom`，用于 Prompt / 计划模板分流。
    - `query`：**必填**。分析需求的自然语言描述。
    - `payload`：可选，默认 `{}`。调用方提供的结构化分析输入（表、指标等），直接进入质量门与合成阶段。
    - `options`：可选，默认各字段见模型。含 `enable_rag`、`enable_context`、`report_style`、`report_template`、
      `chart_mode`、`max_suggestions`、`max_nl2sql_calls`（本模式一般不触发 NL2SQL，仍参与默认合并）、
      `max_rows_per_query`、`strict` 等；未传字段由 `AnalysisService._apply_defaults_payload` 用环境配置补齐。

    **响应体 `AnalysisV2Result`（200）**
    - `request_id`：本次分析 ID，可用于运维查询。
    - `analysis_type`、`summary`：类型与自然语言摘要结论。
    - `structured_report`：结构化报告体（章节/建议等，随模板变化）。
    - `evidence`：`used_rag`、`rag_sources`、`data_coverage` 等；payload 模式下 `nl2sql_calls` 通常为空列表。
    - `trace`：`plan_id`、各节点耗时 `node_latency_ms`、`execution_summary`、`degrade_reasons` 等。

    失败时 HTTP 状态码与 `detail` 见全局异常处理；strict 模式下数据质量不满足可能抛错。
    """
    return await service.run_analysis_payload(data)


@router.post(
    "/run-with-nl2sql",
    summary="综合分析执行（NL2SQL 模式）",
    response_model=AnalysisV2Result,
    response_description="执行成功返回结构化报告、NL2SQL 调用证据与 trace；strict 未过或内部错误时 4xx/5xx。",
)
async def run_analysis_with_nl2sql(data: AnalysisNL2SQLRequest) -> AnalysisV2Result:
    """
    综合分析 V2（NL2SQL）：按数据计划多次调用 `NL2SQLService` 拉数后再合成报告。编排见 `AnalysisGraphRunner.run_with_nl2sql`。

    **鉴权**：`Authorization: Bearer <SERVICE_API_KEY>`。

    **路径 / Query**：无。

    **请求体 `AnalysisNL2SQLRequest`（Schema 为准，以下为速查）**
    - `user_id`：**必填**。与 NL2SQL 子调用及会话写入一致。
    - `session_id`：**必填**。
    - `analysis_type`：**必填**。影响 `configs/prompts.yaml` 中 `analysis_plan_<type>` 与内置默认取数任务。
    - `query`：**必填**。总体分析需求描述。
    - `data_requirements_hint`：可选，默认 `[]`。额外数据维度提示，编排器会合并为补充查询任务（非强制项）。
    - `options`：可选。`max_nl2sql_calls`（单次允许子查询条数上限）、`max_rows_per_query`（每子查询截断行数）、
      `enable_rag`（规划前 nl2sql 命名空间 RAG + 结论前业务 RAG）、`strict`（质量阈值未过则失败）等；
      默认值由 `AnalysisService._apply_defaults_nl2sql` 补齐。

    **响应体 `AnalysisV2Result`（200）**
    - 与 payload 模式相同顶层字段。
    - `evidence.nl2sql_calls`：每项子查询的 `item_id`、`question`、`sql`、`row_count`、`status`、`attempts`、`error` 等。
    - `evidence.data_coverage`：常含 `mode=nl2sql`、`planned_calls`、`success_calls`、质量摘要等。
    - `trace.data_plan_trace`：与 NL2SQL 子任务对应的执行轨迹摘要。

    子查询层使用 `record_conversation=False`，不在会话中重复堆叠 NL2SQL 明细（与直连 `/nl2sql/query` 行为差异）。
    """
    return await service.run_analysis_nl2sql(data)


@router.get(
    "/traces/{request_id}",
    summary="查询综合分析执行 trace",
    response_model=AnalysisTraceView,
    response_description="命中返回单条 trace 视图；不存在时 404。",
)
async def get_analysis_trace(
    request_id: Annotated[str, Path(description="分析请求 ID，与 `AnalysisV2Result.request_id` 一致（如 `anl_` 前缀）")],
) -> AnalysisTraceView:
    """
    按 `request_id` 查询单次分析的持久化 trace（后端由 `ANALYSIS_TRACE_BACKEND` 决定：Redis / ES / 内存等）。

    **路径参数**
    - `request_id`：**必填**。执行 `run-with-payload` / `run-with-nl2sql` 时响应体中的 `request_id`。

    **响应体 `AnalysisTraceView`（200）**
    - `request_id`、`analysis_type`、`summary`、`data_mode`（`payload` | `nl2sql`）。
    - `trace`：完整 `AnalysisTrace`（节点耗时、模板版本、`data_plan_trace`、`degrade_reasons` 等）。
    - `data_coverage`：自 `evidence.data_coverage` 展平的覆盖摘要。

    未找到记录时 **404**，`detail` 含 `request_id`。
    """
    hit = service.get_trace(request_id)
    if hit is None:
        raise HTTPException(status_code=404, detail=f"analysis trace not found: {request_id}")
    return hit


@router.get(
    "/traces",
    summary="分页查询综合分析 trace 列表",
    response_model=AnalysisTraceListResponse,
    response_description="分页返回 trace 摘要列表及 total；过滤条件均为可选。",
)
async def list_analysis_traces(
    limit: Annotated[int, Query(description="每页条数，默认 20；建议与 offset 配合避免单次过大。", ge=1)] = 20,
    offset: Annotated[int, Query(description="偏移量，默认 0", ge=0)] = 0,
    analysis_type: Annotated[
        str | None,
        Query(description="可选。按分析类型过滤，如 overheat_guidance、maintenance_strategy、custom。"),
    ] = None,
    data_mode: Annotated[
        str | None,
        Query(description="可选。按执行模式过滤：payload 或 nl2sql。"),
    ] = None,
    request_id_like: Annotated[
        str | None,
        Query(description="可选。`request_id` 子串匹配（内存侧过滤，非全库 SQL LIKE）。"),
    ] = None,
    started_from: Annotated[
        str | None,
        Query(
            description="可选。起始时间下界 ISO8601（支持 `Z` 后缀），对应 trace 的 started_at。",
        ),
    ] = None,
    started_to: Annotated[
        str | None,
        Query(description="可选。结束时间上界 ISO8601（支持 `Z` 后缀）。"),
    ] = None,
) -> AnalysisTraceListResponse:
    """
    分页列出已保存的分析 trace 摘要（不含完整 trace 体，见列表项字段）。

    **Query（均为可选，除 limit/offset 有默认）**
    - `limit` / `offset`：分页；服务层会放大抓取再过滤，大 offset 时注意性能。
    - `analysis_type`、`data_mode`：存储后端支持的等值过滤。
    - `request_id_like`：子串过滤，仅作用于当前拉取窗口内的结果。
    - `started_from`、`started_to`：按执行开始时间筛选。

    **响应体 `AnalysisTraceListResponse`（200）**
    - `ok`、`limit`、`offset`、`total`、`items[]`（`AnalysisTraceListItem`：`request_id`、`analysis_type`、`data_mode`、
      `summary_preview`、`created_at`、`used_rag`）。
    """
    items, total = service.list_traces(
        limit=limit,
        offset=offset,
        analysis_type=analysis_type,
        data_mode=data_mode,
        request_id_like=request_id_like,
        started_from=started_from,
        started_to=started_to,
    )
    rows = [
        AnalysisTraceListItem(
            request_id=x.request_id,
            analysis_type=x.analysis_type,
            data_mode=x.data_mode,
            summary_preview=x.summary[:120],
            created_at=str(x.trace.execution_summary.get("started_at", "")),
            used_rag=bool(x.trace.execution_summary.get("used_rag", False)),
        )
        for x in items
    ]
    return AnalysisTraceListResponse(ok=True, limit=limit, offset=offset, total=total, items=rows)


@router.get(
    "/traces/stats",
    summary="综合分析 trace 聚合统计",
    response_model=AnalysisTraceStatsResponse,
    response_description="在最多 1000 条命中样本上做按类型、模式、降级原因聚合（非全量扫描）。",
)
async def get_analysis_trace_stats(
    analysis_type: Annotated[
        str | None,
        Query(description="可选。只统计该 `analysis_type`。"),
    ] = None,
    data_mode: Annotated[
        str | None,
        Query(description="可选。只统计该 `data_mode`（payload / nl2sql）。"),
    ] = None,
    started_from: Annotated[
        str | None,
        Query(description="可选。时间下界 ISO8601。"),
    ] = None,
    started_to: Annotated[
        str | None,
        Query(description="可选。时间上界 ISO8601。"),
    ] = None,
) -> AnalysisTraceStatsResponse:
    """
    在限定样本（内部最多拉取 1000 条 trace）上聚合：按分析类型、数据模式计数及降级原因频次。

    **Query**：均为可选，见参数 `description`。

    **响应体 `AnalysisTraceStatsResponse`（200）**
    - `ok`、`total`：样本命中总数（受 list 上限影响，非全局精确 count 时见服务实现）。
    - `by_analysis_type`、`by_data_mode`：聚合字典。
    - `degrade_reasons`：降级原因 → 出现次数。
    """
    return service.get_trace_stats(
        analysis_type=analysis_type,
        data_mode=data_mode,
        started_from=started_from,
        started_to=started_to,
    )


@router.get(
    "/traces/trend",
    summary="综合分析 trace 时间趋势统计",
    response_model=AnalysisTraceTrendResponse,
    response_description="按 minute/hour 时间桶返回各数据模式计数序列；带进程内短 TTL 缓存。",
)
async def get_analysis_trace_trend(
    bucket: Annotated[
        str,
        Query(
            description="时间桶粒度：`minute` 或 `hour`（非法值时回退为 hour）。",
        ),
    ] = "hour",
    analysis_type: Annotated[str | None, Query(description="可选。只统计该分析类型。")] = None,
    data_mode: Annotated[str | None, Query(description="可选。只统计该数据模式。")] = None,
    started_from: Annotated[str | None, Query(description="可选。时间下界 ISO8601。")] = None,
    started_to: Annotated[str | None, Query(description="可选。时间上界 ISO8601。")] = None,
) -> AnalysisTraceTrendResponse:
    """
    按时间桶聚合 trace 条数，用于看板趋势（实现上依赖 trace 存储 list + 进程内缓存）。

    **Query**
    - `bucket`：可选，默认 `hour`；`minute` 更细但数据量更大。
    - 其余过滤字段同 stats 接口。

    **响应体 `AnalysisTraceTrendResponse`（200）**
    - `ok`、`bucket`：实际使用的粒度。
    - `points[]`：`bucket_start`（ISO8601）、`total`、`by_data_mode`（含 `payload` / `nl2sql` 计数）。
    """
    return service.get_trace_trend(
        bucket=bucket,
        analysis_type=analysis_type,
        data_mode=data_mode,
        started_from=started_from,
        started_to=started_to,
    )


@router.get(
    "/traces/degrade-topn",
    summary="综合分析 trace 降级原因 TopN",
    response_model=AnalysisTraceDegradeTopNResponse,
    response_description="在最多 5000 条样本上统计 degrade_reasons 频次，返回 TopN。",
)
async def get_analysis_trace_degrade_topn(
    top_n: Annotated[
        int,
        Query(description="返回条数上限，默认 10；服务端会限制在 1～50。", ge=1, le=50),
    ] = 10,
    analysis_type: Annotated[str | None, Query(description="可选。只统计该分析类型。")] = None,
    data_mode: Annotated[str | None, Query(description="可选。只统计该数据模式。")] = None,
    started_from: Annotated[str | None, Query(description="可选。时间下界 ISO8601。")] = None,
    started_to: Annotated[str | None, Query(description="可选。时间上界 ISO8601。")] = None,
) -> AnalysisTraceDegradeTopNResponse:
    """
    统计 `trace.degrade_reasons` 出现频次，按次数降序取 TopN，供排障。

    **Query**
    - `top_n`：可选，默认 10，最大 50（与服务层 `get_degrade_topn` 一致）。
    - 时间 / 类型 / 模式过滤：缩小样本范围（内部最多分析 5000 条 trace）。

    **响应体 `AnalysisTraceDegradeTopNResponse`（200）**
    - `ok`、`total_unique`：不同降级原因种类数。
    - `items[]`：`reason`、`count`。
    """
    return service.get_degrade_topn(
        top_n=top_n,
        analysis_type=analysis_type,
        data_mode=data_mode,
        started_from=started_from,
        started_to=started_to,
    )
