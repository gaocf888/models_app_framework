"""
RAG 嵌入向量服务。

支持两种后端（环境变量 EMBEDDING_BACKEND，默认 mis_tei）：
- mis_tei：HTTP 调用独立 MIS-TEI 服务（昇腾 NPU 推荐）；
- local：进程内 sentence-transformers（离线优先、在线回退）。

local 模式：
- 优先从本地路径加载（EMBEDDING_MODEL_PATH）；
- 否则使用模型名（EMBEDDING_MODEL_NAME）从 HuggingFace 下载。

Qwen3-Embedding 等 instruction-aware 模型需配置 EMBEDDING_QUERY_PROMPT_NAME=query：
- embed_text（检索 query）带 prompt；
- embed_texts（知识摄入 document/chunk）不带 prompt。
"""

from __future__ import annotations

import os
from typing import Any, List

from app.core.config import get_app_config
from app.core.logging import get_logger

logger = get_logger(__name__)

# 嵌入与 CrossEncoder 重排共用；Qwen3 等 checkpoint 为 bf16 权重，float16 在 NVIDIA GPU 上显存与兼容性均衡
RAG_MODEL_TORCH_DTYPE = "float16"


def rag_model_load_kwargs() -> dict[str, Any]:
    """SentenceTransformer / CrossEncoder 传入的 model_kwargs（半精度加载）。"""
    return {"torch_dtype": RAG_MODEL_TORCH_DTYPE}


def _is_qwen_reranker_model(model_id: str) -> bool:
    mid = (model_id or "").lower()
    return "qwen" in mid and "rerank" in mid


def rag_cross_encoder_load_kwargs(*, trust_remote_code: bool, model_id: str = "") -> dict[str, Any]:
    """CrossEncoder 加载参数（BGE 等标准 cross-encoder；Qwen3 请用 Qwen3Reranker）。"""
    kwargs: dict[str, Any] = {
        "trust_remote_code": trust_remote_code,
        "model_kwargs": rag_model_load_kwargs(),
    }
    return kwargs


def configure_qwen_cross_encoder(reranker: object) -> None:
    """加载后显式设置 tokenizer left padding 与 pad_token（兜底 ST 版本差异）。"""
    tokenizer = getattr(reranker, "tokenizer", None)
    if tokenizer is None:
        processor = getattr(reranker, "processor", None)
        tokenizer = getattr(processor, "tokenizer", None) if processor is not None else None
    if tokenizer is None:
        return
    tokenizer.padding_side = "left"
    if getattr(tokenizer, "pad_token_id", None) is None and getattr(tokenizer, "eos_token", None):
        tokenizer.pad_token = tokenizer.eos_token


def _sentence_transformer_device_repr(model: object) -> str:
    """SentenceTransformer 实际推理设备（用于日志）。"""
    try:
        return str(model.device)  # type: ignore[attr-defined]
    except Exception:
        return "?"


