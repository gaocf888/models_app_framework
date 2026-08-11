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
    # RAG 摄入成功后是否联动写图（与 enabled 独立，默认关）
    ingest_on_rag: bool = False
    # RAG 删文档时是否同步清理图侧数据（与 ingest_on_rag 独立，默认关）
    delete_on_rag: bool = False

    # Neo4j 连接信息（如未配置，则禁用 GraphRAG）
    uri: str | None = None
    username: str | None = None
    password: str | None = None
    database: str | None = None

    # 可选：配置文件路径（如 graph_schema.yaml），便于通过外部 YAML 定义 schema
    schema_config_path: str | None = None
    schema_hot_reload: bool = False

    # 领域本体 / Schema（可选）
    schema: GraphSchemaConfig = field(default_factory=GraphSchemaConfig)

    # 混合检索策略
    strategy: GraphHybridStrategyConfig = field(default_factory=GraphHybridStrategyConfig)

    # 实体关系抽取：llm（默认）| rule（调试）
    extraction_mode: str = "llm"
    extraction_fallback_rule: bool = False
    llm_endpoint: str | None = None
    llm_model: str | None = None
    llm_timeout_s: float = 120.0
    llm_max_tokens: int = 2048
    llm_batch_size: int = 4
    llm_max_retries: int = 2

    # 实体抽取与图事实输出策略（工程化可调参数；规则抽取回退时使用）
    entity_min_len: int = 2
    entity_max_len: int = 24
    zh_entity_max_len: int = 8
    en_entity_min_len: int = 2
    en_entity_max_len: int = 20
    max_entities_per_chunk: int = 40
    min_cooccur_weight: int = 1
    fact_template_entity: str = "[Graph] 实体 {entity} 相关片段: {text}"
    fact_template_cooccur: str = "[Graph] 实体共现: {a} -> {b} (weight={weight})"
    fact_template_relation: str = "[Graph] 关系 {rel_type}: {source} -> {target}"


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
    jobs_index_version: int = 2


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
    clean_strip_html: bool = True
    clean_min_repeated_line_pages: int = 2
    tenant_id_default: str | None = None
    # RUNNING 任务超过该秒数未更新，判定为卡死并自动转 FAILED。
    running_stuck_timeout_seconds: int = 1800
    # RAG 知识库图块（figure）：VLM 描述 + MinIO 存储 + 图—文关联召回
    figure_enabled: bool = False
    figure_minio_bucket: str = "rag-assets"
    figure_caption_prompt_version: str = "rag_figure_caption_v1"
    figure_caption_max_tokens: int = 2048
    figure_caption_temperature: float = 0.2
    figure_presign_ttl_seconds: int = 86400
    figure_object_key_prefix: str = "rag-assets/"
    figure_neighbor_text_max_chars: int = 400
    figure_neighbor_text_before_ratio: float = 0.7
    figure_expand_max_per_text: int = 2
    figure_expand_max_total: int = 6


