from __future__ import annotations

import os
from typing import Any, List

from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine
from sqlalchemy.sql import text

from app.core.config import get_app_config
from app.core.logging import get_logger

logger = get_logger(__name__)

_RETRYABLE_DB_ERROR_MARKERS = (
    "packet sequence number wrong",
    "not connected",
)


def _is_retryable_db_error(exc: BaseException | None) -> bool:
    seen: set[int] = set()
    while exc is not None and id(exc) not in seen:
        seen.add(id(exc))
        if isinstance(exc, UnicodeDecodeError):
            return True
        msg = str(exc).lower()
        if any(marker in msg for marker in _RETRYABLE_DB_ERROR_MARKERS):
            return True
        exc = getattr(exc, "__cause__", None) or getattr(exc, "orig", None)
    return False


class SQLExecutor:
    """
    SQL 执行器（MySQL 版，基于 SQLAlchemy Async）。

    - 使用 `app.core.config.DatabaseConfig` 中的配置创建异步引擎；
    - 当前实现仅支持只读查询（SELECT），与 SQLValidator 保持一致。
    """

    def __init__(self, engine: AsyncEngine | None = None) -> None:
        if engine is not None:
            self._engine = engine
        else:
            db_cfg = getattr(get_app_config(), "db")
            self._engine = create_async_engine(
                db_cfg.url,
                pool_pre_ping=True,
                connect_args={"charset": "utf8mb4"},
            )

    @staticmethod
    def _execute_max_retries() -> int:
        return max(1, int(os.getenv("NL2SQL_EXECUTE_MAX_RETRIES", "2")))

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
                try:
                    await self._engine.dispose()
                except Exception:  # noqa: BLE001
                    logger.warning("SQLExecutor.execute dispose failed after retryable error", exc_info=True)
        if last_exc is not None:
            raise last_exc
        return rows

    async def explain(self, sql: str) -> List[dict[str, Any]]:
        """
        执行前 EXPLAIN，用于提前暴露语法错误、未知列等（与 SELECT 同连接语义）。

        TiDB/MySQL：对无效列名、未知表等，EXPLAIN 通常会像执行 SELECT 一样在解析/优化阶段报错，
        因此可作为「执行前探针」；若方言仅在实际读取数据时才报错，则 EXPLAIN 可能无法覆盖，
        仍以执行错误分支与 refine 为准。
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
