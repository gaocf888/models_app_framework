from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List

from sqlalchemy import MetaData, inspect
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from app.core.config import get_app_config
from app.core.logging import get_logger

logger = get_logger(__name__)


@dataclass
class TableColumn:
    name: str
    type: str
    comment: str | None = None


@dataclass
class TableSchema:
    name: str
    columns: List[TableColumn]
    comment: str | None = None


class SchemaMetadataService:
    """
    NL2SQL 数据库 Schema 元数据服务。

    当前实现：
    - 内存中维护 TableSchema 映射；
    - 提供从数据库动态刷新 Schema 的能力；
    - 保留一套示例 Schema 便于在无数据库时本地调试。
    """

    def __init__(self) -> None:
        self._tables: Dict[str, TableSchema] = {}
        self._engine: AsyncEngine | None = None
        # 示例 Schema，可用于端到端验证 NL2SQL 流程。
        self._load_demo_schema()

    def list_tables(self) -> List[TableSchema]:
        return list(self._tables.values())

    def add_table(self, table: TableSchema) -> None:
        self._tables[table.name] = table

    def _load_demo_schema(self) -> None:
        """
        加载一套用于演示的内存 Schema。

        建议在 MySQL 数据库 `aishare` 中创建类似结构的表：

        CREATE TABLE orders (
          id BIGINT PRIMARY KEY AUTO_INCREMENT,
          user_id BIGINT,
          amount DECIMAL(10,2),
          created_at DATETIME
        );
        """
        orders = TableSchema(
            name="orders",
            comment="用户订单表（示例）",
            columns=[
                TableColumn(name="id", type="BIGINT", comment="主键"),
                TableColumn(name="user_id", type="BIGINT", comment="用户 ID"),
                TableColumn(name="amount", type="DECIMAL(10,2)", comment="订单金额"),
                TableColumn(name="created_at", type="DATETIME", comment="创建时间"),
            ],
        )
        self.add_table(orders)

    async def refresh_from_db(self) -> None:
        """
        从实际数据库加载 Schema 信息（异步）。

        说明：
        - 使用 app.core.config.DatabaseConfig.url 连接数据库；
        - 依赖 SQLAlchemy 的元数据反射功能；
        - 当前实现读取所有表名及其列名/类型，列注释受驱动和数据库支持情况限制。
        """
        db_cfg = getattr(get_app_config(), "db")
        if not self._engine:
            self._engine = create_async_engine(db_cfg.url, pool_pre_ping=True)

        async with self._engine.begin() as conn:
            metadata = MetaData()

            def _reflect(sync_conn):
                metadata.reflect(bind=sync_conn)

            await conn.run_sync(_reflect)

            self._tables.clear()
            for table_name, table in metadata.tables.items():
                cols: List[TableColumn] = []
                for col in table.columns:
                    cols.append(
                        TableColumn(
                            name=col.name,
                            type=str(col.type),
                            comment=None,  # SQLAlchemy 对列注释的支持依驱动而定，此处先置空
                        )
                    )
                self.add_table(TableSchema(name=table_name, columns=cols, comment=None))

        logger.info("schema metadata refreshed from database, tables=%s", list(self._tables.keys()))

