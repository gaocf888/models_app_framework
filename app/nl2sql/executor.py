from __future__ import annotations

import asyncio
import os
from typing import Any, List

from sqlalchemy.exc import DBAPIError, DisconnectionError, InterfaceError, OperationalError
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine
from sqlalchemy.sql import text

from app.core.config import get_app_config
from app.core.logging import get_logger

logger = get_logger(__name__)

# 历史 TiDB/MySQL 包序号错乱等 + 连接层超时/断连（闲置后首连假死场景）
_RETRYABLE_DB_ERROR_MARKERS = (
    "packet sequence number wrong",
    "not connected",
    "connection does not exist",
    "connection refused",
    "connection reset",
    "server closed the connection",
    "could not connect",
    "can't connect",
    "cannot connect",
    "timed out",
    "timeout",
    "temporarily unavailable",
    "network is unreachable",
    "name or service not known",
    "temporary failure in name resolution",
    "too many connections",
    "ssl connection has been closed",
    "connection aborted",
)


def _env_positive_int(name: str, default: int) -> int:
    raw = (os.getenv(name) or "").strip()
    if not raw:
        return default
    try:
        return max(1, int(raw))
    except ValueError:
        return default


def _pool_recycle_seconds() -> int:
    """连接池回收周期（秒）；宜小于防火墙/NAT 空闲超时（常见 300～900s）。"""
    return _env_positive_int("NL2SQL_DB_POOL_RECYCLE_SEC", 280)


def _connect_timeout_seconds() -> int:
    """建连超时（秒），避免黑洞连接无限挂起。"""
    return _env_positive_int("NL2SQL_DB_CONNECT_TIMEOUT_SEC", 10)


def _command_timeout_seconds() -> int:
    """单条语句默认超时（秒，主要作用于 asyncpg）。"""
    return _env_positive_int("NL2SQL_DB_COMMAND_TIMEOUT_SEC", 120)


def _is_retryable_db_error(exc: BaseException | None) -> bool:
    seen: set[int] = set()
    cur: BaseException | None = exc
    while cur is not None and id(cur) not in seen:
        seen.add(id(cur))
        if isinstance(cur, UnicodeDecodeError):
            return True
        if isinstance(
            cur,
            (
                TimeoutError,
                asyncio.TimeoutError,
                ConnectionError,
                ConnectionResetError,
                ConnectionRefusedError,
                ConnectionAbortedError,
                BrokenPipeError,
                OperationalError,
                InterfaceError,
                DisconnectionError,
            ),
        ):
            return True
        # 部分驱动把断连包在 DBAPIError 里，文案仍可匹配
        if isinstance(cur, DBAPIError):
            msg = str(cur).lower()
            if any(marker in msg for marker in _RETRYABLE_DB_ERROR_MARKERS):
                return True
        msg = str(cur).lower()
        if any(marker in msg for marker in _RETRYABLE_DB_ERROR_MARKERS):
            return True
        cur = getattr(cur, "__cause__", None) or getattr(cur, "orig", None)
    return False


def _is_postgres_url(url: str, dialect: str | None = None) -> bool:
    d = (dialect or "").strip().lower()
    if d in {"postgres", "postgresql", "pg"}:
        return True
    u = (url or "").lower()
    return u.startswith("postgresql+") or u.startswith("postgres+")


def _create_business_engine(db_cfg: Any) -> AsyncEngine:
    """按业务库方言创建异步引擎（MySQL/TiDB 与 PostgreSQL 连接参数不同）。"""
    url = getattr(db_cfg, "url", "") or ""
    dialect = getattr(db_cfg, "dialect", None)
    connect_timeout = _connect_timeout_seconds()
    recycle = _pool_recycle_seconds()
    kwargs: dict[str, Any] = {
        "pool_pre_ping": True,
        "pool_recycle": recycle,
    }
    if _is_postgres_url(url, dialect):
        # asyncpg：timeout=建连；command_timeout=语句默认超时
        kwargs["connect_args"] = {
            "timeout": connect_timeout,
            "command_timeout": _command_timeout_seconds(),
        }
    else:
        # aiomysql / asyncmy：connect_timeout 单位为秒
        kwargs["connect_args"] = {
            "charset": "utf8mb4",
            "connect_timeout": connect_timeout,
        }
    logger.info(
        "NL2SQL business engine created postgres=%s pool_recycle=%ss connect_timeout=%ss",
        _is_postgres_url(url, dialect),
        recycle,
        connect_timeout,
    )
    return create_async_engine(url, **kwargs)


