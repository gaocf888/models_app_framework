from __future__ import annotations

import os
from dataclasses import dataclass, field
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
    # 抽样页平均可提取字符数低于该阈值则视为「图片/扫描 PDF」，走 MinerU
    pdf_scanned_max_avg_chars: float = 40.0
    # 多 worker 时 Redis 信号量键前缀（与 REDIS_URL 联用）
    redis_semaphore_key_prefix: str = "mineru:ingest"
    # API 路径（一般无需改）
    file_parse_path: str = "/file_parse"
    # 与 mineru-api 的 MINERU_API_OUTPUT_ROOT 最后一级目录名一致（共享 IO 卷上的相对路径）
    disk_fallback_subdir: str = "mineru-output"


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
    db_name = os.getenv("DB_NAME", "aishare")
    db_url = os.getenv("DB_URL", f"mysql+aiomysql://{db_user}:{db_password}@{db_host}/{db_name}")

    db_cfg = DatabaseConfig(
        url=db_url,
        user=db_user,
        password=db_password,
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
        pdf_scanned_max_avg_chars=float(os.getenv("MINERU_PDF_SCANNED_MAX_AVG_CHARS", "40")),
        redis_semaphore_key_prefix=os.getenv("MINERU_REDIS_SEM_KEY_PREFIX", "mineru:ingest"),
        file_parse_path=os.getenv("MINERU_FILE_PARSE_PATH", "/file_parse"),
        disk_fallback_subdir=os.getenv("MINERU_DISK_FALLBACK_SUBDIR", "mineru-output"),
    )

    cfg = AppConfig(env=env, llm=llm_cfg, logging=logging_cfg, rag=rag_cfg, mineru=mineru_cfg)
    # 动态附加 db 字段，避免破坏现有 AppConfig 初始化调用点
    setattr(cfg, "db", db_cfg)
    return cfg


@lru_cache(maxsize=1)
def get_app_config() -> AppConfig:
    """
    获取全局 AppConfig（单例缓存）。
    """
    return _load_from_env()