@dataclass
class RAGQueryVisionConfig:
    """查询侧多模态增强（阶段 4，与 RAG_FIGURE_ENABLED 独立）。"""

    enabled: bool = False
    mode: str = "vision_augmented"  # vision_augmented | hybrid


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
    query_vision: RAGQueryVisionConfig = field(default_factory=RAGQueryVisionConfig)
    # namespace 知识库启用/优先级（召回加权）
    namespace_kb_priority_boost: float = 0.05
    namespace_kb_priority_tiered: bool = False


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
    # 等待 MinerU 并发槽位的最长时间（秒）；与 HTTP 解析超时解耦，避免在 sem_pool 空时静默挂数小时
    gate_wait_timeout_s: float = 600.0
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
    # 意图分类后端：rules（默认）| llm（规则+进程内轻量 LLM 窄触发）| bert（微调 BERT，需训练）
    intent_backend: str = "rules"
    intent_output_labels: list[str] = field(
        default_factory=lambda: ["kb_qa", "clarify", "data_query", "hybrid_qa"]
    )
    # 模式 B：进程内轻量意图 LLM（CHATBOT_INTENT_BACKEND=llm），与嵌入模型相同的离线优先策略
    intent_llm_model_path: str | None = None
    intent_llm_model_name: str = "Qwen/Qwen2.5-0.5B-Instruct"
    intent_llm_device: str = "cpu"
    intent_llm_max_tokens: int = 128
    intent_llm_temperature: float = 0.0
    intent_llm_conf_threshold: float = 0.78
    intent_llm_fallback_to_rules: bool = True
    # BERT 意图模型：优先本地路径，其次 HuggingFace 模型名；未配置且 backend=bert 时回退 rules
    intent_bert_model_path: str | None = None
    intent_bert_model_name: str | None = None
    intent_bert_device: str = "cpu"
    intent_bert_max_length: int = 256
    intent_bert_fallback_to_rules: bool = True
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
    # 人机协同（意图边界确认、意图消歧、NL2SQL 生成失败降级）
    hitl_enabled: bool = False
    intent_hitl_enabled: bool = True
    intent_hitl_min_confidence: float = 0.75
    intent_disambiguation_enabled: bool = True
    intent_disambiguation_timeout_sec: float = 15.0
    intent_hitl_max_rounds: int = 2
    nl2sql_hitl_enabled: bool = True
    nl2sql_hitl_max_retries: int = 1
    hitl_resume_ttl_seconds: int = 1800
    hitl_session_backend: str = "memory"
    hitl_session_redis_url: str | None = None
    hitl_session_namespace: str = "chatbot_graph"
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
    # 查数成功后：主模型基于 SQL 结果做 Markdown 收紧分析（关则仅出 Markdown 表，兼容旧行为）
    nl2sql_llm_analysis_enabled: bool = True
    # 查数空结果：是否用轻量 LLM 友好引导改问（关则固定文案；执行失败始终固定文案，不调 LLM）
    nl2sql_empty_llm_guide_enabled: bool = True
    # 分析/空结果引导注入模型的最大行数（与展示截断一致）
    nl2sql_analysis_max_rows: int = 80
    # 收紧分析输出长度；过大易拖慢生成并触发 httpx ReadTimeout
    nl2sql_analysis_max_tokens: int = 1024
    # 分析/空结果引导 LLM 读超时（秒）；默认客户端仅 30s，宽表明细易超时
    nl2sql_analysis_timeout_sec: float = 120.0
    # None 表示沿用主答 temperature / 模型默认
    nl2sql_analysis_temperature: float | None = 0.2
    # finished.meta.nl2sql_analysis：旁路结构化（列/行样本），便于前端图表；正文仍为 Markdown
    nl2sql_analysis_meta_enabled: bool = True
    # 主问答流式 LLM 的 sampling temperature（环境变量 CHATBOT_MAIN_LLM_TEMPERATURE）。
    # None 表示不在请求中覆盖，沿用 LLMModelConfig.temperature；仅作用于主答 stream_chat，不影响指代/相似案例等硬编码子调用。
    main_llm_temperature: float | None = None
    # 未传 prompt_version 时使用的客服模板版本（与 configs/prompts_bak_new.yaml 中 chatbot.version 对齐）
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
    # 上下文结构化索引增强（旁路能力，不改变主链路语义）
    outline_enabled: bool = False
    outline_async_enabled: bool = True
    reference_resolve_enabled: bool = True
    reference_lookback_turns: int = 8
    outline_es_enabled: bool = True
    outline_es_index: str = "conversation_outline_v1"
    # --- 指代消解 P0～P3（见 docs/智能客服上下文理指代实现优化方案-20260514.md §4）---
    anaphora_config_path: str | None = None
    anaphora_retrieval_fusion_enabled: bool = True
    anaphora_fusion_max_chars: int = 2800
    anaphora_anchor_block_enabled: bool = False
    anaphora_anchor_max_chars: int = 1200
    anaphora_slots_enabled: bool = False
    anaphora_slots_max_bullets: int = 8
    anaphora_llm_gate_enabled: bool = False
    anaphora_llm_timeout_sec: float = 4.0
    anaphora_llm_model: str | None = None
    anaphora_expose_meta: bool = False
    # 本厂/该厂等问句锁定电厂专属知识库 namespace（RAG 链路 rag_scope_resolve）
    plant_kb_enabled: bool = True
    plant_kb_namespace: str = "Power_plant_knowledge"
    plant_kb_query_boost_name: str = "华电五彩湾北一发电有限公司"
    plant_kb_fallback_on_empty: bool = False
    # 厂别指代是否延续到近几轮 user 历史；默认 false=仅本轮 query 含本厂/本公司等才锁 namespace
    plant_kb_history_continuation: bool = False
    # 高分 FAQ 软直通：首条 citation 高分且 anaphora=none 时，生成阶段不注入 history_messages（默认开）
    faq_soft_direct_enabled: bool = True
    # 软直通闸门：首条 citation 的 rerank_score（CrossEncoder），非 Agentic 融合后的 score
    faq_soft_direct_min_score: float = 0.95
    # 软直通时注入 LLM 的片段条数上限（rag_citations 展示条数不变）
    faq_soft_direct_snippet_top_n: int = 1
    # 主问答 LLM 上下文与历史裁剪（与 vLLM --max-model-len 对齐）
    llm_context_total_tokens: int = 40960
    llm_completion_budget_slack_tokens: int = 768
    history_trim_enabled: bool = True
    history_trim_min_keep: int = 0


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
    default_max_nl2sql_calls: int = 15
    default_max_rows_per_query: int = 2000
    default_max_suggestions: int = 8
    synthesis_timeout_seconds: float = 90.0
    # synthesis user 消息中 gathered_data JSON 最大字符数（整包 json.dumps 后截断）
    synthesis_gathered_json_max_chars: int = 16000
    # synthesis LLM 输出 max_tokens（六章+附录多表时 3072 易触顶截断；见 ANALYSIS_SYNTHESIS_MAX_TOKENS）
    synthesis_max_tokens: int = 8192
    # synthesis 策略：v1=单次整篇 LLM；v2=多槽位（见 docs/综合分析优化版本实现方案(v2版本).md）
    synthesis_strategy: str = "v1"
    synthesis_strategy_overheat_guidance: str | None = None
    synthesis_strategy_maintenance_strategy: str | None = None
    synthesis_strategy_four_tube_health_interpretation: str | None = None
    synthesis_strategy_leakage_burst_analysis: str | None = None
    synthesis_strategy_custom: str | None = None
    # plan/synthesis 模板版本：全局默认 + 按 analysis_type 覆盖（见 ANALYSIS_*_TEMPLATE_VERSION_*）
    plan_template_version: str | None = None
    plan_template_version_overheat_guidance: str | None = None
    plan_template_version_maintenance_strategy: str | None = None
    plan_template_version_four_tube_health_interpretation: str | None = None
    plan_template_version_leakage_burst_analysis: str | None = None
    plan_template_version_img_diag_defect_ident: str | None = "v1"
    plan_template_version_img_diag_leakage_burst: str | None = "v1"
    plan_template_version_custom: str | None = None
    synthesis_template_version: str | None = None
    synthesis_template_version_overheat_guidance: str | None = None
    synthesis_template_version_maintenance_strategy: str | None = None
    synthesis_template_version_four_tube_health_interpretation: str | None = None
    synthesis_template_version_leakage_burst_analysis: str | None = None
    synthesis_template_version_img_diag_defect_ident: str | None = "v1"
    synthesis_template_version_img_diag_leakage_burst: str | None = "v1"
    synthesis_template_version_custom: str | None = None
    synthesis_v2_max_parallel_llm: int = 3
    synthesis_v2_segment_max_tokens: int = 4096
    synthesis_v2_table_max_rows: int = 80
    synthesis_v2_enable_structured_sse_events: bool = True
    synthesis_v2_stream_live_first: bool = False
    synthesis_v2_stream_chunk_chars: int = 16
    synthesis_v2_stream_chunk_delay_ms: float = 18.0
    synthesis_v2_idle_heartbeat_seconds: float = 5.0
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
    # acquire_data：无依赖任务并行执行（生成 SQL + 查库仍发生在各 NL2SQLService.query 内）
    nl2sql_acquire_parallel_enabled: bool = True
    nl2sql_acquire_max_parallel: int = 8
    # NL2SQL：可执行 SQL 快照缓存（L2，进程内 LRU+TTL）；关闭则与未实现缓存前行为一致
    nl2sql_cache_enabled: bool = False
    nl2sql_cache_ttl_seconds: int = 3600
    nl2sql_cache_max_entries: int = 512
    # NL2SQL：L1 时间骨架缓存（意图键 + SQL 模板占位符）；依赖 NL2SQL_CACHE_ENABLED=true 且本开关为 true 时启用
    nl2sql_l1_cache_enabled: bool = True
    # NL2SQL：校验通过后自动写入 nl2sql_qa_examples（带 schema/数据源指纹元数据；检索侧默认按指纹过滤）
    nl2sql_qa_feedback_enabled: bool = False
    # 规划前 RAG 检索 query 构造：
    # legacy — 与旧版一致：`{analysis_type} {用户 query}`（英文枚举参与向量/关键词/重排）；
    # user_only — 仅用用户 query 做主检索；
    # cn_label_prefix — `{中文场景标签} {用户 query}` 单次检索（标签见 analysis_graph_runner 映射）；
    # two_stage — 召回仅用用户 query；Hybrid 重排时使用「中文标签 + query」（无映射则退回枚举 + query）。
    plan_rag_query_mode: str = "two_stage"
    # 看图诊断（img_diag）：视觉臂复用 LLM_DEFAULT_MODEL（须为多模态 VL）；各臂超时与上传大小上限
    img_diag_vision_timeout_seconds: float = 120.0
    img_diag_vision_temperature: float = 0.45
    # 视觉臂 system 前缀：复用 chatbot 模板版本（默认跟随 CHATBOT_PROMPT_DEFAULT_VERSION）
    img_diag_vision_chatbot_prompt_version: str | None = None
    # 视觉臂 user 固定短句（不使用缺陷识别/泄爆分析业务 query；位置/处置语义留给 NL2SQL 与 synthesis）
    img_diag_vision_user_query_defect_ident: str = "请帮我分析图片缺陷"
    img_diag_vision_user_query_leakage_burst: str = "请分析图片中的爆口/泄漏可见形貌特征。"
    img_diag_lane_timeout_seconds: float = 180.0
    img_diag_upload_max_mb: int = 15
    # 看图诊断业务 RAG：vision_augmented（默认，视觉完成后串行 RAG）| parallel | hybrid
    img_diag_rag_mode: str = "vision_augmented"
    # 看图诊断 scope HITL（LangGraph + 人机协同）
    img_diag_use_langgraph: bool = True
    img_diag_scope_hitl_enabled: bool = True
    img_diag_scope_hitl_max_rounds: int = 5
    # 首次 LLM+库表均成功时是否强制一次「匹配成功请确认」人机协同（默认开启；曾触发 HITL 后不再重复）
    img_diag_scope_matched_confirm_enabled: bool = True
    # scope 库表校验失败时是否自动放宽 tube_no/row_no/check_location_name（默认关闭，改由人机协同修正）
    img_diag_scope_auto_relax_enabled: bool = False
    img_diag_scope_low_confidence_hitl: bool = True
    img_diag_scope_validate_sql: str | None = None
    img_diag_scope_validate_timeout_s: float = 10.0
    img_diag_scope_validate_skip_on_error: bool = False
    # 库未匹配 ≥N 轮校正后：失败层诊断 + 候选 + LLM TopK + 选择 HITL（策略 A，默认开启）
    img_diag_scope_candidate_pick_enabled: bool = True
    img_diag_scope_candidate_pick_after_mismatch_rounds: int = 2
    img_diag_scope_candidate_limit: int = 50
    img_diag_scope_candidate_top_k: int = 5
    img_diag_scope_candidate_rank_prompt_version: str = "v1"
    img_diag_scope_candidate_rank_timeout_s: float = 20.0
    # 诊断/候选 SQL（可选覆盖；默认与 overhaul_new_checklocation / base_temp_point 口径一致）
    img_diag_scope_diagnose_boiler_sql: str | None = None
    img_diag_scope_candidate_sql_boiler: str | None = None
    img_diag_scope_candidate_sql_device: str | None = None
    img_diag_scope_candidate_sql_location: str | None = None
    img_diag_scope_candidate_sql_row: str | None = None
    img_diag_scope_candidate_sql_tube: str | None = None
    # scope HITL 持久化：默认 redis（须 REDIS_URL 或 ANALYSIS_IMG_DIAG_*_REDIS_URL）；无 Redis 时改 memory
    img_diag_checkpoint_backend: str = "redis"
    img_diag_checkpoint_redis_url: str | None = None
    img_diag_checkpoint_namespace: str = "img_diag"
    img_diag_session_store_backend: str = "redis"
    img_diag_session_store_redis_url: str | None = None
    img_diag_session_ttl_seconds: int = 172800
    # scope resume SSE：上游长时间无事件时发送 comment ping 保活（秒）；0 表示关闭
    img_diag_resume_sse_idle_ping_seconds: float = 12.0


@dataclass
class NL2SQLIntentConfig:
    """
    NL2SQL 问句意图（时间 + 实体范围）解析、SQL 改写与可观测性。

    对应环境变量见 ``app/app-deploy/.env.example`` 中 NL2SQL_SCOPE_* / NL2SQL_INTENT_* 段。
    """

    scope_sql_rewrite_enabled: bool = True
    scope_lexicon_file: str | None = None
    intent_parse_mode: str = "rule"  # rule | llm | rule_with_llm_fallback
    scope_parse_llm_timeout_ms: int = 8000
    scope_parse_prompt_version: str = "v1"
    scope_parse_llm_max_tokens: int = 512
    scope_parse_llm_temperature: float = 0.0
    scope_parse_log_rule_llm_diff: bool = False
    inject_parsed_intent: bool = False
    response_include_parsed_intent: bool = False
    trace_include_question_intent: bool = True
    # plan 含「锚点向前 N 天」但用户未解析事故锚点时，看图诊断场景以 NOW() 为锚点上界合成回溯窗
    anchor_fallback_now_enabled: bool = True
    anchor_fallback_analysis_types: str = "img_diag_leakage_burst,img_diag_defect_ident"
    # SQL 执行前拒绝仍含 @t_start/@t_end/@t_after 的语句（通常表示时间窗未解析且未改写）
    reject_unresolved_time_placeholders: bool = True