class SQLExecutor:
    """
    SQL 执行器（SQLAlchemy Async；支持 TiDB/MySQL 与 PostgreSQL）。

    - 使用 `app.core.config.DatabaseConfig` 中的配置创建异步引擎；
    - 当前实现仅支持只读查询（SELECT），与 SQLValidator 保持一致。
    """

    def __init__(self, engine: AsyncEngine | None = None) -> None:
        self._owns_engine = engine is None
        if engine is not None:
            self._engine = engine
        else:
            db_cfg = getattr(get_app_config(), "db")
            self._engine = _create_business_engine(db_cfg)

    @staticmethod
    def _execute_max_retries() -> int:
        return max(1, int(os.getenv("NL2SQL_EXECUTE_MAX_RETRIES", "2")))

    async def _dispose_and_rebuild_engine(self) -> None:
        """丢弃坏连接池；自建引擎则按当前配置重建，避免闲置假死后继续复用死连接。"""
        try:
            await self._engine.dispose()
        except Exception:  # noqa: BLE001
            logger.warning("SQLExecutor dispose failed after retryable error", exc_info=True)
        if self._owns_engine:
            db_cfg = getattr(get_app_config(), "db")
            self._engine = _create_business_engine(db_cfg)
            logger.info("SQLExecutor engine rebuilt after retryable DB error")

    async def execute(self, sql: str) -> List[dict[str, Any]]:
        s = (sql or "").strip()
        preview = s
        logger.info("SQLExecutor.execute start sql_len=%d preview=%r", len(s), preview)
        rows: List[dict[str, Any]] = []
        max_retries = self._execute_max_retries()
        last_exc: BaseException | None = None
        for attempt in range(1, max_retries + 1):
            try:
                async with self._engine.connect() as conn:
                    async with conn.begin():
                        result = await conn.execute(text(sql))
                        cols = result.keys()
                        for r in result.fetchall():
                            rows.append({col: value for col, value in zip(cols, r)})
                logger.info("SQLExecutor.execute done row_count=%d", len(rows))
                return rows
            except Exception as exc:
                last_exc = exc
                retryable = _is_retryable_db_error(exc)
                logger.warning(
                    "SQLExecutor.execute failed sql_len=%d attempt=%d/%d retryable=%s preview=%r",
                    len(s),
                    attempt,
                    max_retries,
                    retryable,
                    preview,
                    exc_info=True,
                )
                if not retryable or attempt >= max_retries:
                    raise
                await self._dispose_and_rebuild_engine()
        if last_exc is not None:
            raise last_exc
        return rows

    async def explain(self, sql: str) -> List[dict[str, Any]]:
        """
        执行前 EXPLAIN，用于提前暴露语法错误、未知列等（与 SELECT 同连接语义）。

        TiDB/MySQL：对无效列名、未知表等，EXPLAIN 通常会像执行 SELECT 一样在解析/优化阶段报错。
        PostgreSQL：同样支持 EXPLAIN，语义略有差异，仍可作为执行前探针。
        """
        s = (sql or "").strip()
        preview = s
        logger.info("SQLExecutor.explain start sql_len=%d preview=%r", len(s), preview)
        rows: List[dict[str, Any]] = []
        explain_stmt = f"EXPLAIN {s}"
        try:
            async with self._engine.begin() as conn:
                result = await conn.execute(text(explain_stmt))
                cols = result.keys()
                for r in result.fetchall():
                    rows.append({col: value for col, value in zip(cols, r)})
        except Exception:
            logger.warning(
                "SQLExecutor.explain failed sql_len=%d preview=%r",
                len(s),
                preview,
                exc_info=True,
            )
            raise
        logger.info("SQLExecutor.explain done rows=%d", len(rows))
        return rows
