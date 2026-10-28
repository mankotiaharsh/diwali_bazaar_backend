import logging
from collections.abc import AsyncIterator

from sqlalchemy import inspect, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from app.config import get_settings

logger = logging.getLogger(__name__)

class Base(DeclarativeBase):
    pass


def _connect_args(url: str) -> dict:
    # Supabase / pgBouncer poolers don't support asyncpg's prepared-statement cache.
    if url.startswith("postgresql+asyncpg"):
        return {"statement_cache_size": 0}
    return {}


_url = get_settings().database_url
engine = create_async_engine(_url, connect_args=_connect_args(_url), pool_pre_ping=True)
SessionLocal = async_sessionmaker(engine, expire_on_commit=False)


async def get_session() -> AsyncIterator[AsyncSession]:
    async with SessionLocal() as session:
        yield session


def _add_missing_columns(sync_conn) -> None:
    """
    Tiny forward-only migration: add columns that exist on the models but not yet in the database
    (as nullable, so existing rows stay valid). Switch to Alembic once changes get more involved.
    """
    inspector = inspect(sync_conn)
    for table in Base.metadata.sorted_tables:
        if not inspector.has_table(table.name):
            continue
        existing = {c["name"] for c in inspector.get_columns(table.name)}
        for column in table.columns:
            if column.name not in existing:
                col_type = column.type.compile(dialect=sync_conn.dialect)
                sync_conn.execute(text(f'ALTER TABLE {table.name} ADD COLUMN "{column.name}" {col_type}'))
                logger.warning("Added missing column %s.%s", table.name, column.name)


async def init_db() -> None:
    """Create tables that don't exist yet and add any new columns."""
    from app import models  # noqa: F401  (registers the tables on Base.metadata)

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await conn.run_sync(_add_missing_columns)