@dataclass
class AnalysisAgentConfig:
    """综合分析智能体（analysis_agent）独立配置。"""

    enabled: bool = True
    use_langgraph: bool = True
    use_react_agent: bool = True
    checkpoint_backend: str = "memory"  # none | memory | redis（生产见 APP_ENV=production 默认 redis）
    checkpoint_redis_url: str | None = None
    checkpoint_namespace: str = "analysis_agent"
    session_store_backend: str = "memory"  # memory | redis（多 worker / HITL resume 须 redis）
    session_store_redis_url: str | None = None
    session_ttl_seconds: int = 3600
    slot_nl2sql_max_retries: int = 2
    slot_synth_max_retries: int = 1
    react_max_iterations: int = 8
    stream_chunk_chars: int = 256
    enable_human_in_the_loop: bool = True
    rag_top_k: int = 8
    gathered_json_max_chars: int = 12000
    narrative_max_tokens: int = 4096
    nl2sql_disable_qa_slot_replay: bool = True
    enable_structured_sse_events: bool = True
    trace_backend: str = "memory"
    trace_ttl_minutes: int = 1440
    trace_max_items: int = 5000
    plan_template_version: str = "analysis_agent_v1"


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
    llm_timeout_seconds: float = 300.0
    # 检修 Parse / Classify / Repair 调用 chat 时显式传入，覆盖同模型在 LLMModelConfig 中的默认 temperature
    llm_temperature: float = 0.3
    # 与 vLLM --max-model-len 对齐；用于动态压低 max_tokens，避免 input+max_tokens 超出上下文
    llm_context_total_tokens: int = 32768
    # 在启发式 prompt token 估算之上追加的余量（特殊 token、模板等）
    llm_completion_budget_slack_tokens: int = 768
    llm_max_tokens_parse: int = 1024
    llm_max_tokens_classify: int = 1024
    llm_max_tokens_repair: int = 768
    # Parse 分块并发度（1=串行，>1=并发；建议结合 vLLM 承载能力小步调优）
    parse_concurrency: int = 1
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
    # V2：大表按「表头 + 数据行窗口」切分（单表超 max-chars 时启用）
    v2_table_row_window_enabled: bool = True
    v2_table_data_rows_per_window: int = 20
    v2_table_column_split_enabled: bool = False
    v2_classify_batch_size: int = 40
    # DOCX V2：parse 后按分块内 [颜色标注] 校正检测类型，避免同行/跨列误标缺陷
    v2_color_guard_enabled: bool = True
    # DOCX V2：parse 后按分块网格校正管号+壁厚绑定（方案 C）
    v2_bind_guard_enabled: bool = True
    # DOCX V2：parse 后按 chunk 组合编号（2-1）标记并校正行号/管号
    v2_combo_guard_enabled: bool = True
    # DOCX V2：parse 后按 chunk 列组 direction 校正管号正负（上数/下数等）
    v2_tube_direction_sign_guard_enabled: bool = True
    v2_tube_direction_sign_allow_fallback_4col: bool = False
    # DOCX V2：Parse 送 LLM 时仅保留 [DOCX_V2_TABLE] 表格块（guard/落盘仍用完整 chunk）
    v2_llm_parse_table_only: bool = True
    # DOCX V2：LLM 表格块裁掉从右起连续全空列并更新 cols=N（guard 仍用完整 chunk）
    v2_llm_strip_trailing_empty_cols: bool = True
    # 异步检修任务（断点续跑）根目录：每任务子目录含 request.json、chunks/*.json、job_meta.json
    async_jobs_state_dir: str = "./data/inspection_extract_jobs"
    # REDIS_URL 启用时：检修异步队列 worker 线程数（与摄入队列分离 key_prefix）
    async_queue_workers: int = 2


@dataclass
class InspectionExtractV0Config:
    """
    检修报告结构化提取 V0（LangGraph + 版面 OCR 侧车）。
    """

    enabled: bool = False
    strict_default: bool = False
    prompt_version: str = "v2"
    model_name: str | None = None
    llm_timeout_seconds: float = 300.0
    llm_temperature: float = 0.3
    llm_max_tokens_extract: int = 4096
    layout_ocr_endpoint: str = "http://127.0.0.1:8010"
    layout_ocr_timeout_seconds: float = 300.0
    layout_ocr_max_upload_mb: int = 32
    max_pdf_pages_preprocess: int = 5
    # doc/docx 是否调用版面侧车（侧车内 LibreOffice 转 PDF 后走 PaddleOCR）；false 则仅原生解析 + 文本 IRT
    docx_use_layout_ocr: bool = True
    # IRT 中含多张表时 LLM 按表分块并行（无表或单块时退化为一次调用）；1 表示顺序多表
    llm_table_chunk_concurrency: int = 4
    # 每个表块内最多携带多少条 OCR blocks（同页且与表 bbox 重叠）
    llm_table_chunk_max_blocks: int = 120
    # 是否启用 LangGraph Sqlite 检查点（每任务 job 目录下 langgraph_checkpoint.sqlite）；false 时用内存态 ainvoke，与顺序回退语义一致且无 Sqlite 文件依赖
    langgraph_use_sqlite_checkpoint: bool = False
    # LangGraph Sqlite checkpoint 置于 job 子目录时的文件名（仅 langgraph_use_sqlite_checkpoint=true 时创建）
    langgraph_checkpoint_filename: str = "langgraph_checkpoint.sqlite"
    async_queue_workers: int = 2


@dataclass
class FaceVectorConfig:
    """
    人脸识别向量检索后端配置。

    - backend=local：JSON 持久化 + 进程内 numpy/faiss（默认，零外部依赖）
    - backend=milvus：向量存 Milvus，元数据仍用 JSON + 图片目录
    """

    backend: str = "local"  # local | milvus
    milvus_uri: str = "http://127.0.0.1:19530"
    milvus_collection: str = "face_embeddings"
    embedding_dim: int = 512
    milvus_metric: str = "COSINE"  # COSINE | IP


