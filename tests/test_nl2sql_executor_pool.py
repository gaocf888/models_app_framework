"""NL2SQL SQLExecutor 连接池 / 可重试错误单元测试（不连真实库）。"""

from __future__ import annotations

import asyncio

import pytest
from sqlalchemy.exc import InterfaceError, OperationalError


def test_is_retryable_db_error_timeout_and_operational() -> None:
    from app.nl2sql.executor import _is_retryable_db_error

    assert _is_retryable_db_error(TimeoutError("timed out"))
    assert _is_retryable_db_error(asyncio.TimeoutError())
    assert _is_retryable_db_error(ConnectionRefusedError("connection refused"))
    assert _is_retryable_db_error(
        OperationalError("statement", {}, Exception("server closed the connection unexpectedly"))
    )
    assert _is_retryable_db_error(InterfaceError("statement", {}, Exception("connection does not exist")))
    assert _is_retryable_db_error(RuntimeError("could not connect to server"))
    # 业务 SQL 语法错误不应重试
    assert not _is_retryable_db_error(RuntimeError("syntax error at or near SELECT"))


def test_create_business_engine_postgres_connect_args(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.nl2sql import executor as ex

    monkeypatch.setenv("NL2SQL_DB_POOL_RECYCLE_SEC", "280")
    monkeypatch.setenv("NL2SQL_DB_CONNECT_TIMEOUT_SEC", "10")
    monkeypatch.setenv("NL2SQL_DB_COMMAND_TIMEOUT_SEC", "120")

    captured: dict = {}

    def _fake_create(url: str, **kwargs):
        captured["url"] = url
        captured["kwargs"] = kwargs

        class _Eng:
            pass

        return _Eng()

    monkeypatch.setattr(ex, "create_async_engine", _fake_create)

    class _Cfg:
        url = "postgresql+asyncpg://u:p@127.0.0.1:5432/db"
        dialect = "postgres"

    eng = ex._create_business_engine(_Cfg())
    assert eng is not None
    assert captured["kwargs"]["pool_pre_ping"] is True
    assert captured["kwargs"]["pool_recycle"] == 280
    assert captured["kwargs"]["connect_args"]["timeout"] == 10
    assert captured["kwargs"]["connect_args"]["command_timeout"] == 120


def test_create_business_engine_mysql_connect_args(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.nl2sql import executor as ex

    monkeypatch.setenv("NL2SQL_DB_CONNECT_TIMEOUT_SEC", "8")
    captured: dict = {}

    def _fake_create(url: str, **kwargs):
        captured["kwargs"] = kwargs

        class _Eng:
            pass

        return _Eng()

    monkeypatch.setattr(ex, "create_async_engine", _fake_create)

    class _Cfg:
        url = "mysql+aiomysql://u:p@127.0.0.1:3306/db"
        dialect = "mysql"

    ex._create_business_engine(_Cfg())
    assert captured["kwargs"]["connect_args"]["charset"] == "utf8mb4"
    assert captured["kwargs"]["connect_args"]["connect_timeout"] == 8
    assert captured["kwargs"]["pool_recycle"] == 280
