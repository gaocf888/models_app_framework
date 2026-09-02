from __future__ import annotations

import os

from app.core.config import _load_from_env, get_app_config


def test_data_query_agent_defaults(monkeypatch) -> None:
    get_app_config.cache_clear()
    for k in list(os.environ):
        if k.startswith("DATA_QUERY_AGENT_"):
            monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("APP_ENV", "dev")
    cfg = _load_from_env()
    assert cfg.data_query_agent.enabled is True
    assert cfg.data_query_agent.checkpoint_backend == "redis"
    assert cfg.data_query_agent.session_store_backend == "redis"
    assert cfg.data_query_agent.acquire_max_parallel == 2
    assert cfg.data_query_agent.hud_max_stations == 50
    assert cfg.data_query_agent.hud_max_districts == 16
    assert cfg.data_query_agent.hud_city_enabled is True
    assert cfg.data_query_agent.library_llm_enabled is True
    assert cfg.data_query_agent.hud_layer_enabled is False
    assert cfg.data_query_agent.strategy_block_enabled is False
    assert cfg.data_query_agent.trace_backend == "redis"
    get_app_config.cache_clear()


def test_data_query_agent_can_disable(monkeypatch) -> None:
    get_app_config.cache_clear()
    monkeypatch.setenv("DATA_QUERY_AGENT_ENABLED", "false")
    cfg = _load_from_env()
    assert cfg.data_query_agent.enabled is False
    get_app_config.cache_clear()
