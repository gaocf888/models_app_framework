"""
Qwen3-Reranker 原生重排实现（AutoModelForCausalLM + yes/no logit）。

离线目录常无 sentence-transformers 的 modules.json，CrossEncoder 会退化为通用路径，
tokenize 后 seq_len=0 导致 predict 崩溃。本模块按 Qwen 官方 README 实现。
"""

from __future__ import annotations

import threading
from typing import Any, Sequence

from app.core.logging import get_logger
from app.rag.embedding_service import RAG_MODEL_TORCH_DTYPE

logger = get_logger(__name__)

DEFAULT_INSTRUCTION = "Given a web search query, retrieve relevant passages that answer the query"

_SYSTEM_PROMPT = (
    'Judge whether the Document meets the requirements based on the Query and the Instruct provided. '
    'Note that the answer can only be "yes" or "no".'
)


def format_qwen3_rerank_input(instruction: str, query: str, document: str) -> str:
    return f"<Instruct>: {instruction}\n<Query>: {query}\n<Document>: {document}"


class Qwen3Reranker:
    """与 CrossEncoder 兼容的 predict(pairs) 接口。"""

    def __init__(
        self,
        model_id: str,
        *,
        device: str | None = None,
        max_length: int = 8192,
        instruction: str | None = None,
    ) -> None:
        import torch
        from transformers import AutoModelForCausalLM, AutoTokenizer

        self._model_id = model_id
        self._max_length = max(512, int(max_length))
        self._instruction = (instruction or DEFAULT_INSTRUCTION).strip() or DEFAULT_INSTRUCTION
        self._lock = threading.Lock()

        if device:
            self._device = device
        elif torch.cuda.is_available():
            self._device = "cuda:0"
        else:
            self._device = "cpu"

        dtype = torch.float16 if RAG_MODEL_TORCH_DTYPE == "float16" else torch.float32
        self._tokenizer = AutoTokenizer.from_pretrained(
            model_id,
            padding_side="left",
            trust_remote_code=True,
        )
        if self._tokenizer.pad_token is None and self._tokenizer.eos_token is not None:
            self._tokenizer.pad_token = self._tokenizer.eos_token

        self._model = AutoModelForCausalLM.from_pretrained(
            model_id,
            torch_dtype=dtype,
            trust_remote_code=True,
        )
        self._model.to(self._device)
        self._model.eval()

        self._token_true_id = self._tokenizer.convert_tokens_to_ids("yes")
        self._token_false_id = self._tokenizer.convert_tokens_to_ids("no")

        logger.info(
            "Qwen3Reranker loaded model=%s device=%s max_length=%s torch_dtype=%s",
            model_id,
            self._device,
            self._max_length,
            RAG_MODEL_TORCH_DTYPE,
        )

    @property
    def device(self) -> str:
        return self._device

    def predict(
        self,
        pairs: Sequence[Sequence[str]],
        batch_size: int = 16,
        **kwargs: Any,
    ) -> list[float]:
        del kwargs
        out: list[float] = []
        batch_size = max(1, int(batch_size))
        pair_list = [list(p) for p in pairs]
        for start in range(0, len(pair_list), batch_size):
            chunk = pair_list[start : start + batch_size]
            out.extend(self._score_batch(chunk))
        return out

    def _encode_pair(self, query: str, document: str) -> list[int]:
        user_content = format_qwen3_rerank_input(self._instruction, query, document)
        messages = [
            {"role": "system", "content": _SYSTEM_PROMPT},
            {"role": "user", "content": user_content},
        ]
        template_kwargs: dict[str, Any] = {
            "tokenize": True,
            "add_generation_prompt": True,
        }
        try:
            template_kwargs["enable_thinking"] = False
            ids = self._tokenizer.apply_chat_template(messages, **template_kwargs)
        except TypeError:
            template_kwargs.pop("enable_thinking", None)
            ids = self._tokenizer.apply_chat_template(messages, **template_kwargs)
        if not isinstance(ids, list):
            ids = list(ids)
        if len(ids) > self._max_length:
            ids = ids[: self._max_length]
        return ids

    def _score_batch(self, pairs: list[list[str]]) -> list[float]:
        import torch

        input_ids = [self._encode_pair(str(q or ""), str(d or "")) for q, d in pairs]
        input_ids = [ids for ids in input_ids if ids]
        if not input_ids:
            return [0.0] * len(pairs)

        inputs = self._tokenizer.pad(
            {"input_ids": input_ids},
            padding=True,
            return_tensors="pt",
            max_length=self._max_length,
        )
        inputs = {k: v.to(self._device) for k, v in inputs.items()}

        with self._lock:
            with torch.no_grad():
                logits = self._model(**inputs).logits[:, -1, :]
                true_v = logits[:, self._token_true_id]
                false_v = logits[:, self._token_false_id]
                stacked = torch.stack([false_v, true_v], dim=1)
                probs = torch.nn.functional.log_softmax(stacked, dim=1)[:, 1].exp()
        return probs.detach().cpu().tolist()
