"""
智能客服：BERT 序列分类意图识别。

与规则后端共用硬规则闸（多模态/空句/短句续问等），其余由**已微调** BERT 输出
kb_qa / data_query / clarify。须使用 AutoModelForSequenceClassification 导出目录；
不支持魔塔/HF 通用预训练 BERT（如 bert-base-chinese）直接替代。模型加载失败时可回退规则层。
"""

from __future__ import annotations

import os
import threading
from typing import Any, Dict, List

from app.core.config import get_app_config
from app.core.logging import get_logger

from .chatbot_intent_rules import (
    IntentRuleResult,
    apply_intent_hard_gates,
    build_intent_context_from_history,
    classify_chatbot_intent_by_rules,
    _has_conceptual,
    _has_data,
)

logger = get_logger(__name__)

_VALID_LABELS = frozenset({"kb_qa", "data_query", "clarify", "hybrid_qa"})
_DEFAULT_ID2LABEL = {0: "kb_qa", 1: "data_query", 2: "clarify", 3: "hybrid_qa"}


class ChatbotIntentBertClassifier:
    """进程内单例 BERT 意图分类器（懒加载）。"""

    _instance: ChatbotIntentBertClassifier | None = None
    _lock = threading.Lock()

    def __init__(self) -> None:
        cfg = get_app_config().chatbot
        self._model_path = (cfg.intent_bert_model_path or "").strip() or None
        self._model_name = (cfg.intent_bert_model_name or "").strip() or None
        self._device = (cfg.intent_bert_device or "cpu").strip()
        self._max_length = max(32, int(cfg.intent_bert_max_length))
        self._fallback_to_rules = bool(cfg.intent_bert_fallback_to_rules)
        self._tokenizer = None
        self._model = None
        self._id2label: dict[int, str] = dict(_DEFAULT_ID2LABEL)
        self._load_error: str | None = None

    @classmethod
    def get_instance(cls) -> ChatbotIntentBertClassifier:
        with cls._lock:
            if cls._instance is None:
                cls._instance = cls()
            return cls._instance

    @classmethod
    def reset_instance_for_tests(cls) -> None:
        with cls._lock:
            cls._instance = None

    def _resolve_load_target(self) -> str | None:
        if self._model_path and os.path.isdir(self._model_path):
            return self._model_path
        if self._model_name:
            return self._model_name
        return None

    def _ensure_loaded(self) -> bool:
        if self._model is not None and self._tokenizer is not None:
            return True
        if self._load_error:
            return False

        target = self._resolve_load_target()
        if not target:
            self._load_error = "no_model_path_or_name"
            logger.warning(
                "ChatbotIntentBert: model not configured (set CHATBOT_INTENT_BERT_MODEL_PATH or _MODEL_NAME)"
            )
            return False

        try:
            import torch
            from transformers import AutoModelForSequenceClassification, AutoTokenizer

            self._tokenizer = AutoTokenizer.from_pretrained(target)
            self._model = AutoModelForSequenceClassification.from_pretrained(target)
            device = self._device
            if device.startswith("cuda") and not torch.cuda.is_available():
                logger.warning("ChatbotIntentBert: cuda requested but unavailable, using cpu")
                device = "cpu"
            self._model.to(device)
            self._model.eval()
            self._device = device

            raw_id2label = getattr(self._model.config, "id2label", None) or {}
            parsed: dict[int, str] = {}
            for k, v in raw_id2label.items():
                try:
                    parsed[int(k)] = str(v).strip().lower()
                except (TypeError, ValueError):
                    continue
            if parsed:
                self._id2label = parsed
            logger.info(
                "ChatbotIntentBert: loaded model target=%s device=%s labels=%s",
                target,
                self._device,
                self._id2label,
            )
            return True
        except Exception as e:
            self._load_error = str(e)
            logger.exception("ChatbotIntentBert: failed to load model target=%s error=%s", target, e)
            return False

    @staticmethod
    def _build_model_input(query: str, history_summary: str, *, max_history_chars: int = 480) -> str:
        q = (query or "").strip()
        hist = (history_summary or "").strip()
        if not hist:
            return q
        if len(hist) > max_history_chars:
            hist = hist[-max_history_chars:]
        return f"{q} [SEP] {hist}"

    def _predict_label(self, text: str) -> tuple[str, float]:
        import torch

        if not self._ensure_loaded():
            raise RuntimeError(self._load_error or "bert_model_not_loaded")

        assert self._tokenizer is not None
        assert self._model is not None

        encoded = self._tokenizer(
            text,
            truncation=True,
            max_length=self._max_length,
            padding=True,
            return_tensors="pt",
        )
        encoded = {k: v.to(self._device) for k, v in encoded.items()}
        with torch.no_grad():
            logits = self._model(**encoded).logits
            probs = torch.softmax(logits, dim=-1)[0]
            idx = int(torch.argmax(probs).item())
            conf = float(probs[idx].item())

        label = self._id2label.get(idx, "kb_qa")
        if label not in _VALID_LABELS:
            logger.warning("ChatbotIntentBert: unknown label=%s idx=%s, fallback kb_qa", label, idx)
            label = "kb_qa"
            conf = min(conf, 0.55)
        return label, conf

    def classify(
        self,
        query: str,
        *,
        history_summary: str,
        prev_task_type: str,
    ) -> tuple[str, float]:
        text = self._build_model_input(query, history_summary)
        return self._predict_label(text)


