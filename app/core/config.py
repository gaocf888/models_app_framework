from __future__ import annotations

import os
from dataclasses import dataclass, field
from urllib.parse import quote
from functools import lru_cache
from typing import Any, Dict


@dataclass
class LLMModelConfig:
    """
    单个大模型配置（既支持大语言模型也支持多模态模型）。
    """

    model_id: str
    endpoint: str
    api_key: str | None = None
    max_tokens: int = 2048
    temperature: float = 0.7
    extras: Dict[str, Any] = field(default_factory=dict)


@dataclass
class LLMConfig:
    """
    大模型配置集合。
    """

    default_model: str
    models: Dict[str, LLMModelConfig] = field(default_factory=dict)


@dataclass
class GraphSchemaNodeConfig:
    """
    GraphRAG 节点类型配置（可选领域本体定义）。
    """

    name: str
    labels: list[str]
    key_fields: list[str]
    properties: list[str] = field(default_factory=list)


@dataclass
class GraphSchemaRelationConfig:
    """
    GraphRAG 关系类型配置。
    """

    name: str
    type: str
    from_node: str
    to_node: str
    properties: list[str] = field(default_factory=list)


@dataclass
class GraphSchemaConfig:
    """
    GraphRAG 领域本体 / 图 Schema 配置。

    - enabled=False 时，GraphIngestionService 采用 schema-less 宽松模式；
    - enabled=True 时，根据 nodes/relations 中的定义做类型映射与校验。
    """

    enabled: bool = False
    nodes: Dict[str, GraphSchemaNodeConfig] = field(default_factory=dict)
    relations: Dict[str, GraphSchemaRelationConfig] = field(default_factory=dict)


@dataclass
class GraphHybridStrategyConfig:
    """
    GraphRAG 检索与融合策略配置。
    """

    # vector | graph | hybrid
    mode: str = "vector"
    vector_weight: float = 0.6
    graph_weight: float = 0.4
    max_context_items: int = 20
    graph_hops: int = 1
    max_graph_items: int = 20
    use_intent_routing: bool = False
    relation_keywords: list[str] = field(
        default_factory=lambda: ["关系", "关联", "依赖", "影响", "链路", "路径", "因果", "上游", "下游", "协同", "冲突"]
    )
    relation_keywords_en: list[str] = field(
        default_factory=lambda: ["relationship", "dependency", "impact", "path", "cause"]
    )
    definition_keywords: list[str] = field(default_factory=lambda: ["是什么", "定义", "说明", "介绍", "概念", "原理"])
    definition_keywords_en: list[str] = field(
        default_factory=lambda: ["what is", "definition", "overview", "intro"]
    )
    routed_relation_graph_weight: float = 0.6
    routed_relation_vector_weight: float = 0.4
    routed_relation_graph_hops: int = 2
    routed_relation_max_graph_items: int = 24
    routed_definition_vector_weight: float = 0.7
    routed_definition_graph_weight: float = 0.3


@dataclass
class GraphRAGConfig:
    """
    GraphRAG 总体配置（Neo4j + LangChain Graph）。
    """

    enabled: bool = False

    # Neo4j 连接信息（如未配置，则禁用 GraphRAG）
    uri: str | None = None
    username: str | None = None
    password: str | None = None
    database: str | None = None

    # 可选：配置文件路径（如 graph_schema.yaml），便于通过外部 YAML 定义 schema
    schema_config_path: str | None = None

    # 领域本体 / Schema（可选）
    schema: GraphSchemaConfig = field(default_factory=GraphSchemaConfig)

    # 混合检索策略
    strategy: GraphHybridStrategyConfig = field(default_factory=GraphHybridStrategyConfig)
    # 实体抽取与图事实输出策略（工程化可调参数）
    entity_min_len: int = 2
    entity_max_len: int = 24
    zh_entity_max_len: int = 8
    en_entity_min_len: int = 2
    en_entity_max_len: int = 20
    max_entities_per_chunk: int = 40
    min_cooccur_weight: int = 1
    fact_template_entity: str = "[Graph] 实体 {entity} 相关片段: {text}"
    fact_template_cooccur: str = "[Graph] 实体共现: {a} -> {b} (weight={weight})"


@dataclass
class ElasticsearchConfig:
    """
    Elasticsearch / EasySearch 存储配置（EasySearch 兼容 ES API）。
    """

    hosts: list[str] = field(default_factory=lambda: ["http://localhost:9200"])
    username: str | None = None
    password: str | None = None
    api_key: str | None = None
    verify_certs: bool = False
    request_timeout: int = 30
    index_name: str = "rag_knowledge_base"
    index_alias: str = "rag_knowledge_base"
    index_version: int = 1
    auto_migrate_on_start: bool = True
    vector_field: str = "embedding"
    docs_index_name: str = "rag_docs"
    docs_index_alias: str = "rag_docs_current"
    docs_index_version: int = 1
    jobs_index_name: str = "rag_jobs"
    jobs_index_alias: str = "rag_jobs_current"
    jobs_index_version: int = 1


@dataclass
class HybridRetrievalConfig:
    """
    混合检索配置：语义召回 + 关键词召回 + RRF 融合 + CrossEncoder 重排。
    """

    enabled: bool = True
    semantic_top_k: int = 24
    keyword_top_k: int = 24
    metadata_top_k: int = 12
    metadata_recall_enabled: bool = True
    rrf_k: int = 60
    rerank_top_n: int = 12
    reranker_model_path: str | None = None
    reranker_model_name: str = "BAAI/bge-reranker-large"
    # 可选：显式指定 CrossEncoder 设备，例如 cpu / cuda / cuda:1。
    # 为空时使用 sentence-transformers 默认设备选择。
    reranker_device: str | None = None


@dataclass
class RAGSceneProfile:
    top_k: int = 5
    semantic_top_k: int = 24
    keyword_top_k: int = 24
    rerank_top_n: int = 12


@dataclass
class RAGSceneProfilesConfig:
    llm_inference: RAGSceneProfile = field(default_factory=lambda: RAGSceneProfile(top_k=5, semantic_top_k=24, keyword_top_k=24, rerank_top_n=12))
    chatbot: RAGSceneProfile = field(default_factory=lambda: RAGSceneProfile(top_k=6, semantic_top_k=28, keyword_top_k=28, rerank_top_n=14))
    analysis: RAGSceneProfile = field(default_factory=lambda: RAGSceneProfile(top_k=8, semantic_top_k=32, keyword_top_k=32, rerank_top_n=16))
    nl2sql: RAGSceneProfile = field(default_factory=lambda: RAGSceneProfile(top_k=5, semantic_top_k=20, keyword_top_k=20, rerank_top_n=12))


