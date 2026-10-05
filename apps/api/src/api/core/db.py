from __future__ import annotations

from uuid import uuid4

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)


def _prepared_statement_name() -> str:
    # With statement_cache_size=0 asyncpg would use the unnamed statement, which
    # the next Parse destroys, breaking server-side cursors (session.stream).
    return f"__asyncpg_{uuid4()}__"


def make_engine(database_url: str) -> AsyncEngine:
    return create_async_engine(
        database_url,
        future=True,
        pool_size=10,
        max_overflow=20,
        pool_pre_ping=True,
        pool_recycle=1800,
        connect_args={
            "statement_cache_size": 0,
            "prepared_statement_name_func": _prepared_statement_name,
        },
    )


def make_sessionmaker(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(engine, expire_on_commit=False)