@dataclass
class AppConfig:
    """
    应用全局配置。
    """

    env: str = "dev"
    llm: LLMConfig = field(default_factory=lambda: LLMConfig(default_model="default"))
    rag: RAGConfig = field(default_factory=RAGConfig)
    face_vector: FaceVectorConfig = field(default_factory=FaceVectorConfig)
    logging: LoggingConfig = field(default_factory=LoggingConfig)
    prompt: PromptConfig = field(default_factory=PromptConfig)
    mineru: MinerUConfig = field(default_factory=MinerUConfig)
    chatbot: ChatbotConfig = field(default_factory=ChatbotConfig)
    analysis: AnalysisConfig = field(default_factory=AnalysisConfig)
    nl2sql_intent: NL2SQLIntentConfig = field(default_factory=NL2SQLIntentConfig)
    analysis_agent: AnalysisAgentConfig = field(default_factory=AnalysisAgentConfig)
    inspection_extract: InspectionExtractConfig = field(default_factory=InspectionExtractConfig)
    inspection_extract_v0: InspectionExtractV0Config = field(default_factory=InspectionExtractV0Config)


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
        f"{quote(db_user, safe='')}:{quote(db_password, safe='')}@{db_host}:{db_port}/{db_name}"
        "?charset=utf8mb4",
    )
    if "charset=" not in db_url.lower():
        sep = "&" if "?" in db_url else "?"
        db_url = f"{db_url}{sep}charset=utf8mb4"

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

    def _optional_clamped_temperature(name: str) -> float | None:
        raw = os.getenv(name)
        if raw is None:
            return None
        s = str(raw).strip()
        if not s:
            return None
        return max(0.0, min(2.0, float(s)))

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
        jobs_index_version=int(os.getenv("RAG_ES_JOBS_INDEX_VERSION", "2")),
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
        ingest_on_rag=os.getenv("GRAPH_RAG_INGEST_ON_RAG", "false").lower() == "true",
        delete_on_rag=os.getenv("GRAPH_RAG_DELETE_ON_RAG", "false").lower() == "true",
        uri=os.getenv("NEO4J_URI"),
        username=os.getenv("NEO4J_USERNAME"),
        password=os.getenv("NEO4J_PASSWORD"),
        database=os.getenv("NEO4J_DATABASE") or None,
        schema_config_path=os.getenv("GRAPH_SCHEMA_CONFIG_PATH") or "configs/graph_schema.yaml",
        schema_hot_reload=os.getenv("GRAPH_SCHEMA_HOT_RELOAD", "false").lower() == "true",
        strategy=graph_strategy,
        extraction_mode=os.getenv("GRAPH_EXTRACTION_MODE", "llm").lower(),
        extraction_fallback_rule=os.getenv("GRAPH_EXTRACTION_FALLBACK_RULE", "false").lower() == "true",
        llm_endpoint=os.getenv("GRAPH_LLM_ENDPOINT") or None,
        llm_model=os.getenv("GRAPH_LLM_MODEL") or None,
        llm_timeout_s=float(os.getenv("GRAPH_LLM_TIMEOUT_S", "120")),
        llm_max_tokens=int(os.getenv("GRAPH_LLM_MAX_TOKENS", "2048")),
        llm_batch_size=max(1, int(os.getenv("GRAPH_LLM_BATCH_SIZE", "4"))),
        llm_max_retries=max(0, int(os.getenv("GRAPH_LLM_MAX_RETRIES", "2"))),
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
        fact_template_relation=os.getenv(
            "GRAPH_FACT_TEMPLATE_RELATION", "[Graph] 关系 {rel_type}: {source} -> {target}"
        ),
    )
    if graph_cfg.enabled:
        from app.graph.schema_loader import apply_schema_to_graph_config

        graph_cfg = apply_schema_to_graph_config(graph_cfg)

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
        clean_strip_html=os.getenv("RAG_CLEAN_STRIP_HTML", "true").lower() == "true",
        clean_min_repeated_line_pages=int(os.getenv("RAG_CLEAN_MIN_REPEATED_LINE_PAGES", "2")),
        tenant_id_default=os.getenv("RAG_TENANT_ID_DEFAULT") or None,
        running_stuck_timeout_seconds=max(60, int(os.getenv("RAG_RUNNING_STUCK_TIMEOUT_SECONDS", "1800"))),
        figure_enabled=os.getenv("RAG_FIGURE_ENABLED", "false").lower() == "true",
        figure_minio_bucket=(os.getenv("RAG_FIGURE_MINIO_BUCKET") or "rag-assets").strip(),
        figure_caption_prompt_version=(
            os.getenv("RAG_FIGURE_CAPTION_PROMPT_VERSION") or "rag_figure_caption_v1"
        ).strip(),
        figure_caption_max_tokens=max(256, int(os.getenv("RAG_FIGURE_CAPTION_MAX_TOKENS", "2048"))),
        figure_caption_temperature=max(
            0.0, min(1.0, float(os.getenv("RAG_FIGURE_CAPTION_TEMPERATURE", "0.2")))
        ),
        figure_presign_ttl_seconds=max(60, int(os.getenv("RAG_FIGURE_PRESIGN_TTL_SECONDS", "86400"))),
        figure_object_key_prefix=(os.getenv("RAG_FIGURE_OBJECT_KEY_PREFIX") or "rag-assets/").strip(),
        figure_neighbor_text_max_chars=max(64, int(os.getenv("RAG_FIGURE_NEIGHBOR_TEXT_MAX_CHARS", "400"))),
        figure_neighbor_text_before_ratio=max(
            0.0, min(1.0, float(os.getenv("RAG_FIGURE_NEIGHBOR_TEXT_BEFORE_RATIO", "0.7")))
        ),
        figure_expand_max_per_text=max(0, int(os.getenv("RAG_FIGURE_EXPAND_MAX_PER_TEXT", "2"))),
        figure_expand_max_total=max(0, int(os.getenv("RAG_FIGURE_EXPAND_MAX_TOTAL", "6"))),
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

    query_vision_cfg = RAGQueryVisionConfig(
        enabled=os.getenv("RAG_QUERY_VISION_AUGMENT_ENABLED", "false").lower() == "true",
        mode=(os.getenv("RAG_QUERY_VISION_AUGMENT_MODE") or "vision_augmented").strip().lower(),
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
        query_vision=query_vision_cfg,
        namespace_kb_priority_boost=float(os.getenv("RAG_NAMESPACE_PRIORITY_BOOST", "0.05")),
        namespace_kb_priority_tiered=os.getenv("RAG_NAMESPACE_PRIORITY_TIERED", "false").lower() == "true",
    )

    mineru_cfg = MinerUConfig(
        enabled=os.getenv("MINERU_ENABLED", "false").lower() == "true",
        base_url=os.getenv("MINERU_BASE_URL", "http://mineru-api:8000").rstrip("/"),
        timeout_s=float(os.getenv("MINERU_TIMEOUT_S", "1200")),
        gate_wait_timeout_s=float(os.getenv("MINERU_GATE_WAIT_TIMEOUT_S", "600")),
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
        intent_backend=(os.getenv("CHATBOT_INTENT_BACKEND", "rules") or "rules").strip().lower(),
        intent_output_labels=_split_csv_env(
            "CHATBOT_INTENT_OUTPUT_LABELS", "kb_qa,clarify,data_query,hybrid_qa"
        ),
        intent_llm_model_path=(os.getenv("CHATBOT_INTENT_LLM_MODEL_PATH") or "").strip() or None,
        intent_llm_model_name=(
            os.getenv("CHATBOT_INTENT_LLM_MODEL_NAME", "Qwen/Qwen2.5-0.5B-Instruct") or "Qwen/Qwen2.5-0.5B-Instruct"
        ).strip(),
        intent_llm_device=(os.getenv("CHATBOT_INTENT_LLM_DEVICE", "cpu") or "cpu").strip(),
        intent_llm_max_tokens=max(32, int(os.getenv("CHATBOT_INTENT_LLM_MAX_TOKENS", "128"))),
        intent_llm_temperature=max(0.0, min(2.0, float(os.getenv("CHATBOT_INTENT_LLM_TEMPERATURE", "0")))),
        intent_llm_conf_threshold=max(0.0, min(1.0, float(os.getenv("CHATBOT_INTENT_LLM_CONF_THRESHOLD", "0.78")))),
        intent_llm_fallback_to_rules=os.getenv("CHATBOT_INTENT_LLM_FALLBACK_TO_RULES", "true").lower() == "true",
        intent_bert_model_path=(os.getenv("CHATBOT_INTENT_BERT_MODEL_PATH") or "").strip() or None,
        intent_bert_model_name=(os.getenv("CHATBOT_INTENT_BERT_MODEL_NAME") or "").strip() or None,
        intent_bert_device=(os.getenv("CHATBOT_INTENT_BERT_DEVICE", "cpu") or "cpu").strip(),
        intent_bert_max_length=max(32, int(os.getenv("CHATBOT_INTENT_BERT_MAX_LENGTH", "256"))),
        intent_bert_fallback_to_rules=os.getenv("CHATBOT_INTENT_BERT_FALLBACK_TO_RULES", "true").lower() == "true",
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
        hitl_enabled=os.getenv("CHATBOT_HITL_ENABLED", "false").lower() == "true",
        intent_hitl_enabled=os.getenv("CHATBOT_INTENT_HITL_ENABLED", "true").lower() == "true",
        intent_hitl_min_confidence=max(
            0.0, min(1.0, float(os.getenv("CHATBOT_INTENT_HITL_MIN_CONF", "0.75")))
        ),
        intent_disambiguation_enabled=os.getenv("CHATBOT_INTENT_DISAMBIGUATION_ENABLED", "true").lower()
        == "true",
        intent_disambiguation_timeout_sec=max(
            3.0, float(os.getenv("CHATBOT_INTENT_DISAMBIGUATION_TIMEOUT_SEC", "15"))
        ),
        intent_hitl_max_rounds=max(1, int(os.getenv("CHATBOT_INTENT_HITL_MAX_ROUNDS", "2"))),
        nl2sql_hitl_enabled=os.getenv("CHATBOT_NL2SQL_HITL_ENABLED", "true").lower() == "true",
        nl2sql_hitl_max_retries=max(0, int(os.getenv("CHATBOT_NL2SQL_HITL_MAX_RETRIES", "1"))),
        hitl_resume_ttl_seconds=max(60, int(os.getenv("CHATBOT_HITL_RESUME_TTL_SEC", "1800"))),
        hitl_session_backend=(os.getenv("CHATBOT_HITL_SESSION_BACKEND", "memory") or "memory").lower(),
        hitl_session_redis_url=os.getenv("CHATBOT_HITL_SESSION_REDIS_URL") or None,
        hitl_session_namespace=(os.getenv("CHATBOT_HITL_SESSION_NAMESPACE", "chatbot_graph") or "chatbot_graph"),
        similar_case_enabled=os.getenv("CHATBOT_SIMILAR_CASE_ENABLED", "false").lower() == "true",
        similar_case_namespace=(os.getenv("CHATBOT_SIMILAR_CASE_NAMESPACE", "事故案例") or "事故案例"),
        similar_case_top_k=max(1, int(os.getenv("CHATBOT_SIMILAR_CASE_TOP_K", "5"))),
        fault_detect_enabled=os.getenv("CHATBOT_FAULT_DETECT_ENABLED", "true").lower() == "true",
        fault_vision_enabled=os.getenv("CHATBOT_FAULT_VISION_ENABLED", "true").lower() == "true",
        fault_detect_mode=(os.getenv("CHATBOT_FAULT_DETECT_MODE", "hybrid") or "hybrid").lower(),
        fault_min_confidence=max(0.0, min(1.0, float(os.getenv("CHATBOT_FAULT_MIN_CONFIDENCE", "0.5")))),
        nl2sql_route_enabled=os.getenv("CHATBOT_NL2SQL_ROUTE_ENABLED", "true").lower() == "true",
        nl2sql_llm_analysis_enabled=os.getenv("CHATBOT_NL2SQL_LLM_ANALYSIS_ENABLED", "true").lower() == "true",
        nl2sql_empty_llm_guide_enabled=os.getenv("CHATBOT_NL2SQL_EMPTY_LLM_GUIDE_ENABLED", "true").lower()
        == "true",
        nl2sql_analysis_max_rows=max(1, min(200, int(os.getenv("CHATBOT_NL2SQL_ANALYSIS_MAX_ROWS", "80")))),
        nl2sql_analysis_max_tokens=max(256, int(os.getenv("CHATBOT_NL2SQL_ANALYSIS_MAX_TOKENS", "1024"))),
        nl2sql_analysis_timeout_sec=max(
            15.0, float(os.getenv("CHATBOT_NL2SQL_ANALYSIS_TIMEOUT_SEC", "120"))
        ),
        nl2sql_analysis_temperature=(
            max(0.0, min(2.0, float(os.getenv("CHATBOT_NL2SQL_ANALYSIS_TEMPERATURE", "0.2"))))
            if str(os.getenv("CHATBOT_NL2SQL_ANALYSIS_TEMPERATURE", "0.2") or "").strip()
            else None
        ),
        nl2sql_analysis_meta_enabled=os.getenv("CHATBOT_NL2SQL_ANALYSIS_META_ENABLED", "true").lower() == "true",
        main_llm_temperature=_optional_clamped_temperature("CHATBOT_MAIN_LLM_TEMPERATURE"),
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
        outline_enabled=os.getenv("CHATBOT_OUTLINE_ENABLED", "false").lower() == "true",
        outline_async_enabled=os.getenv("CHATBOT_OUTLINE_ASYNC_ENABLED", "true").lower() == "true",
        reference_resolve_enabled=os.getenv("CHATBOT_REFERENCE_RESOLVE_ENABLED", "true").lower() == "true",
        reference_lookback_turns=max(1, min(50, int(os.getenv("CHATBOT_REFERENCE_LOOKBACK_TURNS", "8")))),
        outline_es_enabled=os.getenv("CHATBOT_OUTLINE_ES_ENABLED", "true").lower() == "true",
        outline_es_index=(os.getenv("CHATBOT_OUTLINE_ES_INDEX", "conversation_outline_v1") or "conversation_outline_v1").strip(),
        anaphora_config_path=(os.getenv("CHATBOT_ANAPHORA_CONFIG_PATH") or "").strip() or None,
        anaphora_retrieval_fusion_enabled=os.getenv("CHATBOT_ANAPHORA_RETRIEVAL_FUSION_ENABLED", "true").lower() == "true",
        anaphora_fusion_max_chars=max(800, int(os.getenv("CHATBOT_ANAPHORA_FUSION_MAX_CHARS", "2800"))),
        anaphora_anchor_block_enabled=os.getenv("CHATBOT_ANAPHORA_ANCHOR_BLOCK_ENABLED", "false").lower() == "true",
        anaphora_anchor_max_chars=max(400, int(os.getenv("CHATBOT_ANCHOR_BLOCK_MAX_CHARS", "1200"))),
        anaphora_slots_enabled=os.getenv("CHATBOT_ANAPHORA_SLOTS_ENABLED", "false").lower() == "true",
        anaphora_slots_max_bullets=max(2, min(20, int(os.getenv("CHATBOT_ANAPHORA_SLOTS_MAX_BULLETS", "8")))),
        anaphora_llm_gate_enabled=os.getenv("CHATBOT_ANAPHORA_LLM_GATE_ENABLED", "false").lower() == "true",
        anaphora_llm_timeout_sec=max(0.5, float(os.getenv("CHATBOT_ANAPHORA_LLM_TIMEOUT_SEC", "4"))),
        anaphora_llm_model=(os.getenv("CHATBOT_ANAPHORA_LLM_MODEL") or "").strip() or None,
        anaphora_expose_meta=os.getenv("CHATBOT_ANAPHORA_EXPOSE_META", "false").lower() == "true",
        plant_kb_enabled=os.getenv("CHATBOT_PLANT_KB_ENABLED", "true").lower() == "true",
        plant_kb_namespace=(os.getenv("CHATBOT_PLANT_KB_NAMESPACE", "Power_plant_knowledge") or "Power_plant_knowledge").strip(),
        plant_kb_query_boost_name=(
            os.getenv("CHATBOT_PLANT_KB_QUERY_BOOST_NAME", "华电五彩湾北一发电有限公司") or "华电五彩湾北一发电有限公司"
        ).strip(),
        plant_kb_fallback_on_empty=os.getenv("CHATBOT_PLANT_KB_FALLBACK_ON_EMPTY", "false").lower() == "true",
        plant_kb_history_continuation=os.getenv("CHATBOT_PLANT_KB_HISTORY_CONTINUATION", "false").lower() == "true",
        faq_soft_direct_enabled=os.getenv("CHATBOT_FAQ_SOFT_DIRECT_ENABLED", "true").lower() == "true",
        faq_soft_direct_min_score=max(0.0, min(1.0, float(os.getenv("CHATBOT_FAQ_SOFT_DIRECT_MIN_SCORE", "0.95")))),
        faq_soft_direct_snippet_top_n=max(1, min(10, int(os.getenv("CHATBOT_FAQ_SOFT_DIRECT_SNIPPET_TOP_N", "1")))),
        llm_context_total_tokens=max(2048, int(os.getenv("CHATBOT_LLM_CONTEXT_TOTAL_TOKENS", "40960"))),
        llm_completion_budget_slack_tokens=max(
            64, int(os.getenv("CHATBOT_LLM_COMPLETION_SLACK_TOKENS", "768"))
        ),
        history_trim_enabled=os.getenv("CHATBOT_HISTORY_TRIM_ENABLED", "true").lower() == "true",
        history_trim_min_keep=max(0, int(os.getenv("CHATBOT_HISTORY_TRIM_MIN_KEEP", "0"))),
    )
    _app_env = (os.getenv("APP_ENV", "dev") or "dev").strip().lower()
    _redis_url_configured = bool((os.getenv("REDIS_URL") or "").strip())
    # 看图诊断 scope HITL：生产或已配置 REDIS_URL 时默认 redis；单机无 Redis 可显式设为 memory
    _img_diag_persist_default = (
        "redis"
        if _app_env in ("production", "prod") or _redis_url_configured
        else "memory"
    )
    analysis_cfg = AnalysisConfig(
        default_report_template=(os.getenv("ANALYSIS_DEFAULT_REPORT_TEMPLATE", "standard") or "standard").strip(),
        default_chart_mode=(os.getenv("ANALYSIS_DEFAULT_CHART_MODE", "auto") or "auto").strip().lower(),
        default_report_style=(os.getenv("ANALYSIS_DEFAULT_REPORT_STYLE", "standard") or "standard").strip(),
        default_max_nl2sql_calls=max(1, int(os.getenv("ANALYSIS_DEFAULT_MAX_NL2SQL_CALLS", "15"))),
        default_max_rows_per_query=max(50, int(os.getenv("ANALYSIS_DEFAULT_MAX_ROWS_PER_QUERY", "2000"))),
        default_max_suggestions=max(1, min(20, int(os.getenv("ANALYSIS_DEFAULT_MAX_SUGGESTIONS", "8")))),
        synthesis_timeout_seconds=max(5.0, float(os.getenv("ANALYSIS_SYNTHESIS_TIMEOUT_SECONDS", "90"))),
        synthesis_gathered_json_max_chars=max(
            1000, int(os.getenv("ANALYSIS_SYNTHESIS_GATHERED_JSON_MAX_CHARS", "16000"))
        ),
        synthesis_max_tokens=max(256, int(os.getenv("ANALYSIS_SYNTHESIS_MAX_TOKENS", "8192"))),
        synthesis_strategy=(
            (os.getenv("ANALYSIS_SYNTHESIS_STRATEGY", "v1") or "v1").strip().lower()
        ),
        synthesis_strategy_overheat_guidance=_env_synthesis_strategy_type("OVERHEAT_GUIDANCE"),
        synthesis_strategy_maintenance_strategy=_env_synthesis_strategy_type("MAINTENANCE_STRATEGY"),
        synthesis_strategy_four_tube_health_interpretation=_env_synthesis_strategy_type(
            "FOUR_TUBE_HEALTH_INTERPRETATION"
        ),
        synthesis_strategy_leakage_burst_analysis=_env_synthesis_strategy_type("LEAKAGE_BURST_ANALYSIS"),
        synthesis_strategy_custom=_env_synthesis_strategy_type("CUSTOM"),
        plan_template_version=_env_analysis_template_version_global("ANALYSIS_PLAN_TEMPLATE_VERSION"),
        plan_template_version_overheat_guidance=_env_analysis_template_version_type(
            "PLAN", "OVERHEAT_GUIDANCE"
        ),
        plan_template_version_maintenance_strategy=_env_analysis_template_version_type(
            "PLAN", "MAINTENANCE_STRATEGY"
        ),
        plan_template_version_four_tube_health_interpretation=_env_analysis_template_version_type(
            "PLAN", "FOUR_TUBE_HEALTH_INTERPRETATION"
        ),
        plan_template_version_leakage_burst_analysis=_env_analysis_template_version_type(
            "PLAN", "LEAKAGE_BURST_ANALYSIS"
        ),
        plan_template_version_img_diag_defect_ident=_env_analysis_template_version_type(
            "PLAN", "IMG_DIAG_DEFECT_IDENT"
        )
        or "v1",
        plan_template_version_img_diag_leakage_burst=_env_analysis_template_version_type(
            "PLAN", "IMG_DIAG_LEAKAGE_BURST"
        )
        or "v1",
        plan_template_version_custom=_env_analysis_template_version_type("PLAN", "CUSTOM"),
        synthesis_template_version=_env_analysis_template_version_global(
            "ANALYSIS_SYNTHESIS_TEMPLATE_VERSION"
        ),
        synthesis_template_version_overheat_guidance=_env_analysis_template_version_type(
            "SYNTHESIS", "OVERHEAT_GUIDANCE"
        ),
        synthesis_template_version_maintenance_strategy=_env_analysis_template_version_type(
            "SYNTHESIS", "MAINTENANCE_STRATEGY"
        ),
        synthesis_template_version_four_tube_health_interpretation=_env_analysis_template_version_type(
            "SYNTHESIS", "FOUR_TUBE_HEALTH_INTERPRETATION"
        ),
        synthesis_template_version_leakage_burst_analysis=_env_analysis_template_version_type(
            "SYNTHESIS", "LEAKAGE_BURST_ANALYSIS"
        ),
        synthesis_template_version_img_diag_defect_ident=_env_analysis_template_version_type(
            "SYNTHESIS", "IMG_DIAG_DEFECT_IDENT"
        )
        or "v1",
        synthesis_template_version_img_diag_leakage_burst=_env_analysis_template_version_type(
            "SYNTHESIS", "IMG_DIAG_LEAKAGE_BURST"
        )
        or "v1",
        synthesis_template_version_custom=_env_analysis_template_version_type("SYNTHESIS", "CUSTOM"),
        synthesis_v2_max_parallel_llm=max(
            1, int(os.getenv("ANALYSIS_SYNTHESIS_V2_MAX_PARALLEL_LLM", "3"))
        ),
        synthesis_v2_segment_max_tokens=max(
            256, int(os.getenv("ANALYSIS_SYNTHESIS_V2_SEGMENT_MAX_TOKENS", "4096"))
        ),
        synthesis_v2_table_max_rows=max(
            1, int(os.getenv("ANALYSIS_SYNTHESIS_V2_TABLE_MAX_ROWS", "80"))
        ),
        synthesis_v2_enable_structured_sse_events=os.getenv(
            "ANALYSIS_SYNTHESIS_V2_ENABLE_STRUCTURED_SSE", "true"
        ).lower()
        != "false",
        synthesis_v2_stream_live_first=os.getenv(
            "ANALYSIS_SYNTHESIS_V2_STREAM_LIVE_FIRST", "false"
        ).lower()
        != "false",
        synthesis_v2_stream_chunk_chars=max(
            1, int(os.getenv("ANALYSIS_SYNTHESIS_V2_STREAM_CHUNK_CHARS", "16"))
        ),
        synthesis_v2_stream_chunk_delay_ms=max(
            0.0,
            float(os.getenv("ANALYSIS_SYNTHESIS_V2_STREAM_CHUNK_DELAY_MS", "18")),
        ),
        synthesis_v2_idle_heartbeat_seconds=max(
            0.5, float(os.getenv("ANALYSIS_SYNTHESIS_V2_IDLE_HEARTBEAT_SECONDS", "5"))
        ),
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
        nl2sql_acquire_parallel_enabled=os.getenv("ANALYSIS_NL2SQL_ACQUIRE_PARALLEL_ENABLED", "true").lower()
        == "true",
        nl2sql_acquire_max_parallel=max(1, int(os.getenv("ANALYSIS_NL2SQL_ACQUIRE_MAX_PARALLEL", "8"))),
        nl2sql_cache_enabled=os.getenv("NL2SQL_CACHE_ENABLED", "false").lower() == "true",
        nl2sql_cache_ttl_seconds=max(60, int(os.getenv("NL2SQL_CACHE_TTL_SECONDS", "3600"))),
        nl2sql_cache_max_entries=max(16, int(os.getenv("NL2SQL_CACHE_MAX_ENTRIES", "512"))),
        nl2sql_l1_cache_enabled=os.getenv("NL2SQL_L1_CACHE_ENABLED", "true").lower() == "true",
        nl2sql_qa_feedback_enabled=os.getenv("NL2SQL_QA_FEEDBACK_ENABLED", "false").lower() == "true",
        plan_rag_query_mode=(
            (os.getenv("ANALYSIS_PLAN_RAG_QUERY_MODE", "two_stage") or "two_stage").strip().lower()
        ),
        img_diag_vision_timeout_seconds=max(
            5.0, float(os.getenv("ANALYSIS_IMG_DIAG_VISION_TIMEOUT_SECONDS", "120"))
        ),
        img_diag_vision_temperature=max(
            0.0,
            min(1.0, float(os.getenv("ANALYSIS_IMG_DIAG_VISION_TEMPERATURE", "0.45"))),
        ),
        img_diag_vision_chatbot_prompt_version=(
            os.getenv("ANALYSIS_IMG_DIAG_VISION_CHATBOT_PROMPT_VERSION") or ""
        ).strip()
        or None,
        img_diag_vision_user_query_defect_ident=(
            os.getenv("ANALYSIS_IMG_DIAG_VISION_USER_QUERY_DEFECT_IDENT")
            or "请帮我分析图片缺陷"
        ).strip(),
        img_diag_vision_user_query_leakage_burst=(
            os.getenv("ANALYSIS_IMG_DIAG_VISION_USER_QUERY_LEAKAGE_BURST")
            or "请分析图片中的爆口/泄漏可见形貌特征。"
        ).strip(),
        img_diag_lane_timeout_seconds=max(
            10.0, float(os.getenv("ANALYSIS_IMG_DIAG_LANE_TIMEOUT_SECONDS", "180"))
        ),
        img_diag_upload_max_mb=max(1, int(os.getenv("ANALYSIS_IMG_DIAG_UPLOAD_MAX_MB", "15"))),
        img_diag_rag_mode=(os.getenv("ANALYSIS_IMG_DIAG_RAG_MODE") or "vision_augmented").strip().lower(),
        img_diag_use_langgraph=os.getenv("ANALYSIS_IMG_DIAG_USE_LANGGRAPH", "true").lower() != "false",
        img_diag_scope_hitl_enabled=os.getenv("ANALYSIS_IMG_DIAG_SCOPE_HITL_ENABLED", "true").lower() != "false",
        img_diag_scope_hitl_max_rounds=max(1, int(os.getenv("ANALYSIS_IMG_DIAG_SCOPE_HITL_MAX_ROUNDS", "5"))),
        img_diag_scope_matched_confirm_enabled=os.getenv(
            "ANALYSIS_IMG_DIAG_SCOPE_MATCHED_CONFIRM_ENABLED", "true"
        ).lower()
        != "false",
        img_diag_scope_auto_relax_enabled=os.getenv(
            "ANALYSIS_IMG_DIAG_SCOPE_AUTO_RELAX_ENABLED", "false"
        ).lower()
        == "true",
        img_diag_scope_low_confidence_hitl=os.getenv(
            "ANALYSIS_IMG_DIAG_SCOPE_LOW_CONFIDENCE_HITL", "true"
        ).lower()
        != "false",
        img_diag_scope_validate_sql=(os.getenv("ANALYSIS_IMG_DIAG_SCOPE_VALIDATE_SQL") or "").strip() or None,
        img_diag_scope_validate_timeout_s=max(
            1.0, float(os.getenv("ANALYSIS_IMG_DIAG_SCOPE_VALIDATE_TIMEOUT_S", "10"))
        ),
        img_diag_scope_validate_skip_on_error=os.getenv(
            "ANALYSIS_IMG_DIAG_SCOPE_VALIDATE_SKIP_ON_ERROR", "false"
        ).lower()
        == "true",
        img_diag_scope_candidate_pick_enabled=os.getenv(
            "ANALYSIS_IMG_DIAG_SCOPE_CANDIDATE_PICK_ENABLED", "true"
        ).lower()
        != "false",
        img_diag_scope_candidate_pick_after_mismatch_rounds=max(
            1,
            int(os.getenv("ANALYSIS_IMG_DIAG_SCOPE_CANDIDATE_PICK_AFTER_MISMATCH_ROUNDS", "2")),
        ),
        img_diag_scope_candidate_limit=max(
            1, int(os.getenv("ANALYSIS_IMG_DIAG_SCOPE_CANDIDATE_LIMIT", "50"))
        ),
        img_diag_scope_candidate_top_k=max(
            1, int(os.getenv("ANALYSIS_IMG_DIAG_SCOPE_CANDIDATE_TOP_K", "5"))
        ),
        img_diag_scope_candidate_rank_prompt_version=(
            os.getenv("ANALYSIS_IMG_DIAG_SCOPE_CANDIDATE_RANK_PROMPT_VERSION") or "v1"
        ).strip()
        or "v1",
        img_diag_scope_candidate_rank_timeout_s=max(
            1.0, float(os.getenv("ANALYSIS_IMG_DIAG_SCOPE_CANDIDATE_RANK_TIMEOUT_S", "20"))
        ),
        img_diag_scope_diagnose_boiler_sql=(
            os.getenv("ANALYSIS_IMG_DIAG_SCOPE_DIAGNOSE_BOILER_SQL") or ""
        ).strip()
        or None,
        img_diag_scope_candidate_sql_boiler=(
            os.getenv("ANALYSIS_IMG_DIAG_SCOPE_CANDIDATE_SQL_BOILER") or ""
        ).strip()
        or None,
        img_diag_scope_candidate_sql_device=(
            os.getenv("ANALYSIS_IMG_DIAG_SCOPE_CANDIDATE_SQL_DEVICE") or ""
        ).strip()
        or None,
        img_diag_scope_candidate_sql_location=(
            os.getenv("ANALYSIS_IMG_DIAG_SCOPE_CANDIDATE_SQL_LOCATION") or ""
        ).strip()
        or None,
        img_diag_scope_candidate_sql_row=(
            os.getenv("ANALYSIS_IMG_DIAG_SCOPE_CANDIDATE_SQL_ROW") or ""
        ).strip()
        or None,
        img_diag_scope_candidate_sql_tube=(
            os.getenv("ANALYSIS_IMG_DIAG_SCOPE_CANDIDATE_SQL_TUBE") or ""
        ).strip()
        or None,
        img_diag_checkpoint_backend=(
            os.getenv("ANALYSIS_IMG_DIAG_CHECKPOINT_BACKEND", _img_diag_persist_default)
            or _img_diag_persist_default
        ).strip().lower(),
        img_diag_checkpoint_redis_url=(os.getenv("ANALYSIS_IMG_DIAG_CHECKPOINT_REDIS_URL") or os.getenv("REDIS_URL") or "").strip()
        or None,
        img_diag_checkpoint_namespace=(
            os.getenv("ANALYSIS_IMG_DIAG_CHECKPOINT_NAMESPACE", "img_diag") or "img_diag"
        ).strip(),
        img_diag_session_store_backend=(
            os.getenv("ANALYSIS_IMG_DIAG_SESSION_STORE_BACKEND", _img_diag_persist_default)
            or _img_diag_persist_default
        ).strip().lower(),
        img_diag_session_store_redis_url=(
            os.getenv("ANALYSIS_IMG_DIAG_SESSION_STORE_REDIS_URL") or os.getenv("REDIS_URL") or ""
        ).strip()
        or None,
        img_diag_session_ttl_seconds=max(
            60, int(os.getenv("ANALYSIS_IMG_DIAG_SESSION_TTL_SECONDS", "172800"))
        ),
        img_diag_resume_sse_idle_ping_seconds=max(
            0.0,
            float(os.getenv("ANALYSIS_IMG_DIAG_RESUME_SSE_IDLE_PING_SECONDS", "12")),
        ),
    )
    _aa_persist_default = "redis" if _app_env in ("production", "prod") else "memory"
    analysis_agent_cfg = AnalysisAgentConfig(
        enabled=os.getenv("ANALYSIS_AGENT_ENABLED", "true").lower() != "false",
        use_langgraph=os.getenv("ANALYSIS_AGENT_USE_LANGGRAPH", "true").lower() != "false",
        use_react_agent=os.getenv("ANALYSIS_AGENT_USE_REACT_AGENT", "true").lower() != "false",
        checkpoint_backend=(
            os.getenv("ANALYSIS_AGENT_CHECKPOINT_BACKEND", _aa_persist_default) or _aa_persist_default
        ).strip().lower(),
        checkpoint_redis_url=(os.getenv("ANALYSIS_AGENT_CHECKPOINT_REDIS_URL") or os.getenv("REDIS_URL") or "").strip()
        or None,
        checkpoint_namespace=(os.getenv("ANALYSIS_AGENT_CHECKPOINT_NAMESPACE", "analysis_agent") or "analysis_agent").strip(),
        session_store_backend=(
            os.getenv("ANALYSIS_AGENT_SESSION_STORE_BACKEND", _aa_persist_default) or _aa_persist_default
        ).strip().lower(),
        session_store_redis_url=(
            os.getenv("ANALYSIS_AGENT_SESSION_STORE_REDIS_URL") or os.getenv("REDIS_URL") or ""
        ).strip()
        or None,
        session_ttl_seconds=max(60, int(os.getenv("ANALYSIS_AGENT_SESSION_TTL_SECONDS", "3600"))),
        slot_nl2sql_max_retries=max(0, int(os.getenv("ANALYSIS_AGENT_SLOT_NL2SQL_MAX_RETRIES", "2"))),
        slot_synth_max_retries=max(0, int(os.getenv("ANALYSIS_AGENT_SLOT_SYNTH_MAX_RETRIES", "1"))),
        react_max_iterations=max(1, int(os.getenv("ANALYSIS_AGENT_REACT_MAX_ITERATIONS", "8"))),
        stream_chunk_chars=max(1, int(os.getenv("ANALYSIS_AGENT_STREAM_CHUNK_CHARS", "256"))),
        enable_human_in_the_loop=os.getenv("ANALYSIS_AGENT_ENABLE_HUMAN_IN_THE_LOOP", "true").lower() != "false",
        rag_top_k=max(1, int(os.getenv("ANALYSIS_AGENT_RAG_TOP_K", "8"))),
        gathered_json_max_chars=max(1000, int(os.getenv("ANALYSIS_AGENT_GATHERED_JSON_MAX_CHARS", "12000"))),
        narrative_max_tokens=max(256, int(os.getenv("ANALYSIS_AGENT_NARRATIVE_MAX_TOKENS", "4096"))),
        nl2sql_disable_qa_slot_replay=os.getenv("ANALYSIS_AGENT_NL2SQL_DISABLE_QA_SLOT_REPLAY", "true").lower()
        != "false",
        enable_structured_sse_events=os.getenv("ANALYSIS_AGENT_ENABLE_STRUCTURED_SSE", "true").lower() != "false",
        trace_backend=(os.getenv("ANALYSIS_AGENT_TRACE_BACKEND", "memory") or "memory").strip().lower(),
        trace_ttl_minutes=max(10, int(os.getenv("ANALYSIS_AGENT_TRACE_TTL_MINUTES", "1440"))),
        trace_max_items=max(100, int(os.getenv("ANALYSIS_AGENT_TRACE_MAX_ITEMS", "5000"))),
        plan_template_version=(
            os.getenv("ANALYSIS_AGENT_PLAN_TEMPLATE_VERSION", "analysis_agent_v1") or "analysis_agent_v1"
        ).strip(),
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
        llm_timeout_seconds=max(10.0, float(os.getenv("INSPECT_EXTRACT_LLM_TIMEOUT_SECONDS", "300"))),
        llm_temperature=max(0.0, min(2.0, float(os.getenv("INSPECT_EXTRACT_LLM_TEMPERATURE", "0.3")))),
        llm_context_total_tokens=max(2048, int(os.getenv("INSPECT_EXTRACT_LLM_CONTEXT_TOKENS", "32768"))),
        llm_completion_budget_slack_tokens=max(64, int(os.getenv("INSPECT_EXTRACT_LLM_COMPLETION_SLACK_TOKENS", "768"))),
        llm_max_tokens_parse=max(128, int(os.getenv("INSPECT_EXTRACT_LLM_MAX_TOKENS_PARSE", "1024"))),
        llm_max_tokens_classify=max(128, int(os.getenv("INSPECT_EXTRACT_LLM_MAX_TOKENS_CLASSIFY", "1024"))),
        llm_max_tokens_repair=max(128, int(os.getenv("INSPECT_EXTRACT_LLM_MAX_TOKENS_REPAIR", "768"))),
        parse_concurrency=max(1, int(os.getenv("INSPECT_EXTRACT_PARSE_CONCURRENCY", "1"))),
        log_llm_raw_response=_inspect_log_llm_raw,
        log_llm_raw_max_chars=max(200, int(os.getenv("INSPECT_EXTRACT_LOG_LLM_RAW_MAX_CHARS", "2000"))),
        log_parse_chunk_full=_inspect_log_parse_chunk_full,
        log_parse_chunk_max_chars=max(0, int(os.getenv("INSPECT_EXTRACT_LOG_PARSE_CHUNK_MAX_CHARS", "0"))),
        pipeline_version=(os.getenv("INSPECT_EXTRACT_PIPELINE_VERSION", "v1") or "v1").strip().lower(),
        v2_shading_candidate_fills=_v2_fills_list,
        v2_parse_unit_max_chars=max(2000, int(os.getenv("INSPECT_EXTRACT_V2_PARSE_UNIT_MAX_CHARS", "6000"))),
        v2_table_row_window_enabled=os.getenv("INSPECT_EXTRACT_V2_TABLE_ROW_WINDOW_ENABLED", "true").lower()
        in ("1", "true", "yes", "on"),
        v2_table_data_rows_per_window=max(
            1, int(os.getenv("INSPECT_EXTRACT_V2_TABLE_DATA_ROWS_PER_WINDOW", "20"))
        ),
        v2_table_column_split_enabled=os.getenv("INSPECT_EXTRACT_V2_TABLE_COLUMN_SPLIT_ENABLED", "false").lower()
        in ("1", "true", "yes", "on"),
        v2_classify_batch_size=max(8, min(200, int(os.getenv("INSPECT_EXTRACT_V2_CLASSIFY_BATCH_SIZE", "40")))),
        v2_color_guard_enabled=os.getenv("INSPECT_EXTRACT_V2_COLOR_GUARD", "true").lower()
        in ("1", "true", "yes", "on"),
        v2_bind_guard_enabled=os.getenv("INSPECT_EXTRACT_V2_BIND_GUARD", "true").lower()
        in ("1", "true", "yes", "on"),
        v2_combo_guard_enabled=os.getenv("INSPECT_EXTRACT_V2_COMBO_GUARD", "true").lower()
        in ("1", "true", "yes", "on"),
        v2_tube_direction_sign_guard_enabled=os.getenv(
            "INSPECT_EXTRACT_V2_TUBE_DIRECTION_SIGN_GUARD", "true"
        ).lower()
        in ("1", "true", "yes", "on"),
        v2_tube_direction_sign_allow_fallback_4col=os.getenv(
            "INSPECT_EXTRACT_V2_TUBE_DIRECTION_SIGN_ALLOW_FALLBACK_4COL", "false"
        ).lower()
        in ("1", "true", "yes", "on"),
        v2_llm_parse_table_only=os.getenv("INSPECT_EXTRACT_V2_LLM_PARSE_TABLE_ONLY", "true").lower()
        in ("1", "true", "yes", "on"),
        v2_llm_strip_trailing_empty_cols=os.getenv(
            "INSPECT_EXTRACT_V2_LLM_STRIP_TRAILING_EMPTY_COLS", "true"
        ).lower()
        in ("1", "true", "yes", "on"),
        async_jobs_state_dir=(
            os.getenv("INSPECT_EXTRACT_ASYNC_JOBS_DIR", "./data/inspection_extract_jobs") or "./data/inspection_extract_jobs"
        ).strip(),
        async_queue_workers=max(1, int(os.getenv("INSPECT_EXTRACT_ASYNC_QUEUE_WORKERS", "2"))),
    )

    inspection_extract_v0_cfg = InspectionExtractV0Config(
        enabled=os.getenv("INSPECT_EXTRACT_V0_ENABLED", "false").lower() == "true",
        strict_default=os.getenv("INSPECT_EXTRACT_V0_STRICT_DEFAULT", "false").lower() == "true",
        prompt_version=(os.getenv("INSPECT_EXTRACT_V0_PROMPT_VERSION", "v2") or "v2").strip(),
        model_name=(os.getenv("INSPECT_EXTRACT_V0_MODEL_NAME") or "").strip() or None,
        llm_timeout_seconds=max(10.0, float(os.getenv("INSPECT_EXTRACT_V0_LLM_TIMEOUT_SECONDS", "300"))),
        llm_temperature=max(0.0, min(2.0, float(os.getenv("INSPECT_EXTRACT_V0_LLM_TEMPERATURE", "0.3")))),
        llm_max_tokens_extract=max(256, int(os.getenv("INSPECT_EXTRACT_V0_LLM_MAX_TOKENS_EXTRACT", "4096"))),
        layout_ocr_endpoint=(os.getenv("INSPECT_EXTRACT_V0_LAYOUT_OCR_ENDPOINT", "http://127.0.0.1:8010") or "http://127.0.0.1:8010").rstrip("/"),
        layout_ocr_timeout_seconds=max(10.0, float(os.getenv("INSPECT_EXTRACT_V0_LAYOUT_OCR_TIMEOUT_SECONDS", "300"))),
        layout_ocr_max_upload_mb=max(1, int(os.getenv("INSPECT_EXTRACT_V0_LAYOUT_OCR_MAX_UPLOAD_MB", "32"))),
        max_pdf_pages_preprocess=max(1, min(50, int(os.getenv("INSPECT_EXTRACT_V0_MAX_PDF_PAGES", "5")))),
        docx_use_layout_ocr=os.getenv("INSPECT_EXTRACT_V0_DOCX_USE_LAYOUT_OCR", "true").lower() in ("1", "true", "yes", "on"),
        llm_table_chunk_concurrency=max(1, int(os.getenv("INSPECT_EXTRACT_V0_LLM_TABLE_CHUNK_CONCURRENCY", "4"))),
        llm_table_chunk_max_blocks=max(80, int(os.getenv("INSPECT_EXTRACT_V0_LLM_TABLE_CHUNK_MAX_BLOCKS", "120"))),
        langgraph_use_sqlite_checkpoint=os.getenv("INSPECT_EXTRACT_V0_LANGGRAPH_USE_SQLITE", "false").lower() in ("1", "true", "yes", "on"),
        langgraph_checkpoint_filename=(
            os.getenv("INSPECT_EXTRACT_V0_LANGGRAPH_CHECKPOINT_FILE", "langgraph_checkpoint.sqlite") or "langgraph_checkpoint.sqlite"
        ).strip(),
        async_queue_workers=max(1, int(os.getenv("INSPECT_EXTRACT_V0_ASYNC_QUEUE_WORKERS", "2"))),
    )

    _scope_lexicon_file = (os.getenv("NL2SQL_SCOPE_LEXICON_FILE") or "").strip() or None
    nl2sql_intent_cfg = NL2SQLIntentConfig(
        scope_sql_rewrite_enabled=os.getenv("NL2SQL_SCOPE_SQL_REWRITE_ENABLED", "true").lower() == "true",
        scope_lexicon_file=_scope_lexicon_file,
        intent_parse_mode=(os.getenv("NL2SQL_INTENT_PARSE_MODE", "rule") or "rule").strip().lower(),
        scope_parse_llm_timeout_ms=max(500, int(os.getenv("NL2SQL_SCOPE_PARSE_LLM_TIMEOUT_MS", "8000"))),
        scope_parse_prompt_version=(os.getenv("NL2SQL_SCOPE_PARSE_PROMPT_VERSION", "v1") or "v1").strip(),
        scope_parse_llm_max_tokens=max(64, int(os.getenv("NL2SQL_SCOPE_PARSE_LLM_MAX_TOKENS", "512"))),
        scope_parse_llm_temperature=float(os.getenv("NL2SQL_SCOPE_PARSE_LLM_TEMPERATURE", "0")),
        scope_parse_log_rule_llm_diff=os.getenv("NL2SQL_SCOPE_PARSE_LOG_RULE_LLM_DIFF", "false").lower()
        == "true",
        inject_parsed_intent=os.getenv("NL2SQL_INJECT_PARSED_INTENT", "false").lower() == "true",
        response_include_parsed_intent=os.getenv("NL2SQL_RESPONSE_INCLUDE_PARSED_INTENT", "false").lower()
        == "true",
        trace_include_question_intent=os.getenv("NL2SQL_TRACE_INCLUDE_QUESTION_INTENT", "true").lower()
        == "true",
        anchor_fallback_now_enabled=os.getenv("NL2SQL_ANCHOR_FALLBACK_NOW_ENABLED", "true").lower()
        == "true",
        anchor_fallback_analysis_types=(
            os.getenv(
                "NL2SQL_ANCHOR_FALLBACK_ANALYSIS_TYPES",
                "img_diag_leakage_burst,img_diag_defect_ident",
            )
            or "img_diag_leakage_burst,img_diag_defect_ident"
        ).strip(),
        reject_unresolved_time_placeholders=os.getenv(
            "NL2SQL_REJECT_UNRESOLVED_TIME_PLACEHOLDERS", "true"
        ).lower()
        == "true",
    )

    face_vector_cfg = FaceVectorConfig(
        backend=(os.getenv("FACE_VECTOR_BACKEND", "local") or "local").strip().lower(),
        milvus_uri=(os.getenv("MILVUS_URI", "http://127.0.0.1:19530") or "http://127.0.0.1:19530").strip(),
        milvus_collection=(
            os.getenv("MILVUS_FACE_COLLECTION", "face_embeddings") or "face_embeddings"
        ).strip(),
        embedding_dim=max(1, int(os.getenv("FACE_EMBEDDING_DIM", "512"))),
        milvus_metric=(os.getenv("MILVUS_FACE_METRIC", "COSINE") or "COSINE").strip().upper(),
    )

    cfg = AppConfig(
        env=env,
        llm=llm_cfg,
        logging=logging_cfg,
        rag=rag_cfg,
        face_vector=face_vector_cfg,
        mineru=mineru_cfg,
        chatbot=chatbot_cfg,
        analysis=analysis_cfg,
        nl2sql_intent=nl2sql_intent_cfg,
        analysis_agent=analysis_agent_cfg,
        inspection_extract=inspection_extract_cfg,
        inspection_extract_v0=inspection_extract_v0_cfg,
    )
    # 动态附加 db 字段，避免破坏现有 AppConfig 初始化调用点
    setattr(cfg, "db", db_cfg)
    return cfg


