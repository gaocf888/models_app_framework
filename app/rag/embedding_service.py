"""
RAG 嵌入向量服务。

支持企业级嵌入模型，采用「离线优先、在线回退」的配置化加载方式：
- 优先从本地路径加载模型（EMBEDDING_MODEL_PATH），适用于无外网或内网部署；
- 若本地未配置或路径无效，则使用模型名（EMBEDDING_MODEL_NAME）从 HuggingFace 在线下载；
- 在线下载失败时捕获异常并打印日志后抛出，由调用方决定是否降级。

默认使用 BAAI/bge-small-zh-v1.5（中文场景常用、体积小、效果稳定），
可通过配置切换为其他 sentence-transformers 兼容模型（如 bge-m3、Qwen3-Embedding 等）。

Qwen3-Embedding 等 instruction-aware 模型需配置 EMBEDDING_QUERY_PROMPT_NAME=query：
- embed_text（检索 query）带 prompt；
- embed_texts（知识摄入 document/chunk）不带 prompt。

推理设备可通过 EMBEDDING_DEVICE 显式指定（cpu / cuda / cuda:0 等），与 RAG_RERANKER_DEVICE 用法一致。

加载时显式使用 float16（model_kwargs），避免 sentence-transformers / transformers 默认 FP32 导致显存占用翻倍。
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
    """CrossEncoder 加载参数；Qwen3-Reranker 须 left padding，否则 batch predict 可能 seq_len=0 崩溃。"""
    use_qwen = trust_remote_code or _is_qwen_reranker_model(model_id)
    kwargs: dict[str, Any] = {
        "trust_remote_code": use_qwen,
        "model_kwargs": rag_model_load_kwargs(),
    }
    if use_qwen:
        pad = {"padding_side": "left"}
        # sentence-transformers 新版用 processor_kwargs；旧版仍读 tokenizer_kwargs
        kwargs["processor_kwargs"] = pad
        kwargs["tokenizer_kwargs"] = pad
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
    嵌入向量服务：基于 sentence-transformers 的企业级嵌入模型。

    加载策略（由配置决定）：
    1. 若配置了 EMBEDDING_MODEL_PATH 且路径存在，则从该路径加载模型（离线）；
    2. 否则使用 EMBEDDING_MODEL_NAME 从 HuggingFace 下载并加载（在线）；
    3. 若在线下载失败，则记录异常日志并抛出 RuntimeError。
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

        self._model = None
        self._dim: int = 0
        self._init_model()

    def _log_loaded(self, *, source: str, load_id: str) -> None:
        target_device = _sentence_transformer_device_repr(self._model) if self._model is not None else "?"
        logger.info(
            "EmbeddingService: loaded %s model=%s embedding_dim=%s device=%s "
            "configured_device=%s torch_dtype=%s query_prompt_name=%s trust_remote_code=%s",
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

    def embed_text(self, text: str) -> list[float]:
        """单条文本嵌入（检索 query 路径；instruction-aware 模型会使用 query prompt）。"""
        encode_kwargs: dict[str, Any] = {"normalize_embeddings": True}
        if self._query_prompt_name:
            encode_kwargs["prompt_name"] = self._query_prompt_name
        emb = self._model.encode(text, **encode_kwargs)
        return emb.tolist()

    def embed_texts(self, texts: List[str]) -> List[list[float]]:
        """批量文本嵌入（知识摄入 document/chunk 路径，不使用 query prompt）。"""
        arr = self._model.encode(texts, normalize_embeddings=True)
        return arr.tolist()
