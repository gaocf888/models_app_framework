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

