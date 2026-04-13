from __future__ import annotations

from typing import Any, List

from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, create_async_engine
from sqlalchemy.sql import text

from app.core.config import get_app_config
from app.core.logging import get_logger

logger = get_logger(__name__)


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
            self._engine = create_async_engine(db_cfg.url, pool_pre_ping=True)

    async def execute(self, sql: str) -> List[dict[str, Any]]:
        s = (sql or "").strip()
        preview = s[:240] + ("..." if len(s) > 240 else "")
        logger.info("SQLExecutor.execute start sql_len=%d preview=%r", len(s), preview)
        rows: List[dict[str, Any]] = []
        try:
            async with self._engine.begin() as conn:
                result = await conn.execute(text(sql))
                cols = result.keys()
                for r in result.fetchall():
                    rows.append({col: value for col, value in zip(cols, r)})
        except Exception:
            logger.warning(
                "SQLExecutor.execute failed sql_len=%d preview=%r",
                len(s),
                preview,
                exc_info=True,
            )
            raise
        logger.info("SQLExecutor.execute done row_count=%d", len(rows))
        return rows

