from prometheus_client import Counter, Histogram

"""
Prometheus 指标定义模块。

统一在这里集中声明 HTTP、LLM、RAG、小模型、NL2SQL 等子系统的核心指标，
便于在代码中引用和在文档中对照维护。
"""

# HTTP 层通用指标：请求总数与延迟分布
REQUEST_COUNT = Counter(
    "http_requests_total",
    "Total HTTP requests",
    ["method", "path", "status"],
)

REQUEST_LATENCY = Histogram(
    "http_request_latency_seconds",
    "HTTP request latency in seconds",
    ["method", "path"],
)

# LLM 调用指标：每个模型的调用次数与延迟
LLM_REQUEST_COUNT = Counter(
    "llm_requests_total",
    "Total LLM requests",
    ["model"],
)

LLM_REQUEST_LATENCY = Histogram(
    "llm_request_latency_seconds",
    "LLM request latency in seconds",
    ["model"],
)

# RAG 指标：RAG 检索请求计数
RAG_QUERY_COUNT = Counter(
    "rag_queries_total",
    "Total RAG retrieval queries",
)

RAG_SEMANTIC_RECALL_COUNT = Counter(
    "rag_semantic_recall_total",
    "Total RAG semantic recall calls",
)

RAG_KEYWORD_RECALL_COUNT = Counter(
    "rag_keyword_recall_total",
    "Total RAG keyword recall calls",
)

RAG_METADATA_RECALL_COUNT = Counter(
    "rag_metadata_recall_total",
    "Total RAG metadata recall calls",
)

RAG_RERANK_COUNT = Counter(
    "rag_rerank_total",
    "Total RAG rerank calls",
)

RAG_DOC_DELETE_COUNT = Counter(
    "rag_doc_delete_total",
    "Total RAG document deletions",
    ["namespace"],
)

# 小模型通道指标：按小模型名称统计已处理帧数量
SMALL_MODEL_FRAMES_PROCESSED = Counter(
    "small_model_frames_processed_total",
    "Total frames processed by small model inference",
    ["model_name"],
)

# NL2SQL 指标：查询次数与错误次数
NL2SQL_QUERY_COUNT = Counter(
    "nl2sql_queries_total",
    "Total NL2SQL queries",
)

NL2SQL_QUERY_ERROR_COUNT = Counter(
    "nl2sql_query_errors_total",
    "Total NL2SQL query errors",
)

# Analysis 指标（`AnalysisGraphRunner` / `AnalysisService`）：请求生命周期、各图节点耗时、
# 单次分析内 NL2SQL 子调用次数、strict/质量门等降级计数。
ANALYSIS_REQUEST_COUNT = Counter(
    "analysis_requests_total",
    "Total analysis requests",
    ["analysis_type", "data_mode", "status"],
)

ANALYSIS_NODE_LATENCY = Histogram(
    "analysis_node_latency_seconds",
    "Analysis node latency in seconds",
    ["node", "analysis_type"],
)

ANALYSIS_NL2SQL_CALL_COUNT = Counter(
    "analysis_nl2sql_calls_total",
    "Total analysis NL2SQL sub-calls",
    ["analysis_type", "status"],
)

ANALYSIS_DEGRADE_COUNT = Counter(
    "analysis_degrade_total",
    "Total analysis degrade events",
    ["reason"],
)

# Analysis Trace 运维指标（`AnalysisService` 列表/统计/趋势/TopN）：查询结果、延迟、趋势缓存、Redis 索引惰性清理。
ANALYSIS_TRACE_QUERY_COUNT = Counter(
    "analysis_trace_queries_total",
    "Total analysis trace query calls",
    ["endpoint", "status"],
)

ANALYSIS_TRACE_QUERY_LATENCY = Histogram(
    "analysis_trace_query_latency_seconds",
    "Analysis trace query latency in seconds",
    ["endpoint"],
)

ANALYSIS_TRACE_TREND_CACHE_HIT_COUNT = Counter(
    "analysis_trace_trend_cache_hits_total",
    "Total analysis trace trend cache hits",
)

ANALYSIS_TRACE_TREND_CACHE_MISS_COUNT = Counter(
    "analysis_trace_trend_cache_miss_total",
    "Total analysis trace trend cache misses",
)

ANALYSIS_TRACE_TREND_CACHE_INVALIDATE_COUNT = Counter(
    "analysis_trace_trend_cache_invalidate_total",
    "Total analysis trace trend cache invalidations",
)

ANALYSIS_TRACE_INDEX_CLEANUP_COUNT = Counter(
    "analysis_trace_index_cleanup_total",
    "Total analysis trace stale index cleanups",
    ["index_type"],
)

# Inspection Extract 指标：请求量、解析与 LLM 耗时、输出记录数、校验失败数。
INSPECT_EXTRACT_REQUEST_COUNT = Counter(
    "inspect_extract_requests_total",
    "Total inspection extract requests",
    ["status"],
)

INSPECT_EXTRACT_PARSE_LATENCY = Histogram(
    "inspect_extract_parse_latency_seconds",
    "Inspection extract parse latency in seconds",
)

INSPECT_EXTRACT_LLM_LATENCY = Histogram(
    "inspect_extract_llm_latency_seconds",
    "Inspection extract LLM latency in seconds",
)

INSPECT_EXTRACT_RECORD_COUNT = Counter(
    "inspect_extract_records_total",
    "Total structured records extracted",
)

INSPECT_EXTRACT_VALIDATION_FAIL_COUNT = Counter(
    "inspect_extract_validation_fail_total",
    "Total validation failures in extracted records",
)

