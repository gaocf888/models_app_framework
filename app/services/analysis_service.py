from __future__ import annotations

"""
综合分析应用服务层：默认值注入、`AnalysisGraphRunner` 编排、trace 写入与运维查询（列表/统计/趋势/降级 TopN）。
"""

import time
from datetime import datetime, timezone
from threading import Lock

from app.conversation.manager import ConversationManager
from app.core.config import get_app_config
from app.core.logging import get_logger
from app.core.metrics import (
    ANALYSIS_TRACE_QUERY_COUNT,
    ANALYSIS_TRACE_QUERY_LATENCY,
    ANALYSIS_TRACE_TREND_CACHE_HIT_COUNT,
    ANALYSIS_TRACE_TREND_CACHE_INVALIDATE_COUNT,
    ANALYSIS_TRACE_TREND_CACHE_MISS_COUNT,
)
from app.models.analysis import (
    AnalysisTraceDegradeItem,
    AnalysisTraceDegradeTopNResponse,
    AnalysisNL2SQLRequest,
    AnalysisPayloadRequest,
    AnalysisTraceStatsResponse,
    AnalysisTraceTrendPoint,
    AnalysisTraceTrendResponse,
    AnalysisTraceView,
    AnalysisV2Result,
)
from app.llm.client import VLLMHttpClient
from app.llm.graphs.analysis_graph_runner import AnalysisGraphRunner
from app.llm.prompt_registry import PromptTemplateRegistry
from app.rag.hybrid_rag_service import HybridRAGService
from app.rag.rag_service import RAGService
from app.services.nl2sql_service import NL2SQLService
from app.services.analysis_trace_store import create_analysis_trace_store

logger = get_logger(__name__)


