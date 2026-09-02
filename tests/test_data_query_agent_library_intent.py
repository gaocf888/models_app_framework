"""数据查询智能体意图 1。"""

from __future__ import annotations

import pytest

from app.data_query_agent.catalog import clear_library_catalog_cache, get_library_catalog
from app.data_query_agent.library_intent import resolve_library_intent


def setup_function() -> None:
    clear_library_catalog_cache()


def teardown_function() -> None:
    clear_library_catalog_cache()


def test_path1_request_library_id_fcb() -> None:
    cat = get_library_catalog()
    r = resolve_library_intent("朝阳区年沉降比较大的监测点", "fcb", catalog=cat)
    assert r.ok
    assert r.library is not None
    assert r.library.id == "fcb"
    assert r.source == "request"
    assert r.library.table == "t_data_wash_fcb"


def test_path2_layered_station_phrase() -> None:
    r = resolve_library_intent("大兴区有哪些分层监测点？", None)
    assert r.ok
    assert r.library is not None
    assert r.library.id == "fcb"
    assert r.source == "parsed"


def test_displacement_maps_gnss() -> None:
    r = resolve_library_intent("查一下位移比较大的点", None)
    assert r.ok
    assert r.library is not None
    assert r.library.id == "gnss"


def test_unresolved_query_data() -> None:
    r = resolve_library_intent("查数据", None)
    assert not r.ok
    assert r.interrupt_reason == "library_unresolved"


def test_ambiguous_fcb_and_gnss() -> None:
    r = resolve_library_intent("对比分层标和 GNSS 的数据", None)
    assert not r.ok
    assert r.interrupt_reason == "library_ambiguous"
    assert "fcb" in r.candidates
    assert "gnss" in r.candidates


def test_invalid_library_id() -> None:
    r = resolve_library_intent("查沉降", "not-a-lib")
    assert not r.ok
    assert r.interrupt_reason == "library_id_invalid"


def test_tree_wins_conflict_warning() -> None:
    r = resolve_library_intent("查 GNSS", "fcb")
    assert r.ok
    assert r.library is not None
    assert r.library.id == "fcb"
    assert r.source == "request"
    assert "library_conflict_nl_ignored" in r.warnings


def test_generic_settle_defaults_fcb() -> None:
    r = resolve_library_intent("朝阳区沉降比较大的监测点", None)
    assert r.ok
    assert r.library is not None
    assert r.library.id == "fcb"
    assert r.source == "default"


@pytest.mark.asyncio
async def test_library_llm_must_be_in_catalog(monkeypatch) -> None:
    from app.core.config import get_app_config
    from app.data_query_agent.catalog import get_library_catalog
    from app.data_query_agent.library_intent_llm import supplement_library_intent_llm

    monkeypatch.setenv("DATA_QUERY_AGENT_LIBRARY_LLM_ENABLED", "false")
    get_app_config.cache_clear()

    class _Fake:
        async def chat(self, **kwargs):
            return '{"library_id":"not_a_real_lib"}'

    cat = get_library_catalog()
    hit = await supplement_library_intent_llm("查一下那个测水位的井", cat, llm_client=_Fake())
    get_app_config.cache_clear()
    assert hit is None


@pytest.mark.asyncio
async def test_library_llm_accepts_catalog_id(monkeypatch) -> None:
    from app.core.config import get_app_config
    from app.data_query_agent.catalog import get_library_catalog
    from app.data_query_agent.library_intent_llm import supplement_library_intent_llm

    monkeypatch.setenv("DATA_QUERY_AGENT_LIBRARY_LLM_ENABLED", "true")
    get_app_config.cache_clear()

    class _Fake:
        async def chat(self, **kwargs):
            return '{"library_id":"dxswj"}'

    cat = get_library_catalog()
    hit = await supplement_library_intent_llm("查一下那个测水位的井", cat, llm_client=_Fake())
    get_app_config.cache_clear()
    assert hit is not None
    assert hit.library is not None
    assert hit.library.id == "dxswj"
    assert hit.source == "llm"


@pytest.mark.asyncio
async def test_library_llm_rejects_hallucination(monkeypatch) -> None:
    from app.core.config import get_app_config
    from app.data_query_agent.catalog import get_library_catalog
    from app.data_query_agent.library_intent_llm import supplement_library_intent_llm

    monkeypatch.setenv("DATA_QUERY_AGENT_LIBRARY_LLM_ENABLED", "true")
    get_app_config.cache_clear()

    class _Fake:
        async def chat(self, **kwargs):
            return '{"library_id":"insar"}'

    cat = get_library_catalog()
    hit = await supplement_library_intent_llm("查 InSAR", cat, llm_client=_Fake())
    get_app_config.cache_clear()
    assert hit is None
