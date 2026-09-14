"""chatbot_intent 后端切换与 funnel 漏斗单测。"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

from app.llm.graphs.chatbot_intent import classify_chatbot_intent, resolve_intent_backend
from app.llm.graphs.chatbot_intent_funnel import classify_chatbot_intent_by_funnel
from app.llm.graphs.chatbot_intent_prototypes import (
    clear_intent_prototype_cache,
    load_intent_prototype_texts,
    match_intent_by_prototypes,
)
from app.llm.graphs.chatbot_business_profile import clear_chatbot_business_profile_cache


def test_resolve_intent_backend_defaults_rules():
    with patch("app.llm.graphs.chatbot_intent.get_app_config") as mock_cfg:
        mock_cfg.return_value.chatbot.intent_backend = "rules"
        assert resolve_intent_backend() == "rules"


def test_resolve_intent_backend_funnel_valid():
    with patch("app.llm.graphs.chatbot_intent.get_app_config") as mock_cfg:
        mock_cfg.return_value.chatbot.intent_backend = "funnel"
        assert resolve_intent_backend() == "funnel"


def test_resolve_intent_backend_legacy_llm_falls_back_rules():
    with patch("app.llm.graphs.chatbot_intent.get_app_config") as mock_cfg:
        mock_cfg.return_value.chatbot.intent_backend = "llm"
        assert resolve_intent_backend() == "rules"


def test_resolve_intent_backend_legacy_bert_falls_back_rules():
    with patch("app.llm.graphs.chatbot_intent.get_app_config") as mock_cfg:
        mock_cfg.return_value.chatbot.intent_backend = "bert"
        assert resolve_intent_backend() == "rules"


def test_resolve_intent_backend_invalid_falls_back_rules():
    with patch("app.llm.graphs.chatbot_intent.get_app_config") as mock_cfg:
        mock_cfg.return_value.chatbot.intent_backend = "unknown"
        assert resolve_intent_backend() == "rules"


def test_facade_rules_backend_matches_rules_module():
    clear_chatbot_business_profile_cache()
    with patch.dict("os.environ", {"CHATBOT_DOMAIN": "subsidence"}, clear=False):
        clear_chatbot_business_profile_cache()
        r = classify_chatbot_intent(
            "统计朝阳区年沉降",
            enable_nl2sql_route=True,
            image_urls=[],
            backend="rules",
        )
    assert r.intent_label == "data_query"
    assert "structured" in r.intent_reason


def test_sync_classify_funnel_backend_falls_back_rules():
    r = classify_chatbot_intent(
        "哪些行政区近期沉降相对偏大？",
        enable_nl2sql_route=True,
        image_urls=[],
        backend="funnel",
    )
    # sync 路径不跑 funnel，回退 rules → default_kb_qa
    assert r.intent_label == "kb_qa"
    assert "default_kb_qa" in r.intent_reason


def test_subsidence_prototypes_loaded():
    clear_chatbot_business_profile_cache()
    texts = load_intent_prototype_texts("subsidence")
    assert "data_query" in texts
    assert any("偏大" in t for t in texts["data_query"])


def test_match_intent_by_prototypes_accepts_with_injected_embed():
    clear_intent_prototype_cache()
    # 用简单 bag-of-chars 哈希向量，保证同句相似度高、异类较低
    def embed(text: str) -> list[float]:
        t = (text or "").strip()
        vec = [0.0] * 32
        for i, ch in enumerate(t[:64]):
            vec[i % 32] += (ord(ch) % 17) / 17.0
        # 对 data_query 口语加稳定偏置
        if any(k in t for k in ("偏大", "统计", "查询", "排行", "站点")):
            vec[0] += 5.0
        if any(k in t for k in ("成因", "机理", "差异", "什么是", "规范")):
            vec[1] += 5.0
        return vec

    m = match_intent_by_prototypes(
        "哪些行政区近期沉降相对偏大？",
        domain="subsidence",
        embed_query=embed,
        sim_accept=0.5,
        sim_margin=0.01,
    )
    assert m.accepted
    assert m.label == "data_query"


def test_funnel_l1_short_circuit_structured():
    async def _run():
        r = await classify_chatbot_intent_by_funnel(
            "统计朝阳区年沉降",
            enable_nl2sql_route=True,
            image_urls=[],
        )
        assert r.intent_label == "data_query"
        assert r.intent_reason.startswith("funnel_l1_rules")

    asyncio.run(_run())


def test_funnel_l2_hits_oral_data_query():
    async def _run():
        with patch(
            "app.llm.graphs.chatbot_intent_funnel.match_intent_by_prototypes"
        ) as mock_match:
            mock_match.return_value = MagicMock(
                accepted=True,
                label="data_query",
                reason="l2_accept|data_query|sim=0.88|gap=0.20",
                best_score=0.88,
                second_score=0.68,
            )
            r = await classify_chatbot_intent_by_funnel(
                "哪些行政区近期沉降相对偏大？",
                enable_nl2sql_route=True,
                image_urls=[],
            )
        assert r.intent_label == "data_query"
        assert "funnel_l2_embed" in r.intent_reason

    asyncio.run(_run())


def test_funnel_l3_vllm_when_l2_rejects():
    async def _run():
        with patch(
            "app.llm.graphs.chatbot_intent_funnel.match_intent_by_prototypes"
        ) as mock_match:
            mock_match.return_value = MagicMock(
                accepted=False,
                label="data_query",
                reason="l2_reject|top=data_query|sim=0.40|gap=0.01",
                best_score=0.40,
                second_score=0.39,
            )
            llm = MagicMock()
            llm.chat = AsyncMock(
                return_value='{"intent_label":"data_query","confidence":0.9,"reason":"oral_rank"}'
            )
            with patch("app.llm.graphs.chatbot_intent_funnel.get_app_config") as mock_cfg:
                mock_cfg.return_value.llm.default_model = "default"
                mock_cfg.return_value.chatbot.intent_output_labels = [
                    "kb_qa",
                    "clarify",
                    "data_query",
                    "hybrid_qa",
                ]
                r = await classify_chatbot_intent_by_funnel(
                    "哪些行政区近期沉降相对偏大？",
                    enable_nl2sql_route=True,
                    image_urls=[],
                    llm_client=llm,
                )
        assert r.intent_label == "data_query"
        assert "funnel_l3_vllm" in r.intent_reason
        llm.chat.assert_awaited()

    asyncio.run(_run())


def test_funnel_l3_fail_falls_back_rules():
    async def _run():
        with patch(
            "app.llm.graphs.chatbot_intent_funnel.match_intent_by_prototypes"
        ) as mock_match:
            mock_match.return_value = MagicMock(
                accepted=False,
                label=None,
                reason="l2_reject",
                best_score=0.0,
                second_score=0.0,
            )
            llm = MagicMock()
            llm.chat = AsyncMock(side_effect=RuntimeError("boom"))
            r = await classify_chatbot_intent_by_funnel(
                "哪些行政区近期沉降相对偏大？",
                enable_nl2sql_route=True,
                image_urls=[],
                llm_client=llm,
            )
        assert r.intent_label == "kb_qa"
        assert "funnel_l3_fail_fallback_rules" in r.intent_reason

    asyncio.run(_run())