def classify_chatbot_intent_by_bert(
    query: str,
    *,
    enable_nl2sql_route: bool,
    image_urls: List[str],
    history_messages: List[Dict[str, Any]] | None = None,
) -> IntentRuleResult:
    """BERT 后端：硬规则闸 + 序列分类；失败时按配置回退规则。"""
    q = (query or "").strip()
    h_sum, prev_task = build_intent_context_from_history(history_messages)

    def _out(label: str, reason: str, conf: float) -> IntentRuleResult:
        return IntentRuleResult(label, reason, conf, h_sum, prev_task)

    gated = apply_intent_hard_gates(
        q,
        enable_nl2sql_route=enable_nl2sql_route,
        image_urls=image_urls,
        history_summary=h_sum,
        prev_task_type=prev_task,
    )
    if gated is not None:
        return gated

    # 三分类旧模型无法产出 hybrid：规则层双信号命中时直接走综合意图
    if enable_nl2sql_route and _has_data(q) and _has_conceptual(q):
        return _out("hybrid_qa", "bert_rules_mixed_hybrid", 0.75)

    classifier = ChatbotIntentBertClassifier.get_instance()
    try:
        label, conf = classifier.classify(q, history_summary=h_sum, prev_task_type=prev_task)
        if not enable_nl2sql_route and label in {"data_query", "hybrid_qa"}:
            return _out("kb_qa", f"bert_nl2sql_disabled|{label}", min(conf, 0.7))
        return _out(label, f"bert_classifier|label={label}", conf)
    except Exception as e:
        cfg = get_app_config().chatbot
        if cfg.intent_bert_fallback_to_rules:
            logger.warning("ChatbotIntentBert: inference failed, fallback to rules error=%s", e)
            ruled = classify_chatbot_intent_by_rules(
                query,
                enable_nl2sql_route=enable_nl2sql_route,
                image_urls=image_urls,
                history_messages=history_messages,
            )
            return IntentRuleResult(
                ruled.intent_label,
                f"bert_fallback_rules|{ruled.intent_reason}",
                min(ruled.intent_confidence, 0.75),
                ruled.history_summary,
                ruled.prev_task_type,
            )
        logger.exception("ChatbotIntentBert: inference failed and fallback disabled error=%s", e)
        return _out("kb_qa", f"bert_error_default_kb_qa|{type(e).__name__}", 0.5)