class EmbeddingService:
    """
    嵌入向量服务。

    backend=mis_tei：不加载本地权重，调用 MIS-TEI /embed。
    backend=local：sentence-transformers 离线优先、在线回退。
    """

    def __init__(self, model_path: str | None = None, model_name: str | None = None) -> None:
        """
        初始化嵌入服务。若未传入参数，则从 AppConfig.rag 读取 embedding_model_path / embedding_model_name。
        """
        cfg = get_app_config().rag
        self._model_path = model_path if model_path is not None else cfg.embedding_model_path
        self._model_name = model_name if model_name is not None else cfg.embedding_model_name
        self._query_prompt_name = cfg.embedding_query_prompt_name
        self._trust_remote_code = cfg.embedding_trust_remote_code
        self._configured_device = (cfg.embedding_device or "").strip() or None
        self._backend = (getattr(cfg, "embedding_backend", None) or "mis_tei").strip().lower() or "mis_tei"

        self._model = None
        self._tei_client = None
        self._dim: int = 0

        if self._backend == "mis_tei":
            self._init_mis_tei()
        else:
            if self._backend not in {"local", "sentence_transformers", "st"}:
                logger.warning(
                    "EmbeddingService: unknown embedding_backend=%s; fallback to local",
                    self._backend,
                )
            self._backend = "local"
            self._init_model()

    def _init_mis_tei(self) -> None:
        from app.rag.mis_tei_client import MisTeiEmbeddingClient

        tei = get_app_config().mis_tei
        self._tei_client = MisTeiEmbeddingClient(
            base_url=tei.embed_base_url,
            timeout_s=tei.timeout_s,
            batch_size=tei.embed_batch_size,
            normalize=tei.normalize_embeddings,
        )
        self._dim = int(tei.embedding_dim)
        # 可选探测维度（服务未就绪时保留配置值）
        try:
            probe = self._tei_client.embed(["dimension_probe"])
            if probe and probe[0]:
                self._dim = len(probe[0])
        except Exception as e:  # noqa: BLE001
            logger.warning(
                "EmbeddingService: MIS-TEI dimension probe failed; using configured dim=%s err=%s",
                self._dim,
                e,
            )
        logger.info(
            "EmbeddingService: backend=mis_tei base_url=%s embedding_dim=%s batch_size=%s normalize=%s",
            tei.embed_base_url,
            self._dim,
            tei.embed_batch_size,
            tei.normalize_embeddings,
        )

    def _log_loaded(self, *, source: str, load_id: str) -> None:
        target_device = _sentence_transformer_device_repr(self._model) if self._model is not None else "?"
        logger.info(
            "EmbeddingService: loaded %s model=%s embedding_dim=%s device=%s "
            "configured_device=%s torch_dtype=%s query_prompt_name=%s trust_remote_code=%s backend=local",
            source,
            load_id,
            self._dim,
            target_device,
            self._configured_device or "auto",
            RAG_MODEL_TORCH_DTYPE,
            self._query_prompt_name,
            self._trust_remote_code,
        )

    def _init_model(self) -> None:
        """按「离线优先、在线回退」顺序加载模型，失败时打日志并抛出异常。"""
        load_from_path = self._model_path and os.path.isdir(self._model_path)

        if load_from_path:
            try:
                self._model = self._load_sentence_transformer(self._model_path)
                self._dim = self._model.get_sentence_embedding_dimension()
                self._log_loaded(source="offline", load_id=self._model_path)
                return
            except Exception as e:
                logger.warning(
                    "EmbeddingService: failed to load from path=%s, error=%s; will try online.",
                    self._model_path,
                    e,
                    exc_info=True,
                )

        try:
            self._model = self._load_sentence_transformer(self._model_name)
            self._dim = self._model.get_sentence_embedding_dimension()
            self._log_loaded(source="online", load_id=self._model_name)
        except Exception as e:
            logger.exception(
                "EmbeddingService: online download/load failed, model_name=%s, error=%s",
                self._model_name,
                e,
            )
            raise RuntimeError(
                "EmbeddingService: failed to load embedding model (offline and online). "
                "Set EMBEDDING_MODEL_PATH to a valid local path or ensure network access for HuggingFace."
            ) from e

    def _sentence_transformer_kwargs(self) -> dict[str, Any]:
        kwargs: dict[str, Any] = {"model_kwargs": rag_model_load_kwargs()}
        if self._trust_remote_code:
            kwargs["trust_remote_code"] = True
        if self._query_prompt_name:
            kwargs["tokenizer_kwargs"] = {"padding_side": "left"}
        if self._configured_device:
            kwargs["device"] = self._configured_device
        return kwargs

    def _load_sentence_transformer(self, name_or_path: str):
        """延迟导入 sentence_transformers 并加载模型。"""
        try:
            from sentence_transformers import SentenceTransformer  # type: ignore[import-untyped]
        except ImportError as e:
            raise ImportError(
                "sentence_transformers is required for embedding. "
                "Install with: pip install -r requirements-大模型应用.txt (or pip install sentence-transformers)"
            ) from e
        return SentenceTransformer(name_or_path, **self._sentence_transformer_kwargs())

    @property
    def embedding_dimension(self) -> int:
        """返回当前嵌入向量维度。"""
        return self._dim

    @property
    def backend(self) -> str:
        return self._backend

    def embed_text(self, text: str) -> list[float]:
        """单条文本嵌入（检索 query 路径；instruction-aware 模型会使用 query prompt）。"""
        if self._backend == "mis_tei":
            assert self._tei_client is not None
            return self._tei_client.embed([text or ""])[0]
        encode_kwargs: dict[str, Any] = {"normalize_embeddings": True}
        if self._query_prompt_name:
            encode_kwargs["prompt_name"] = self._query_prompt_name
        emb = self._model.encode(text, **encode_kwargs)
        return emb.tolist()

    def embed_texts(self, texts: List[str]) -> List[list[float]]:
        """批量文本嵌入（知识摄入 document/chunk 路径，不使用 query prompt）。"""
        if self._backend == "mis_tei":
            assert self._tei_client is not None
            return self._tei_client.embed(list(texts))
        arr = self._model.encode(texts, normalize_embeddings=True)
        return arr.tolist()
