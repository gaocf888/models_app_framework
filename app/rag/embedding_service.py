"""
RAG 嵌入向量服务。

支持企业级嵌入模型，采用「离线优先、在线回退」的配置化加载方式：
- 优先从本地路径加载模型（EMBEDDING_MODEL_PATH），适用于无外网或内网部署；
- 若本地未配置或路径无效，则使用模型名（EMBEDDING_MODEL_NAME）从 HuggingFace 在线下载；
- 在线下载失败时捕获异常并打印日志后抛出，由调用方决定是否降级。

默认使用 BAAI/bge-small-zh-v1.5（中文场景常用、体积小、效果稳定），
可通过配置切换为其他 sentence-transformers 兼容模型（如 bge-m3、multilingual 等）。
"""

from __future__ import annotations

import os
from typing import List

from app.core.config import get_app_config
from app.core.logging import get_logger

logger = get_logger(__name__)


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

        self._model = None
        self._dim: int = 0
        self._init_model()

    def _init_model(self) -> None:
        """按「离线优先、在线回退」顺序加载模型，失败时打日志并抛出异常。"""
        load_from_path = self._model_path and os.path.isdir(self._model_path)

        if load_from_path:
            try:
                self._model = self._load_sentence_transformer(self._model_path)
                self._dim = self._model.get_sentence_embedding_dimension()
                logger.info(
                    "EmbeddingService: loaded offline model from path=%s, embedding_dim=%s",
                    self._model_path,
                    self._dim,
                )
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
            logger.info(
                "EmbeddingService: loaded online model name=%s, embedding_dim=%s",
                self._model_name,
                self._dim,
            )
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

    @staticmethod
    def _load_sentence_transformer(name_or_path: str):
        """延迟导入 sentence_transformers 并加载模型。"""
        try:
            from sentence_transformers import SentenceTransformer  # type: ignore[import-untyped]
        except ImportError as e:
            raise ImportError(
                "sentence_transformers is required for embedding. "
                "Install with: pip install -r requirements-大模型应用.txt (or pip install sentence-transformers)"
            ) from e
        return SentenceTransformer(name_or_path)

    @property
    def embedding_dimension(self) -> int:
        """返回当前嵌入向量维度。"""
        return self._dim

    def embed_text(self, text: str) -> list[float]:
        """单条文本嵌入。"""
        emb = self._model.encode(text, normalize_embeddings=True)
        return emb.tolist()

    def embed_texts(self, texts: List[str]) -> List[list[float]]:
        """批量文本嵌入。"""
        arr = self._model.encode(texts, normalize_embeddings=True)
        return arr.tolist()