@dataclass
class RAGContentFetchConfig:
    """
    `content` 为 http(s) 文件 URL 时的拉取行为（需显式开启；建议配合主机白名单）。

    - 开启后：`source_type` 为 pdf/docx/doc 时下载到临时文件再解析；text/markdown/html 时下载为 UTF-8 文本。
    - 默认拒绝解析到私网/回环等地址，降低 SSRF 风险；生产建议设置 `allow_hosts`。
    """

    enabled: bool = False
    max_bytes: int = 52428800
    timeout_s: float = 120.0
    allow_hosts: list[str] = field(default_factory=list)
    block_private_ips: bool = True
    bearer_token: str | None = None
    header_name: str | None = None
    header_value: str | None = None


@dataclass
class RAGIngestionConfig:
    """
    知识摄入平台相关配置（对齐《企业级 RAG 文档摄入与检索一体化改造设计稿》§4）。
    """

    ingest_async_enabled: bool = True
    max_concurrency: int = 4
    ingest_batch_size: int = 32
    pipeline_version: str = "1.0.0"
    default_chunk_strategy: str = "structure"
    chunk_size: int = 500
    chunk_overlap: int = 80
    min_chunk_size: int = 40
    cleaning_profile: str = "normal"
    clean_remove_header_footer: bool = True
    clean_merge_duplicate_paragraphs: bool = True
    clean_fix_encoding_noise: bool = True
    clean_min_repeated_line_pages: int = 2
    tenant_id_default: str | None = None
    # RUNNING 任务超过该秒数未更新，判定为卡死并自动转 FAILED。
    running_stuck_timeout_seconds: int = 1800


@dataclass
class RAGAgenticConfig:
    """
    Agentic 检索策略配置（多步计划检索）。

    这些参数用于在线调优：
    - 子问题数量上限；
    - 检索并发度；
    - 子问题融合权重（主问题/拆分子问题/场景增强子问题）；
    - 每个子问题检索预算下限。
    """

    enabled: bool = True
    max_subqueries: int = 4
    max_parallel_workers: int = 4
    per_step_k_floor: int = 3
    main_query_weight: float = 1.0
    split_query_weight: float = 0.8
    scene_boost_weight: float = 0.7
    enable_scene_boost: bool = True


@dataclass
class RAGConfig:
    """
    RAG 与上下文相关配置。
    """

    enable_rag_by_default: bool = True
    enable_context_by_default: bool = True
    top_k: int = 5
    vector_store_type: str = "es"
    faiss_index_dir: str = "./data/faiss"
    es: ElasticsearchConfig = field(default_factory=ElasticsearchConfig)
    hybrid: HybridRetrievalConfig = field(default_factory=HybridRetrievalConfig)
    scene_profiles: RAGSceneProfilesConfig = field(default_factory=RAGSceneProfilesConfig)

    # 嵌入模型配置（离线优先、在线回退，环境变量 EMBEDDING_MODEL_PATH / EMBEDDING_MODEL_NAME）
    embedding_model_path: str | None = None
    embedding_model_name: str = "BAAI/bge-small-zh-v1.5"

    # GraphRAG（Neo4j + LangChain Graph），默认关闭，与向量 RAG 并行可选
    graph: GraphRAGConfig = field(default_factory=GraphRAGConfig)

    ingestion: RAGIngestionConfig = field(default_factory=RAGIngestionConfig)
    content_fetch: RAGContentFetchConfig = field(default_factory=RAGContentFetchConfig)
    agentic: RAGAgenticConfig = field(default_factory=RAGAgenticConfig)


@dataclass
class LoggingConfig:
    """
    日志相关配置。
    """

    level: str = "INFO"
    json_format: bool = False
    log_file: str | None = None
    file_enabled: bool = False
    file_max_bytes: int = 100 * 1024 * 1024
    file_backup_count: int = 10
    file_compress: bool = True


@dataclass
class PromptABVariant:
    """
    单个 Prompt 策略版本的元数据。
    """

    name: str
    weight: float = 1.0
    description: str | None = None


@dataclass
class PromptABConfig:
    """
    Prompt A/B 测试配置（按场景划分）。
    """

    variants: Dict[str, PromptABVariant] = field(default_factory=dict)


@dataclass
class PromptConfig:
    """
    提示词与 A/B 策略总体配置。
    """

    chatbot: PromptABConfig = field(default_factory=PromptABConfig)
    analysis: PromptABConfig = field(default_factory=PromptABConfig)
    nl2sql: PromptABConfig = field(default_factory=PromptABConfig)


@dataclass
class MinerUConfig:
    """
    MinerU 独立容器解析（PDF→Markdown）相关配置。

    io_path 为容器内与 mineru-deploy 共享卷挂载点，须与 docker-compose 中
    MINERU_IO_HOST_PATH → /workspace/mineru-io 一致；MinerU 容器内对应路径通常为 /io。
    """

    enabled: bool = False
    base_url: str = "http://mineru-api:8000"
    timeout_s: float = 1200.0
    max_concurrent: int = 1
    io_path: str = "/workspace/mineru-io"
    # 与 mineru-api /file_parse 表单字段对齐（扫描件建议 parse_method=ocr）
    backend: str = "pipeline"
    parse_method: str = "ocr"
    language: str = "ch"
    formula_enable: bool = True
    table_enable: bool = True
    # 按页分段调用 /file_parse（0 表示不分段，整份 PDF 一次解析）
    page_batch_size: int = 0
    # 抽样页平均可提取字符数低于该阈值则视为「图片/扫描 PDF」，走 MinerU
    pdf_scanned_max_avg_chars: float = 40.0
    # 多 worker 时 Redis 信号量键前缀（与 REDIS_URL 联用）
    redis_semaphore_key_prefix: str = "mineru:ingest"
    # API 路径（一般无需改）
    file_parse_path: str = "/file_parse"
    # 与 mineru-api 的 MINERU_API_OUTPUT_ROOT 最后一级目录名一致（共享 IO 卷上的相对路径）
    disk_fallback_subdir: str = "mineru-output"