def _env_synthesis_strategy_type(env_suffix: str) -> str | None:
    """解析 ANALYSIS_SYNTHESIS_STRATEGY_<SUFFIX>，仅接受 v1/v2。"""
    raw = (os.getenv(f"ANALYSIS_SYNTHESIS_STRATEGY_{env_suffix}") or "").strip().lower()
    return raw if raw in ("v1", "v2") else None


def _env_analysis_template_version_global(env_name: str) -> str | None:
    """解析全局模板版本环境变量（如 ANALYSIS_PLAN_TEMPLATE_VERSION）。"""
    raw = (os.getenv(env_name) or "").strip()
    return raw or None


def _env_analysis_template_version_type(kind: str, env_suffix: str) -> str | None:
    """
    解析按专项的模板版本：ANALYSIS_PLAN_TEMPLATE_VERSION_<SUFFIX> 或
    ANALYSIS_SYNTHESIS_TEMPLATE_VERSION_<SUFFIX>。
    """
    prefix = "ANALYSIS_PLAN_TEMPLATE_VERSION" if kind == "PLAN" else "ANALYSIS_SYNTHESIS_TEMPLATE_VERSION"
    raw = (os.getenv(f"{prefix}_{env_suffix}") or "").strip()
    return raw or None


@lru_cache(maxsize=1)
def get_app_config() -> AppConfig:
    """
    获取全局 AppConfig（单例缓存）。
    """
    return _load_from_env()

