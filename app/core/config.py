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


@dataclass
class RAGConfig:
    """
    RAG 与上下文相关配置。
    """

    enable_rag_by_default: bool = True
    enable_context_by_default: bool = True
    top_k: int = 5
    vector_store_type: str = "faiss"
    faiss_index_dir: str = "./data/faiss"

    # 嵌入模型配置（离线优先、在线回退，环境变量 EMBEDDING_MODEL_PATH / EMBEDDING_MODEL_NAME）
    embedding_model_path: str | None = None
    embedding_model_name: str = "BAAI/bge-small-zh-v1.5"

    # GraphRAG（Neo4j + LangChain Graph），默认关闭，与向量 RAG 并行可选
    graph: GraphRAGConfig = field(default_factory=GraphRAGConfig)


@dataclass
class LoggingConfig:
    """
    日志相关配置。
    """

    level: str = "INFO"
    json_format: bool = False
    log_file: str | None = None


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
class AppConfig:
    """
    应用全局配置。
    """

    env: str = "dev"
    llm: LLMConfig = field(default_factory=lambda: LLMConfig(default_model="default"))
    rag: RAGConfig = field(default_factory=RAGConfig)
    logging: LoggingConfig = field(default_factory=LoggingConfig)
    prompt: PromptConfig = field(default_factory=PromptConfig)


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
    说明：后续可扩展为从 YAML/JSON/配置中心加载。
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

    graph_strategy = GraphHybridStrategyConfig(
        mode=os.getenv("GRAPH_RAG_MODE", "vector").lower(),
        vector_weight=float(os.getenv("GRAPH_RAG_VECTOR_WEIGHT", "0.6")),
        graph_weight=float(os.getenv("GRAPH_RAG_GRAPH_WEIGHT", "0.4")),
        max_context_items=int(os.getenv("GRAPH_RAG_MAX_CONTEXT_ITEMS", "20")),
        graph_hops=int(os.getenv("GRAPH_RAG_GRAPH_HOPS", "1")),
        max_graph_items=int(os.getenv("GRAPH_RAG_MAX_GRAPH_ITEMS", "20")),
        use_intent_routing=os.getenv("GRAPH_RAG_USE_INTENT_ROUTING", "false").lower() == "true",
    )
    graph_cfg = GraphRAGConfig(
        enabled=os.getenv("GRAPH_RAG_ENABLED", "false").lower() == "true",
        uri=os.getenv("NEO4J_URI"),
        username=os.getenv("NEO4J_USERNAME"),
        password=os.getenv("NEO4J_PASSWORD"),
        database=os.getenv("NEO4J_DATABASE") or None,
        schema_config_path=os.getenv("GRAPH_SCHEMA_CONFIG_PATH") or None,
        strategy=graph_strategy,
    )

    rag_cfg = RAGConfig(
        enable_rag_by_default=os.getenv("RAG_ENABLE_BY_DEFAULT", "true").lower() == "true",
        enable_context_by_default=os.getenv("RAG_ENABLE_CONTEXT_BY_DEFAULT", "true").lower() == "true",
        top_k=int(os.getenv("RAG_TOP_K", "5")),
        vector_store_type=os.getenv("RAG_VECTOR_STORE_TYPE", "faiss"),
        faiss_index_dir=os.getenv("RAG_FAISS_INDEX_DIR", "./data/faiss"),
        embedding_model_path=os.getenv("EMBEDDING_MODEL_PATH") or None,
        embedding_model_name=os.getenv("EMBEDDING_MODEL_NAME", "BAAI/bge-small-zh-v1.5"),
        graph=graph_cfg,
    )

    cfg = AppConfig(env=env, llm=llm_cfg, logging=logging_cfg, rag=rag_cfg)
    # 动态附加 db 字段，避免破坏现有 AppConfig 初始化调用点
    setattr(cfg, "db", db_cfg)
    return cfg


@lru_cache(maxsize=1)
def get_app_config() -> AppConfig:
    """
    获取全局 AppConfig（单例缓存）。
    """
    return _load_from_env()