@dataclass
class ChatbotConfig:
    """
    智能客服（LangGraph 编排）配置。

    说明：
    - 该配置用于统一管理 `CHATBOT_*` 环境变量，避免业务代码散落读取 env。
    - 当前实现以流式接口为主，非流式接口仍保留兼容路径（deprecated）。
    """

    graph_enabled: bool = True
    intent_enabled: bool = True
    intent_output_labels: list[str] = field(default_factory=lambda: ["kb_qa", "clarify", "data_query"])
    crag_enabled: bool = True
    fallback_legacy_on_error: bool = True
    persist_partial_on_disconnect: bool = True
    # 图执行总时长预算（毫秒），用于硬超时保护。
    max_graph_latency_ms: int = 60000
    history_limit: int = 20
    crag_max_attempts: int = 2
    crag_min_score: float = 0.55
    rag_engine_mode: str = "agentic"
    rag_engine_fallback: str = "hybrid"
    max_rewrite_query_length: int = 256
    # checkpoint backend：none | memory | redis（redis 依赖可选，未安装会自动降级）
    checkpoint_backend: str = "none"
    checkpoint_redis_url: str | None = None
    checkpoint_namespace: str = "chatbot_graph"
    # 锅炉/管材故障域 + 限定 namespace 相似案例（见 enterprise 文档 §14）
    similar_case_enabled: bool = False
    similar_case_namespace: str = "事故案例"
    similar_case_top_k: int = 5
    fault_detect_enabled: bool = True
    fault_vision_enabled: bool = True
    fault_detect_mode: str = "hybrid"
    fault_min_confidence: float = 0.5
    # 结构化问数走 NL2SQL（意图 data_query），与文档 RAG（kb_qa）分流
    nl2sql_route_enabled: bool = True
    # 未传 prompt_version 时使用的客服模板版本（与 configs/prompts.yaml 中 chatbot.version 对齐）
    default_prompt_version: str = "boiler_v1"
    # 回答结束后关联问题推荐（规则 + 片段 + LLM）
    suggested_questions_enabled: bool = True
    suggested_questions_max: int = 5
    # 图片预处理总开关：true 时在 ChatbotService 入口对 image_urls 执行下载+缩放+压缩+存储（local/minio）。
    image_preprocess_enabled: bool = True
    # 统一最长边（像素）：超过即等比缩放，降低视觉 token 与传输开销。
    image_max_edge: int = 1280
    # 触发有损压缩阈值（MB）：原图超过该体积时按 image_jpeg_quality 压缩；否则高质量保存。
    image_compress_threshold_mb: float = 2.0
    # 有损压缩质量（JPEG 1~95）：默认 80，兼顾可识别度与体积。
    image_jpeg_quality: int = 80
    # 图片存储后端：minio | local。默认 minio（推荐，便于多实例共享与给 vLLM 提供可访问 URL）。
    image_storage_backend: str = "minio"
    # 本地落盘目录（可相对 app 目录）；用于历史会话图片回显与静态服务。
    image_store_dir: str = "runtime/chatbot_images"
    # 对外访问前缀（由 main.py 挂载 StaticFiles），默认 /chatbot/media。
    image_public_path: str = "/chatbot/media"
    # --- MinIO 配置（image_storage_backend=minio 时生效） ---
    image_minio_endpoint: str = "models-app-minio:9000"
    image_minio_access_key: str = "minioadmin"
    image_minio_secret_key: str = "minioadmin"
    image_minio_bucket: str = "chatbot-images"
    image_minio_secure: bool = False
    image_minio_auto_create_bucket: bool = True
    image_minio_presign_ttl_seconds: int = 900


@dataclass
class AnalysisConfig:
    """
    综合分析（双入口 + LangGraph 编排）的环境配置映射目标。

    含：默认报告与 NL2SQL 选项、strict、payload/nl2sql 质量阈值、trace 后端与 ES 连接、
    LangGraph checkpoint、是否启用 nl2sql 路径上的 LLM 意图/计划分阶段调用。
    """

    default_report_template: str = "standard"
    default_chart_mode: str = "auto"  # auto | minimal | off
    default_report_style: str = "standard"
    default_max_nl2sql_calls: int = 6
    default_max_rows_per_query: int = 2000
    default_max_suggestions: int = 8
    synthesis_timeout_seconds: float = 90.0
    strict_by_default: bool = False
    trace_backend: str = "redis"  # redis | memory
    trace_ttl_minutes: int = 1440
    trace_max_items: int = 10000
    trace_trend_cache_ttl_seconds: int = 30
    trace_lazy_cleanup_batch_size: int = 200
    trace_es_hosts: str = "http://localhost:9200"
    trace_es_index: str = "analysis_trace_archive"
    trace_es_verify_certs: bool = False
    trace_es_timeout_seconds: int = 10
    trace_es_username: str = ""
    trace_es_password: str = ""
    trace_es_api_key: str = ""
    payload_time_window_coverage_min: float = 0.6
    payload_anomaly_rate_max: float = 0.2
    payload_missing_key_rate_max: float = 0.3
    nl2sql_time_window_coverage_min: float = 0.5
    nl2sql_anomaly_rate_max: float = 0.25
    nl2sql_missing_key_rate_max: float = 0.35
    # LangGraph checkpoint：none | memory | redis（与 Chatbot 一致；redis 依赖缺失时编译阶段会降级为无 checkpoint）
    checkpoint_backend: str = "none"
    checkpoint_redis_url: str | None = None
    checkpoint_namespace: str = "analysis_graph"
    # NL2SQL 综合分析：是否启用「意图 LLM + 数据计划 LLM」分阶段结构化规划（关闭则仅用 JSON 模板/内置默认）
    nl2sql_llm_planner_enabled: bool = True


def _default_inspection_v2_shading_fills() -> list[str]:
    """常见「超标」底纹 RGB（无 #，大写），可通过环境变量覆盖。"""
    return [
        "FF0000",
        "C00000",
        "F79646",
        "E6B8B7",
        "F2DCDB",
        "FF6666",
        "C0504D",
        "943634",
    ]


def _normalize_inspection_shading_fill_hex(raw: str) -> str:
    s = raw.strip().upper().replace("#", "")
    if len(s) == 8 and s.startswith("FF"):
        s = s[2:]
    if len(s) >= 6:
        return s[-6:]
    return s


@dataclass
class InspectionExtractConfig:
    """
    检修报告结构化提取模块配置。
    """

    enabled: bool = True
    strict_default: bool = False
    max_repair_retries: int = 1
    prompt_version: str = "v1"
    model_name: str | None = None
    llm_timeout_seconds: float = 180.0
    llm_max_tokens_parse: int = 1024
    llm_max_tokens_classify: int = 1024
    llm_max_tokens_repair: int = 768
    log_llm_raw_response: bool = False
    log_llm_raw_max_chars: int = 2000
    # 排障：打印送入 LLM 的完整 parse 分块正文（生产慎用）；0 表示不按字符截断（仍按段拆分日志）
    # 运行时默认：若环境变量未设置，则与 log_llm_raw_response 一致（见 load_app_config）
    log_parse_chunk_full: bool = False
    log_parse_chunk_max_chars: int = 0
    # v1 | v2：v2 使用独立 docx 摄入（底纹等），与旧解析并行；默认 v1 不替换现网
    pipeline_version: str = "v1"
    # docx 单元格底纹 w:fill 命中下列十六进制时标记为「超标候选」（与阈值规则并存）
    v2_shading_candidate_fills: list[str] = field(default_factory=_default_inspection_v2_shading_fills)
    # V2：Processing Unit 分块后每块最大字符；classify 批大小（与文档 20～40 条建议对齐）
    v2_parse_unit_max_chars: int = 6000
    v2_classify_batch_size: int = 40


