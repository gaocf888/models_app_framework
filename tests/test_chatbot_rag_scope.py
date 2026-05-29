"""chatbot_rag_scope 规则单测。"""

from app.llm.graphs.chatbot_rag_scope import (
    augment_retrieval_query_for_plant_kb,
    resolve_rag_namespace,
)

_NS = "Power_plant_knowledge"


def test_plant_pronoun_locks_namespace():
    r = resolve_rag_namespace(
        "本厂的检修规程有哪些要求？",
        enabled=True,
        plant_kb_namespace=_NS,
    )
    assert r.rag_namespace == _NS
    assert r.rag_scope_reason == "plant_pronoun"
    assert r.query_boost


def test_plant_pronoun_only_also_locks():
    r = resolve_rag_namespace(
        "本厂怎么样",
        enabled=True,
        plant_kb_namespace=_NS,
    )
    assert r.rag_namespace == _NS
    assert r.rag_scope_reason == "plant_pronoun"


def test_extended_plant_pronoun_markers():
    for q in (
        "本公司的安全制度有哪些？",
        "本电厂运行规程怎么规定的？",
        "厂里检修流程是什么？",
        "我们这边设备台账在哪看？",
    ):
        r = resolve_rag_namespace(q, enabled=True, plant_kb_namespace=_NS)
        assert r.rag_namespace == _NS, q
        assert r.rag_scope_reason == "plant_pronoun", q


def test_history_pronoun_continuation():
    hist = [{"role": "user", "content": "我们厂的设备配置是怎样的？"}, {"role": "assistant", "content": "…"}]
    r = resolve_rag_namespace(
        "那工艺参数呢",
        enabled=True,
        plant_kb_namespace=_NS,
        history_messages=hist,
    )
    assert r.rag_namespace == _NS
    assert r.rag_scope_reason == "plant_pronoun_history_continuation"


def test_disabled_returns_none():
    r = resolve_rag_namespace(
        "本厂设备规程",
        enabled=False,
        plant_kb_namespace=_NS,
    )
    assert r.rag_namespace is None
    assert r.rag_scope_reason == "plant_kb_disabled"


def test_augment_query_boost():
    out = augment_retrieval_query_for_plant_kb("检修规程", query_boost="华电五彩湾北一发电有限公司")
    assert "华电五彩湾" in out
    assert augment_retrieval_query_for_plant_kb(out, query_boost="华电五彩湾北一发电有限公司") == out
