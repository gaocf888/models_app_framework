from __future__ import annotations

import os
from unittest.mock import patch

from app.core.config import _load_from_env, get_app_config


def test_analysis_agent_production_defaults_redis_persistence() -> None:
    get_app_config.cache_clear()
    with patch.dict(
        os.environ,
        {"APP_ENV": "production", "ANALYSIS_AGENT_CHECKPOINT_BACKEND": "", "ANALYSIS_AGENT_SESSION_STORE_BACKEND": ""},
        clear=False,
    ):
        cfg = _load_from_env()
    assert cfg.analysis_agent.checkpoint_backend == "redis"
    assert cfg.analysis_agent.session_store_backend == "redis"
    get_app_config.cache_clear()


def test_analysis_agent_dev_defaults_memory_persistence() -> None:
    get_app_config.cache_clear()
    with patch.dict(
        os.environ,
        {"APP_ENV": "dev"},
        clear=False,
    ):
        cfg = _load_from_env()
    assert cfg.analysis_agent.checkpoint_backend == "memory"
    assert cfg.analysis_agent.session_store_backend == "memory"
    get_app_config.cache_clear()


def test_analysis_agent_plan_template_version_from_env() -> None:
    get_app_config.cache_clear()
    with patch.dict(
        os.environ,
        {"ANALYSIS_AGENT_PLAN_TEMPLATE_VERSION": "analysis_agent_v2"},
        clear=False,
    ):
        cfg = _load_from_env()
    assert cfg.analysis_agent.plan_template_version == "analysis_agent_v2"
    get_app_config.cache_clear()