class AnalysisService:
    """
    综合分析服务（企业版 V2）。

    说明：
    - 仅保留企业级双入口链路（payload/nl2sql）；
    - 统一由 AnalysisGraphRunner 编排，不再保留旧版 /analysis/run 回退链路。
    """

    def __init__(
        self,
        rag_service: RAGService | None = None,
        conv_manager: ConversationManager | None = None,
        llm_client: VLLMHttpClient | None = None,
        prompt_registry: PromptTemplateRegistry | None = None,
    ) -> None:
        """组装 RAG/LLM/NL2SQL、trace 存储与 `AnalysisGraphRunner`；未传入时使用默认实现。"""
        self._hybrid_rag = HybridRAGService(rag_service=rag_service or RAGService())
        self._conv = conv_manager or ConversationManager()
        self._llm = llm_client or VLLMHttpClient()
        self._prompts = prompt_registry or PromptTemplateRegistry()
        self._nl2sql = NL2SQLService(conv_manager=self._conv)
        analysis_cfg = get_app_config().analysis
        self._trace_store = create_analysis_trace_store(
            backend=analysis_cfg.trace_backend,
            ttl_minutes=analysis_cfg.trace_ttl_minutes,
            max_items=analysis_cfg.trace_max_items,
            lazy_cleanup_batch_size=analysis_cfg.trace_lazy_cleanup_batch_size,
            es_hosts=analysis_cfg.trace_es_hosts,
            es_index=analysis_cfg.trace_es_index,
            es_verify_certs=analysis_cfg.trace_es_verify_certs,
            es_timeout_seconds=analysis_cfg.trace_es_timeout_seconds,
            es_username=analysis_cfg.trace_es_username or None,
            es_password=analysis_cfg.trace_es_password or None,
            es_api_key=analysis_cfg.trace_es_api_key or None,
        )
        self._trace_trend_cache_ttl = max(1, int(analysis_cfg.trace_trend_cache_ttl_seconds))
        self._trace_trend_cache: dict[str, tuple[float, AnalysisTraceTrendResponse]] = {}
        self._trace_trend_cache_lock = Lock()
        self._graph_runner = AnalysisGraphRunner(
            conv_manager=self._conv,
            llm_client=self._llm,
            prompt_registry=self._prompts,
            hybrid_rag=self._hybrid_rag,
            nl2sql_service=self._nl2sql,
        )

    async def run_analysis_payload(self, data: AnalysisPayloadRequest) -> AnalysisV2Result:
        """执行 payload 分析并持久化 trace。"""
        req = self._apply_defaults_payload(data)
        result = await self._graph_runner.run_with_payload(req)
        self._save_trace(result)
        return result

    async def run_analysis_nl2sql(self, data: AnalysisNL2SQLRequest) -> AnalysisV2Result:
        req = self._apply_defaults_nl2sql(data)
        result = await self._graph_runner.run_with_nl2sql(req)
        self._save_trace(result)
        return result

    def get_trace(self, request_id: str) -> AnalysisTraceView | None:
        """按 request_id 读取单次分析完整 trace（后端由 `ANALYSIS_TRACE_BACKEND` 决定）。"""
        started = time.perf_counter()
        try:
            hit = self._trace_store.get(request_id)
            ANALYSIS_TRACE_QUERY_COUNT.labels(endpoint="get", status="success").inc()
            if hit is None:
                return None
            return AnalysisTraceView(
                request_id=hit.request_id,
                analysis_type=hit.analysis_type,
                summary=hit.summary,
                data_mode=hit.evidence.data_coverage.get("mode", "payload"),
                trace=hit.trace,
                data_coverage=hit.evidence.data_coverage,
            )
        except Exception:  # noqa: BLE001
            ANALYSIS_TRACE_QUERY_COUNT.labels(endpoint="get", status="failed").inc()
            raise
        finally:
            ANALYSIS_TRACE_QUERY_LATENCY.labels(endpoint="get").observe(time.perf_counter() - started)

    def _save_trace(self, result: AnalysisV2Result) -> None:
        """写入 trace 存储并失效趋势查询内存缓存。"""
        self._trace_store.save(result)
        # 新 trace 写入后清空趋势缓存，确保统计结果及时反映最新数据。
        with self._trace_trend_cache_lock:
            if self._trace_trend_cache:
                self._trace_trend_cache.clear()
                ANALYSIS_TRACE_TREND_CACHE_INVALIDATE_COUNT.inc()

    @staticmethod
    def _parse_iso8601(value: str | None) -> datetime | None:
        """解析查询参数中的时间边界（支持 Z 后缀）。"""
        if not value:
            return None
        text = value.strip()
        if not text:
            return None
        try:
            if text.endswith("Z"):
                text = text[:-1] + "+00:00"
            dt = datetime.fromisoformat(text)
            return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
        except Exception:  # noqa: BLE001
            return None

    @staticmethod
    def _to_trace_view(x: AnalysisV2Result) -> AnalysisTraceView:
        """将完整结果转为列表/统计接口用的精简视图。"""
        return AnalysisTraceView(
            request_id=x.request_id,
            analysis_type=x.analysis_type,
            summary=x.summary,
            data_mode=x.evidence.data_coverage.get("mode", "payload"),
            trace=x.trace,
            data_coverage=x.evidence.data_coverage,
        )

    def list_traces(
        self,
        *,
        limit: int,
        offset: int,
        analysis_type: str | None = None,
        data_mode: str | None = None,
        request_id_like: str | None = None,
        started_from: str | None = None,
        started_to: str | None = None,
    ) -> tuple[list[AnalysisTraceView], int]:
        started = time.perf_counter()
        try:
            from_dt = self._parse_iso8601(started_from)
            to_dt = self._parse_iso8601(started_to)
            score_min_ms = int(from_dt.timestamp() * 1000) if from_dt else None
            score_max_ms = int(to_dt.timestamp() * 1000) if to_dt else None
            fetch_limit = min(1000, max(200, offset + limit + 200))
            items, total_from_store = self._trace_store.list(
                limit=fetch_limit,
                offset=0,
                score_min_ms=score_min_ms,
                score_max_ms=score_max_ms,
                analysis_type=analysis_type,
                data_mode=data_mode,
            )
            filtered: list[AnalysisV2Result] = []
            for x in items:
                if request_id_like and request_id_like not in x.request_id:
                    continue
                filtered.append(x)
            total = total_from_store if (analysis_type is None and data_mode is None and request_id_like is None) else len(filtered)
            page = filtered[offset : offset + limit]
            out = [self._to_trace_view(x) for x in page]
            ANALYSIS_TRACE_QUERY_COUNT.labels(endpoint="list", status="success").inc()
            return out, total
        except Exception:  # noqa: BLE001
            ANALYSIS_TRACE_QUERY_COUNT.labels(endpoint="list", status="failed").inc()
            raise
        finally:
            ANALYSIS_TRACE_QUERY_LATENCY.labels(endpoint="list").observe(time.perf_counter() - started)

    def get_trace_stats(
        self,
        *,
        analysis_type: str | None = None,
        data_mode: str | None = None,
        started_from: str | None = None,
        started_to: str | None = None,
    ) -> AnalysisTraceStatsResponse:
        """在最多 1000 条命中样本上做聚合统计（非全量扫描）。"""
        started = time.perf_counter()
        try:
            items, total = self.list_traces(
                limit=1000,
                offset=0,
                analysis_type=analysis_type,
                data_mode=data_mode,
                started_from=started_from,
                started_to=started_to,
            )
            by_type: dict[str, int] = {}
            by_mode: dict[str, int] = {}
            degrade: dict[str, int] = {}
            for row in items:
                by_type[row.analysis_type] = by_type.get(row.analysis_type, 0) + 1
                by_mode[row.data_mode] = by_mode.get(row.data_mode, 0) + 1
                for reason in row.trace.degrade_reasons:
                    degrade[reason] = degrade.get(reason, 0) + 1
            out = AnalysisTraceStatsResponse(
                ok=True,
                total=total,
                by_analysis_type=by_type,
                by_data_mode=by_mode,
                degrade_reasons=degrade,
            )
            ANALYSIS_TRACE_QUERY_COUNT.labels(endpoint="stats", status="success").inc()
            return out
        except Exception:  # noqa: BLE001
            ANALYSIS_TRACE_QUERY_COUNT.labels(endpoint="stats", status="failed").inc()
            raise
        finally:
            ANALYSIS_TRACE_QUERY_LATENCY.labels(endpoint="stats").observe(time.perf_counter() - started)

    def get_trace_trend(
        self,
        *,
        bucket: str = "hour",
        analysis_type: str | None = None,
        data_mode: str | None = None,
        started_from: str | None = None,
        started_to: str | None = None,
    ) -> AnalysisTraceTrendResponse:
        """按时间桶聚合 trace 量；带短 TTL 进程内缓存。"""
        started = time.perf_counter()
        cache_key = "|".join(
            [
                str(bucket),
                str(analysis_type or ""),
                str(data_mode or ""),
                str(started_from or ""),
                str(started_to or ""),
            ]
        )
        with self._trace_trend_cache_lock:
            hit = self._trace_trend_cache.get(cache_key)
            if hit and (time.time() - hit[0]) <= self._trace_trend_cache_ttl:
                ANALYSIS_TRACE_TREND_CACHE_HIT_COUNT.inc()
                ANALYSIS_TRACE_QUERY_COUNT.labels(endpoint="trend", status="success").inc()
                ANALYSIS_TRACE_QUERY_LATENCY.labels(endpoint="trend").observe(time.perf_counter() - started)
                return hit[1]
        ANALYSIS_TRACE_TREND_CACHE_MISS_COUNT.inc()
        try:
            from_dt = self._parse_iso8601(started_from)
            to_dt = self._parse_iso8601(started_to)
            score_min_ms = int(from_dt.timestamp() * 1000) if from_dt else None
            score_max_ms = int(to_dt.timestamp() * 1000) if to_dt else None
            items, _ = self._trace_store.list(
                limit=5000,
                offset=0,
                score_min_ms=score_min_ms,
                score_max_ms=score_max_ms,
                analysis_type=analysis_type,
                data_mode=data_mode,
            )

            if bucket not in {"minute", "hour"}:
                bucket = "hour"
            agg: dict[datetime, dict[str, int]] = {}
            for x in items:
                mode = str(x.evidence.data_coverage.get("mode", "payload"))
                started_at = self._parse_iso8601(str(x.trace.execution_summary.get("started_at", "")))
                if started_at is None:
                    continue
                ts = started_at.astimezone(timezone.utc)
                key = ts.replace(second=0, microsecond=0)
                if bucket == "hour":
                    key = key.replace(minute=0)
                row = agg.setdefault(key, {"total": 0, "payload": 0, "nl2sql": 0})
                row["total"] += 1
                row[mode] = row.get(mode, 0) + 1

            points = []
            for k in sorted(agg.keys()):
                row = agg[k]
                points.append(
                    AnalysisTraceTrendPoint(
                        bucket_start=k.isoformat().replace("+00:00", "Z"),
                        total=int(row.get("total", 0)),
                        by_data_mode={
                            "payload": int(row.get("payload", 0)),
                            "nl2sql": int(row.get("nl2sql", 0)),
                        },
                    )
                )
            result = AnalysisTraceTrendResponse(ok=True, bucket="minute" if bucket == "minute" else "hour", points=points)
            with self._trace_trend_cache_lock:
                self._trace_trend_cache[cache_key] = (time.time(), result)
            ANALYSIS_TRACE_QUERY_COUNT.labels(endpoint="trend", status="success").inc()
            return result
        except Exception:  # noqa: BLE001
            ANALYSIS_TRACE_QUERY_COUNT.labels(endpoint="trend", status="failed").inc()
            raise
        finally:
            ANALYSIS_TRACE_QUERY_LATENCY.labels(endpoint="trend").observe(time.perf_counter() - started)

    def get_degrade_topn(
        self,
        *,
        top_n: int = 10,
        analysis_type: str | None = None,
        data_mode: str | None = None,
        started_from: str | None = None,
        started_to: str | None = None,
    ) -> AnalysisTraceDegradeTopNResponse:
        """统计 trace 中 `degrade_reasons` 出现频次 TopN。"""
        started = time.perf_counter()
        try:
            items, _ = self.list_traces(
                limit=5000,
                offset=0,
                analysis_type=analysis_type,
                data_mode=data_mode,
                started_from=started_from,
                started_to=started_to,
            )
            counts: dict[str, int] = {}
            for row in items:
                for reason in row.trace.degrade_reasons:
                    if not reason:
                        continue
                    counts[reason] = counts.get(reason, 0) + 1
            rows = sorted(counts.items(), key=lambda kv: kv[1], reverse=True)
            limit_n = max(1, min(top_n, 50))
            out = AnalysisTraceDegradeTopNResponse(
                ok=True,
                total_unique=len(rows),
                items=[AnalysisTraceDegradeItem(reason=k, count=v) for k, v in rows[:limit_n]],
            )
            ANALYSIS_TRACE_QUERY_COUNT.labels(endpoint="degrade_topn", status="success").inc()
            return out
        except Exception:  # noqa: BLE001
            ANALYSIS_TRACE_QUERY_COUNT.labels(endpoint="degrade_topn", status="failed").inc()
            raise
        finally:
            ANALYSIS_TRACE_QUERY_LATENCY.labels(endpoint="degrade_topn").observe(time.perf_counter() - started)

    @staticmethod
    def _apply_defaults_payload(data: AnalysisPayloadRequest) -> AnalysisPayloadRequest:
        """用 `AnalysisConfig` 补齐 options 中的默认模板、strict、行数上限等。"""
        cfg = get_app_config().analysis
        chart_mode = data.options.chart_mode
        if chart_mode not in {"auto", "minimal", "off"}:
            chart_mode = cfg.default_chart_mode
        return data.model_copy(
            update={
                "options": data.options.model_copy(
                    update={
                        "report_template": data.options.report_template or cfg.default_report_template,
                        "chart_mode": chart_mode,
                        "report_style": data.options.report_style or cfg.default_report_style,
                        "max_nl2sql_calls": data.options.max_nl2sql_calls or cfg.default_max_nl2sql_calls,
                        "max_rows_per_query": data.options.max_rows_per_query or cfg.default_max_rows_per_query,
                        "max_suggestions": data.options.max_suggestions or cfg.default_max_suggestions,
                        "strict": data.options.strict or cfg.strict_by_default,
                    }
                )
            }
        )

    @staticmethod
    def _apply_defaults_nl2sql(data: AnalysisNL2SQLRequest) -> AnalysisNL2SQLRequest:
        """同 `_apply_defaults_payload`，作用于 nl2sql 请求体。"""
        cfg = get_app_config().analysis
        chart_mode = data.options.chart_mode
        if chart_mode not in {"auto", "minimal", "off"}:
            chart_mode = cfg.default_chart_mode
        return data.model_copy(
            update={
                "options": data.options.model_copy(
                    update={
                        "report_template": data.options.report_template or cfg.default_report_template,
                        "chart_mode": chart_mode,
                        "report_style": data.options.report_style or cfg.default_report_style,
                        "max_nl2sql_calls": data.options.max_nl2sql_calls or cfg.default_max_nl2sql_calls,
                        "max_rows_per_query": data.options.max_rows_per_query or cfg.default_max_rows_per_query,
                        "max_suggestions": data.options.max_suggestions or cfg.default_max_suggestions,
                        "strict": data.options.strict or cfg.strict_by_default,
                    }
                )
            }
        )