@dataclass
class AppConfig:
    """
    应用全局配置。
    """

    env: str = "dev"
    llm: LLMConfig = field(default_factory=lambda: LLMConfig(default_model="default"))
    rag: RAGConfig = field(default_factory=RAGConfig)
    logging: LoggingConfig = field(default_factory=LoggingConfig)
    prompt: PromptConfig = field(default_factory=PromptConfig)
    mineru: MinerUConfig = field(default_factory=MinerUConfig)
    chatbot: ChatbotConfig = field(default_factory=ChatbotConfig)
    analysis: AnalysisConfig = field(default_factory=AnalysisConfig)
    inspection_extract: InspectionExtractConfig = field(default_factory=InspectionExtractConfig)


@dataclass
class DatabaseConfig:
    """
    数据库连接配置。

    说明：
    - 为了便于开发，这里提供了一个默认的 MySQL 连接信息；
    - 在生产环境中，强烈建议通过环境变量覆盖这些默认值。
    """

    url: str
    user: str
    password: str
    host: str
    port: int
    database: str


def _load_from_env() -> AppConfig:
    """
    从环境变量加载最小化配置。
    说明：
    - 当前已覆盖 RAG 的 ES/EasySearch、混合检索、重排模型、场景化参数等关键配置；
    - 后续可扩展为从 YAML/JSON/配置中心统一加载。
    """
    env = os.getenv("APP_ENV", "dev")

    # 简单示例：从环境变量读取一个默认 vLLM endpoint
    default_model_id = os.getenv("LLM_DEFAULT_MODEL", "default")
    default_endpoint = os.getenv("LLM_DEFAULT_ENDPOINT", "http://localhost:8001/v1")
    default_api_key = os.getenv("LLM_DEFAULT_API_KEY")

    llm_cfg = LLMConfig(
        default_model=default_model_id,
        models={
            default_model_id: LLMModelConfig(
                model_id=default_model_id,
                endpoint=default_endpoint,
                api_key=default_api_key,
            )
        },
    )

    logging_cfg = LoggingConfig(
        level=os.getenv("LOG_LEVEL", "INFO"),
        json_format=os.getenv("LOG_JSON", "false").lower() == "true",
        log_file=os.getenv("LOG_FILE"),
        file_enabled=os.getenv("LOG_FILE_ENABLED", "false").lower() == "true",
        file_max_bytes=max(1024 * 1024, int(os.getenv("LOG_FILE_MAX_BYTES", str(100 * 1024 * 1024)))),
        file_backup_count=max(1, int(os.getenv("LOG_FILE_BACKUP_COUNT", "10"))),
        file_compress=os.getenv("LOG_FILE_COMPRESS", "true").lower() == "true",
    )

    # 数据库配置：支持环境变量覆盖，默认使用用户提供的 MySQL 连接信息。
    db_user = os.getenv("DB_USER", "root")
    db_password = os.getenv("DB_PASSWORD", "1qaz@4321")
    db_host = os.getenv("DB_HOST", "124.222.37.179")
    db_name = os.getenv("DB_NAME", "boiler")
    db_port = int(os.getenv("DB_PORT", "3306"))
    # userinfo 中的 @ : # 等必须百分号编码，否则第一个 @ 会被当成「凭据结束」，例如密码 1qaz@4321 会把 host 错解析成 4321@124...
    db_url = os.getenv(
        "DB_URL",
        "mysql+aiomysql://"
        f"{quote(db_user, safe='')}:{quote(db_password, safe='')}@{db_host}:{db_port}/{db_name}",
    )

    db_cfg = DatabaseConfig(
        url=db_url,
        user=db_user,
        password=db_password,
        host=db_host,
        port=db_port,
        database=db_name,
    )

    def _split_csv_env(name: str, default_csv: str) -> list[str]:
        raw = os.getenv(name, default_csv)
        return [x.strip() for x in raw.split(",") if x.strip()]

    graph_strategy = GraphHybridStrategyConfig(
        mode=os.getenv("GRAPH_RAG_MODE", "vector").lower(),
        vector_weight=float(os.getenv("GRAPH_RAG_VECTOR_WEIGHT", "0.6")),
        graph_weight=float(os.getenv("GRAPH_RAG_GRAPH_WEIGHT", "0.4")),
        max_context_items=int(os.getenv("GRAPH_RAG_MAX_CONTEXT_ITEMS", "20")),
        graph_hops=int(os.getenv("GRAPH_RAG_GRAPH_HOPS", "1")),
        max_graph_items=int(os.getenv("GRAPH_RAG_MAX_GRAPH_ITEMS", "20")),
        use_intent_routing=os.getenv("GRAPH_RAG_USE_INTENT_ROUTING", "false").lower() == "true",
        relation_keywords=_split_csv_env("GRAPH_RAG_RELATION_KEYWORDS", "关系,关联,依赖,影响,链路,路径,因果,上游,下游,协同,冲突"),
        relation_keywords_en=_split_csv_env(
            "GRAPH_RAG_RELATION_KEYWORDS_EN", "relationship,dependency,impact,path,cause"
        ),
        definition_keywords=_split_csv_env("GRAPH_RAG_DEFINITION_KEYWORDS", "是什么,定义,说明,介绍,概念,原理"),
        definition_keywords_en=_split_csv_env(
            "GRAPH_RAG_DEFINITION_KEYWORDS_EN", "what is,definition,overview,intro"
        ),
        routed_relation_graph_weight=float(os.getenv("GRAPH_RAG_ROUTED_RELATION_GRAPH_WEIGHT", "0.6")),
        routed_relation_vector_weight=float(os.getenv("GRAPH_RAG_ROUTED_RELATION_VECTOR_WEIGHT", "0.4")),
        routed_relation_graph_hops=int(os.getenv("GRAPH_RAG_ROUTED_RELATION_GRAPH_HOPS", "2")),
        routed_relation_max_graph_items=int(os.getenv("GRAPH_RAG_ROUTED_RELATION_MAX_GRAPH_ITEMS", "24")),
        routed_definition_vector_weight=float(os.getenv("GRAPH_RAG_ROUTED_DEFINITION_VECTOR_WEIGHT", "0.7")),
        routed_definition_graph_weight=float(os.getenv("GRAPH_RAG_ROUTED_DEFINITION_GRAPH_WEIGHT", "0.3")),
    )
    es_hosts_raw = os.getenv("RAG_ES_HOSTS", "http://localhost:9200")
    es_hosts = [h.strip() for h in es_hosts_raw.split(",") if h.strip()]
    es_cfg = ElasticsearchConfig(
        hosts=es_hosts or ["http://localhost:9200"],
        username=os.getenv("RAG_ES_USERNAME", "admin") or None,
        password=os.getenv("RAG_ES_PASSWORD", "wQ=5c-^PRiG0#FN6PJAn^WaR") or None,
        api_key=os.getenv("RAG_ES_API_KEY") or None,
        verify_certs=os.getenv("RAG_ES_VERIFY_CERTS", "false").lower() == "true",
        request_timeout=int(os.getenv("RAG_ES_REQUEST_TIMEOUT", "30")),
        index_name=os.getenv("RAG_ES_INDEX_NAME", "rag_knowledge_base"),
        index_alias=os.getenv("RAG_ES_INDEX_ALIAS", "rag_knowledge_base"),
        index_version=int(os.getenv("RAG_ES_INDEX_VERSION", "1")),
        auto_migrate_on_start=os.getenv("RAG_ES_AUTO_MIGRATE_ON_START", "true").lower() == "true",
        vector_field=os.getenv("RAG_ES_VECTOR_FIELD", "embedding"),
        docs_index_name=os.getenv("RAG_ES_DOCS_INDEX_NAME", "rag_docs"),
        docs_index_alias=os.getenv("RAG_ES_DOCS_INDEX_ALIAS", "rag_docs_current"),
        docs_index_version=int(os.getenv("RAG_ES_DOCS_INDEX_VERSION", "1")),
        jobs_index_name=os.getenv("RAG_ES_JOBS_INDEX_NAME", "rag_jobs"),
        jobs_index_alias=os.getenv("RAG_ES_JOBS_INDEX_ALIAS", "rag_jobs_current"),
        jobs_index_version=int(os.getenv("RAG_ES_JOBS_INDEX_VERSION", "1")),
    )
    hybrid_cfg = HybridRetrievalConfig(
        enabled=os.getenv("RAG_HYBRID_ENABLED", "true").lower() == "true",
        semantic_top_k=int(os.getenv("RAG_HYBRID_SEMANTIC_TOP_K", "24")),
        keyword_top_k=int(os.getenv("RAG_HYBRID_KEYWORD_TOP_K", "24")),
        metadata_top_k=int(os.getenv("RAG_HYBRID_METADATA_TOP_K", "12")),
        metadata_recall_enabled=os.getenv("RAG_HYBRID_METADATA_RECALL_ENABLED", "true").lower() == "true",
        rrf_k=int(os.getenv("RAG_HYBRID_RRF_K", "60")),
        rerank_top_n=int(os.getenv("RAG_HYBRID_RERANK_TOP_N", "12")),
        reranker_model_path=os.getenv("RAG_RERANKER_MODEL_PATH") or None,
        reranker_model_name=os.getenv("RAG_RERANKER_MODEL_NAME", "BAAI/bge-reranker-large"),
        reranker_device=os.getenv("RAG_RERANKER_DEVICE") or None,
    )
    scene_profiles_cfg = RAGSceneProfilesConfig(
        llm_inference=RAGSceneProfile(
            top_k=int(os.getenv("RAG_SCENE_LLM_TOP_K", "5")),
            semantic_top_k=int(os.getenv("RAG_SCENE_LLM_SEMANTIC_TOP_K", "24")),
            keyword_top_k=int(os.getenv("RAG_SCENE_LLM_KEYWORD_TOP_K", "24")),
            rerank_top_n=int(os.getenv("RAG_SCENE_LLM_RERANK_TOP_N", "12")),
        ),
        chatbot=RAGSceneProfile(
            top_k=int(os.getenv("RAG_SCENE_CHATBOT_TOP_K", "6")),
            semantic_top_k=int(os.getenv("RAG_SCENE_CHATBOT_SEMANTIC_TOP_K", "28")),
            keyword_top_k=int(os.getenv("RAG_SCENE_CHATBOT_KEYWORD_TOP_K", "28")),
            rerank_top_n=int(os.getenv("RAG_SCENE_CHATBOT_RERANK_TOP_N", "14")),
        ),
        analysis=RAGSceneProfile(
            top_k=int(os.getenv("RAG_SCENE_ANALYSIS_TOP_K", "8")),
            semantic_top_k=int(os.getenv("RAG_SCENE_ANALYSIS_SEMANTIC_TOP_K", "32")),
            keyword_top_k=int(os.getenv("RAG_SCENE_ANALYSIS_KEYWORD_TOP_K", "32")),
            rerank_top_n=int(os.getenv("RAG_SCENE_ANALYSIS_RERANK_TOP_N", "16")),
        ),
        nl2sql=RAGSceneProfile(
            top_k=int(os.getenv("RAG_SCENE_NL2SQL_TOP_K", "5")),
            semantic_top_k=int(os.getenv("RAG_SCENE_NL2SQL_SEMANTIC_TOP_K", "20")),
            keyword_top_k=int(os.getenv("RAG_SCENE_NL2SQL_KEYWORD_TOP_K", "20")),
            rerank_top_n=int(os.getenv("RAG_SCENE_NL2SQL_RERANK_TOP_N", "12")),
        ),
    )
    graph_cfg = GraphRAGConfig(
        enabled=os.getenv("GRAPH_RAG_ENABLED", "false").lower() == "true",
        uri=os.getenv("NEO4J_URI"),
        username=os.getenv("NEO4J_USERNAME"),
        password=os.getenv("NEO4J_PASSWORD"),
        database=os.getenv("NEO4J_DATABASE") or None,
        schema_config_path=os.getenv("GRAPH_SCHEMA_CONFIG_PATH") or None,
        strategy=graph_strategy,
        entity_min_len=int(os.getenv("GRAPH_ENTITY_MIN_LEN", "2")),
        entity_max_len=int(os.getenv("GRAPH_ENTITY_MAX_LEN", "24")),
        zh_entity_max_len=int(os.getenv("GRAPH_ZH_ENTITY_MAX_LEN", "8")),
        en_entity_min_len=int(os.getenv("GRAPH_EN_ENTITY_MIN_LEN", "2")),
        en_entity_max_len=int(os.getenv("GRAPH_EN_ENTITY_MAX_LEN", "20")),
        max_entities_per_chunk=int(os.getenv("GRAPH_MAX_ENTITIES_PER_CHUNK", "40")),
        min_cooccur_weight=int(os.getenv("GRAPH_MIN_COOCCUR_WEIGHT", "1")),
        fact_template_entity=os.getenv("GRAPH_FACT_TEMPLATE_ENTITY", "[Graph] 实体 {entity} 相关片段: {text}"),
        fact_template_cooccur=os.getenv(
            "GRAPH_FACT_TEMPLATE_COOCCUR", "[Graph] 实体共现: {a} -> {b} (weight={weight})"
        ),
    )

    ingestion_cfg = RAGIngestionConfig(
        ingest_async_enabled=os.getenv("RAG_INGEST_ASYNC_ENABLED", "true").lower() == "true",
        max_concurrency=int(os.getenv("RAG_INGEST_MAX_CONCURRENCY", "4")),
        ingest_batch_size=int(os.getenv("RAG_INGEST_BATCH_SIZE", "32")),
        pipeline_version=os.getenv("RAG_PIPELINE_VERSION", "1.0.0"),
        default_chunk_strategy=os.getenv("RAG_DEFAULT_CHUNK_STRATEGY", "structure").lower(),
        chunk_size=int(os.getenv("RAG_CHUNK_SIZE", "500")),
        chunk_overlap=int(os.getenv("RAG_CHUNK_OVERLAP", "80")),
        min_chunk_size=int(os.getenv("RAG_MIN_CHUNK_SIZE", "40")),
        cleaning_profile=os.getenv("RAG_CLEANING_PROFILE", "normal").lower(),
        clean_remove_header_footer=os.getenv("RAG_CLEAN_REMOVE_HEADER_FOOTER", "true").lower() == "true",
        clean_merge_duplicate_paragraphs=os.getenv("RAG_CLEAN_MERGE_DUPLICATE_PARAGRAPHS", "true").lower() == "true",
        clean_fix_encoding_noise=os.getenv("RAG_CLEAN_FIX_ENCODING_NOISE", "true").lower() == "true",
        clean_min_repeated_line_pages=int(os.getenv("RAG_CLEAN_MIN_REPEATED_LINE_PAGES", "2")),
        tenant_id_default=os.getenv("RAG_TENANT_ID_DEFAULT") or None,
        running_stuck_timeout_seconds=max(60, int(os.getenv("RAG_RUNNING_STUCK_TIMEOUT_SECONDS", "1800"))),
    )
    agentic_cfg = RAGAgenticConfig(
        enabled=os.getenv("RAG_AGENTIC_ENABLED", "true").lower() == "true",
        max_subqueries=int(os.getenv("RAG_AGENTIC_MAX_SUBQUERIES", "4")),
        max_parallel_workers=int(os.getenv("RAG_AGENTIC_MAX_PARALLEL_WORKERS", "4")),
        per_step_k_floor=int(os.getenv("RAG_AGENTIC_PER_STEP_K_FLOOR", "3")),
        main_query_weight=float(os.getenv("RAG_AGENTIC_MAIN_QUERY_WEIGHT", "1.0")),
        split_query_weight=float(os.getenv("RAG_AGENTIC_SPLIT_QUERY_WEIGHT", "0.8")),
        scene_boost_weight=float(os.getenv("RAG_AGENTIC_SCENE_BOOST_WEIGHT", "0.7")),
        enable_scene_boost=os.getenv("RAG_AGENTIC_ENABLE_SCENE_BOOST", "true").lower() == "true",
    )

    content_fetch_allow = _split_csv_env("RAG_CONTENT_FETCH_ALLOW_HOSTS", "")
    content_fetch_cfg = RAGContentFetchConfig(
        enabled=os.getenv("RAG_CONTENT_FETCH_ENABLED", "false").lower() == "true",
        max_bytes=max(1024 * 1024, int(os.getenv("RAG_CONTENT_FETCH_MAX_BYTES", str(50 * 1024 * 1024)))),
        timeout_s=float(os.getenv("RAG_CONTENT_FETCH_TIMEOUT_S", "120")),
        allow_hosts=content_fetch_allow,
        block_private_ips=os.getenv("RAG_CONTENT_FETCH_BLOCK_PRIVATE", "true").lower() == "true",
        bearer_token=os.getenv("RAG_CONTENT_FETCH_BEARER_TOKEN") or None,
        header_name=os.getenv("RAG_CONTENT_FETCH_HEADER_NAME") or None,
        header_value=os.getenv("RAG_CONTENT_FETCH_HEADER_VALUE") or None,
    )

    rag_cfg = RAGConfig(
        enable_rag_by_default=os.getenv("RAG_ENABLE_BY_DEFAULT", "true").lower() == "true",
        enable_context_by_default=os.getenv("RAG_ENABLE_CONTEXT_BY_DEFAULT", "true").lower() == "true",
        top_k=int(os.getenv("RAG_TOP_K", "5")),
        vector_store_type=os.getenv("RAG_VECTOR_STORE_TYPE", "es"),
        faiss_index_dir=os.getenv("RAG_FAISS_INDEX_DIR", "./data/faiss"),
        es=es_cfg,
        hybrid=hybrid_cfg,
        scene_profiles=scene_profiles_cfg,
        embedding_model_path=os.getenv("EMBEDDING_MODEL_PATH") or None,
        embedding_model_name=os.getenv("EMBEDDING_MODEL_NAME", "BAAI/bge-small-zh-v1.5"),
        graph=graph_cfg,
        ingestion=ingestion_cfg,
        content_fetch=content_fetch_cfg,
        agentic=agentic_cfg,
    )

    mineru_cfg = MinerUConfig(
        enabled=os.getenv("MINERU_ENABLED", "false").lower() == "true",
        base_url=os.getenv("MINERU_BASE_URL", "http://mineru-api:8000").rstrip("/"),
        timeout_s=float(os.getenv("MINERU_TIMEOUT_S", "1200")),
        max_concurrent=max(1, int(os.getenv("MINERU_MAX_CONCURRENT", "1"))),
        io_path=os.getenv("MINERU_IO_CONTAINER_PATH", "/workspace/mineru-io"),
        backend=os.getenv("MINERU_BACKEND", "pipeline"),
        parse_method=os.getenv("MINERU_PARSE_METHOD", "ocr"),
        language=os.getenv("MINERU_LANGUAGE", "ch"),
        formula_enable=os.getenv("MINERU_FORMULA_ENABLE", "true").lower() == "true",
        table_enable=os.getenv("MINERU_TABLE_ENABLE", "true").lower() == "true",
        page_batch_size=max(0, int(os.getenv("MINERU_PAGE_BATCH_SIZE", "0"))),
        pdf_scanned_max_avg_chars=float(os.getenv("MINERU_PDF_SCANNED_MAX_AVG_CHARS", "40")),
        redis_semaphore_key_prefix=os.getenv("MINERU_REDIS_SEM_KEY_PREFIX", "mineru:ingest"),
        file_parse_path=os.getenv("MINERU_FILE_PARSE_PATH", "/file_parse"),
        disk_fallback_subdir=os.getenv("MINERU_DISK_FALLBACK_SUBDIR", "mineru-output"),
    )

    chatbot_cfg = ChatbotConfig(
        graph_enabled=os.getenv("CHATBOT_GRAPH_ENABLED", "true").lower() == "true",
        intent_enabled=os.getenv("CHATBOT_INTENT_ENABLED", "true").lower() == "true",
        intent_output_labels=_split_csv_env("CHATBOT_INTENT_OUTPUT_LABELS", "kb_qa,clarify,data_query"),
        crag_enabled=os.getenv("CHATBOT_CRAG_ENABLED", "true").lower() == "true",
        fallback_legacy_on_error=os.getenv("CHATBOT_FALLBACK_LEGACY_ON_ERROR", "true").lower() == "true",
        persist_partial_on_disconnect=os.getenv("CHATBOT_PERSIST_PARTIAL_ON_DISCONNECT", "true").lower() == "true",
        max_graph_latency_ms=max(1000, int(os.getenv("MAX_GRAPH_LATENCY_MS", "60000"))),
        history_limit=max(1, int(os.getenv("CHATBOT_HISTORY_LIMIT", "20"))),
        crag_max_attempts=max(1, int(os.getenv("CHATBOT_CRAG_MAX_ATTEMPTS", "2"))),
        crag_min_score=max(0.0, min(1.0, float(os.getenv("CHATBOT_CRAG_MIN_SCORE", "0.55")))),
        rag_engine_mode=(os.getenv("CHATBOT_RAG_ENGINE_MODE", "agentic") or "agentic").lower(),
        rag_engine_fallback=(os.getenv("CHATBOT_RAG_ENGINE_FALLBACK", "hybrid") or "hybrid").lower(),
        max_rewrite_query_length=max(20, int(os.getenv("MAX_REWRITE_QUERY_LENGTH", "256"))),
        checkpoint_backend=(os.getenv("CHATBOT_CHECKPOINT_BACKEND", "none") or "none").lower(),
        checkpoint_redis_url=os.getenv("CHATBOT_CHECKPOINT_REDIS_URL") or None,
        checkpoint_namespace=(os.getenv("CHATBOT_CHECKPOINT_NAMESPACE", "chatbot_graph") or "chatbot_graph"),
        similar_case_enabled=os.getenv("CHATBOT_SIMILAR_CASE_ENABLED", "false").lower() == "true",
        similar_case_namespace=(os.getenv("CHATBOT_SIMILAR_CASE_NAMESPACE", "事故案例") or "事故案例"),
        similar_case_top_k=max(1, int(os.getenv("CHATBOT_SIMILAR_CASE_TOP_K", "5"))),
        fault_detect_enabled=os.getenv("CHATBOT_FAULT_DETECT_ENABLED", "true").lower() == "true",
        fault_vision_enabled=os.getenv("CHATBOT_FAULT_VISION_ENABLED", "true").lower() == "true",
        fault_detect_mode=(os.getenv("CHATBOT_FAULT_DETECT_MODE", "hybrid") or "hybrid").lower(),
        fault_min_confidence=max(0.0, min(1.0, float(os.getenv("CHATBOT_FAULT_MIN_CONFIDENCE", "0.5")))),
        nl2sql_route_enabled=os.getenv("CHATBOT_NL2SQL_ROUTE_ENABLED", "true").lower() == "true",
        default_prompt_version=(os.getenv("CHATBOT_PROMPT_DEFAULT_VERSION", "boiler_v1") or "boiler_v1").strip(),
        suggested_questions_enabled=os.getenv("CHATBOT_SUGGESTED_QUESTIONS_ENABLED", "true").lower() == "true",
        suggested_questions_max=max(1, min(10, int(os.getenv("CHATBOT_SUGGESTED_QUESTIONS_MAX", "5")))),
        image_preprocess_enabled=os.getenv("CHATBOT_IMAGE_PREPROCESS_ENABLED", "true").lower() == "true",
        image_max_edge=max(256, int(os.getenv("CHATBOT_IMAGE_MAX_EDGE", "1280"))),
        image_compress_threshold_mb=max(0.1, float(os.getenv("CHATBOT_IMAGE_COMPRESS_THRESHOLD_MB", "2"))),
        image_jpeg_quality=max(50, min(95, int(os.getenv("CHATBOT_IMAGE_JPEG_QUALITY", "80")))),
        image_storage_backend=(os.getenv("CHATBOT_IMAGE_STORAGE_BACKEND", "minio") or "minio").strip().lower(),
        image_store_dir=(os.getenv("CHATBOT_IMAGE_STORE_DIR", "runtime/chatbot_images") or "runtime/chatbot_images").strip(),
        image_public_path=(os.getenv("CHATBOT_IMAGE_PUBLIC_PATH", "/chatbot/media") or "/chatbot/media").strip(),
        image_minio_endpoint=(os.getenv("CHATBOT_IMAGE_MINIO_ENDPOINT", "models-app-minio:9000") or "models-app-minio:9000").strip(),
        image_minio_access_key=(os.getenv("CHATBOT_IMAGE_MINIO_ACCESS_KEY", "minioadmin") or "minioadmin").strip(),
        image_minio_secret_key=(os.getenv("CHATBOT_IMAGE_MINIO_SECRET_KEY", "minioadmin") or "minioadmin").strip(),
        image_minio_bucket=(os.getenv("CHATBOT_IMAGE_MINIO_BUCKET", "chatbot-images") or "chatbot-images").strip(),
        image_minio_secure=os.getenv("CHATBOT_IMAGE_MINIO_SECURE", "false").lower() == "true",
        image_minio_auto_create_bucket=os.getenv("CHATBOT_IMAGE_MINIO_AUTO_CREATE_BUCKET", "true").lower() == "true",
        image_minio_presign_ttl_seconds=max(60, int(os.getenv("CHATBOT_IMAGE_MINIO_PRESIGN_TTL_SECONDS", "900"))),
    )
    analysis_cfg = AnalysisConfig(
        default_report_template=(os.getenv("ANALYSIS_DEFAULT_REPORT_TEMPLATE", "standard") or "standard").strip(),
        default_chart_mode=(os.getenv("ANALYSIS_DEFAULT_CHART_MODE", "auto") or "auto").strip().lower(),
        default_report_style=(os.getenv("ANALYSIS_DEFAULT_REPORT_STYLE", "standard") or "standard").strip(),
        default_max_nl2sql_calls=max(1, int(os.getenv("ANALYSIS_DEFAULT_MAX_NL2SQL_CALLS", "6"))),
        default_max_rows_per_query=max(50, int(os.getenv("ANALYSIS_DEFAULT_MAX_ROWS_PER_QUERY", "2000"))),
        default_max_suggestions=max(1, min(20, int(os.getenv("ANALYSIS_DEFAULT_MAX_SUGGESTIONS", "8")))),
        synthesis_timeout_seconds=max(5.0, float(os.getenv("ANALYSIS_SYNTHESIS_TIMEOUT_SECONDS", "90"))),
        strict_by_default=os.getenv("ANALYSIS_STRICT_BY_DEFAULT", "false").lower() == "true",
        trace_backend=(os.getenv("ANALYSIS_TRACE_BACKEND", "redis") or "redis").strip().lower(),
        trace_ttl_minutes=max(10, int(os.getenv("ANALYSIS_TRACE_TTL_MINUTES", "1440"))),
        trace_max_items=max(100, int(os.getenv("ANALYSIS_TRACE_MAX_ITEMS", "10000"))),
        trace_trend_cache_ttl_seconds=max(1, int(os.getenv("ANALYSIS_TRACE_TREND_CACHE_TTL_SECONDS", "30"))),
        trace_lazy_cleanup_batch_size=max(20, int(os.getenv("ANALYSIS_TRACE_LAZY_CLEANUP_BATCH_SIZE", "200"))),
        trace_es_hosts=(os.getenv("ANALYSIS_TRACE_ES_HOSTS") or os.getenv("RAG_ES_HOSTS") or "http://localhost:9200").strip(),
        trace_es_index=(os.getenv("ANALYSIS_TRACE_ES_INDEX", "analysis_trace_archive") or "analysis_trace_archive").strip(),
        trace_es_verify_certs=os.getenv("ANALYSIS_TRACE_ES_VERIFY_CERTS", "false").lower() == "true",
        trace_es_timeout_seconds=max(1, int(os.getenv("ANALYSIS_TRACE_ES_TIMEOUT_SECONDS", "10"))),
        trace_es_username=(os.getenv("ANALYSIS_TRACE_ES_USERNAME") or os.getenv("RAG_ES_USERNAME") or "").strip(),
        trace_es_password=(os.getenv("ANALYSIS_TRACE_ES_PASSWORD") or os.getenv("RAG_ES_PASSWORD") or "").strip(),
        trace_es_api_key=(os.getenv("ANALYSIS_TRACE_ES_API_KEY") or os.getenv("RAG_ES_API_KEY") or "").strip(),
        payload_time_window_coverage_min=max(
            0.0, min(1.0, float(os.getenv("ANALYSIS_PAYLOAD_TIME_WINDOW_COVERAGE_MIN", "0.6")))
        ),
        payload_anomaly_rate_max=max(0.0, min(1.0, float(os.getenv("ANALYSIS_PAYLOAD_ANOMALY_RATE_MAX", "0.2")))),
        payload_missing_key_rate_max=max(
            0.0, min(1.0, float(os.getenv("ANALYSIS_PAYLOAD_MISSING_KEY_RATE_MAX", "0.3")))
        ),
        nl2sql_time_window_coverage_min=max(
            0.0, min(1.0, float(os.getenv("ANALYSIS_NL2SQL_TIME_WINDOW_COVERAGE_MIN", "0.5")))
        ),
        nl2sql_anomaly_rate_max=max(0.0, min(1.0, float(os.getenv("ANALYSIS_NL2SQL_ANOMALY_RATE_MAX", "0.25")))),
        nl2sql_missing_key_rate_max=max(
            0.0, min(1.0, float(os.getenv("ANALYSIS_NL2SQL_MISSING_KEY_RATE_MAX", "0.35")))
        ),
        checkpoint_backend=(os.getenv("ANALYSIS_CHECKPOINT_BACKEND", "none") or "none").lower(),
        checkpoint_redis_url=os.getenv("ANALYSIS_CHECKPOINT_REDIS_URL") or None,
        checkpoint_namespace=(os.getenv("ANALYSIS_CHECKPOINT_NAMESPACE", "analysis_graph") or "analysis_graph"),
        nl2sql_llm_planner_enabled=os.getenv("ANALYSIS_NL2SQL_LLM_PLANNER_ENABLED", "true").lower() == "true",
    )
    _v2_fills_env = os.getenv("INSPECT_EXTRACT_V2_SHADING_CANDIDATE_FILLS", "").strip()
    if _v2_fills_env:
        _v2_fills_list = [
            _normalize_inspection_shading_fill_hex(x) for x in _v2_fills_env.split(",") if x.strip()
        ]
    else:
        _v2_fills_list = _default_inspection_v2_shading_fills()

    _inspect_log_llm_raw = os.getenv("INSPECT_EXTRACT_LOG_LLM_RAW_RESPONSE", "false").lower() == "true"
    _inspect_chunk_full_ev = (os.getenv("INSPECT_EXTRACT_LOG_PARSE_CHUNK_FULL") or "").strip().lower()
    if _inspect_chunk_full_ev in ("true", "1", "yes"):
        _inspect_log_parse_chunk_full = True
    elif _inspect_chunk_full_ev in ("false", "0", "no"):
        _inspect_log_parse_chunk_full = False
    else:
        # 未设置环境变量时与 raw LLM 日志一致，避免排障时漏打完整分块
        _inspect_log_parse_chunk_full = _inspect_log_llm_raw

    inspection_extract_cfg = InspectionExtractConfig(
        enabled=os.getenv("INSPECT_EXTRACT_ENABLED", "true").lower() == "true",
        strict_default=os.getenv("INSPECT_EXTRACT_STRICT_DEFAULT", "false").lower() == "true",
        max_repair_retries=max(0, int(os.getenv("INSPECT_EXTRACT_MAX_REPAIR_RETRIES", "1"))),
        prompt_version=(os.getenv("INSPECT_EXTRACT_PROMPT_VERSION", "v1") or "v1").strip(),
        model_name=(os.getenv("INSPECT_EXTRACT_MODEL_NAME") or "").strip() or None,
        llm_timeout_seconds=max(10.0, float(os.getenv("INSPECT_EXTRACT_LLM_TIMEOUT_SECONDS", "180"))),
        llm_max_tokens_parse=max(128, int(os.getenv("INSPECT_EXTRACT_LLM_MAX_TOKENS_PARSE", "1024"))),
        llm_max_tokens_classify=max(128, int(os.getenv("INSPECT_EXTRACT_LLM_MAX_TOKENS_CLASSIFY", "1024"))),
        llm_max_tokens_repair=max(128, int(os.getenv("INSPECT_EXTRACT_LLM_MAX_TOKENS_REPAIR", "768"))),
        log_llm_raw_response=_inspect_log_llm_raw,
        log_llm_raw_max_chars=max(200, int(os.getenv("INSPECT_EXTRACT_LOG_LLM_RAW_MAX_CHARS", "2000"))),
        log_parse_chunk_full=_inspect_log_parse_chunk_full,
        log_parse_chunk_max_chars=max(0, int(os.getenv("INSPECT_EXTRACT_LOG_PARSE_CHUNK_MAX_CHARS", "0"))),
        pipeline_version=(os.getenv("INSPECT_EXTRACT_PIPELINE_VERSION", "v1") or "v1").strip().lower(),
        v2_shading_candidate_fills=_v2_fills_list,
        v2_parse_unit_max_chars=max(2000, int(os.getenv("INSPECT_EXTRACT_V2_PARSE_UNIT_MAX_CHARS", "6000"))),
        v2_classify_batch_size=max(8, min(200, int(os.getenv("INSPECT_EXTRACT_V2_CLASSIFY_BATCH_SIZE", "40")))),
    )

    cfg = AppConfig(
        env=env,
        llm=llm_cfg,
        logging=logging_cfg,
        rag=rag_cfg,
        mineru=mineru_cfg,
        chatbot=chatbot_cfg,
        analysis=analysis_cfg,
        inspection_extract=inspection_extract_cfg,
    )
    # 动态附加 db 字段，避免破坏现有 AppConfig 初始化调用点
    setattr(cfg, "db", db_cfg)
    return cfg


@lru_cache(maxsize=1)
def get_app_config() -> AppConfig:
    """
    获取全局 AppConfig（单例缓存）。
    """
    return _load_from_env()

