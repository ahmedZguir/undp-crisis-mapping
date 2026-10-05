"""Writes to the per-report enrichment sidecar tables.

Table and column names are interpolated into SQL; callers pass only literals
from this package, never user input.
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from api.workers.errors import truncate_error


async def execute_write(
    sessionmaker: async_sessionmaker[AsyncSession], sql: str, params: dict[str, Any]
) -> None:
    """Run one statement in its own transaction."""
    async with sessionmaker() as session, session.begin():
        await session.execute(text(sql), params)


async def mark_skipped(
    sessionmaker: async_sessionmaker[AsyncSession],
    report_id: uuid.UUID,
    *,
    table: str,
    status_col: str = "status",
    error_col: str = "error",
) -> None:
    """Move a still-pending stage to 'skipped'."""
    await execute_write(
        sessionmaker,
        f"update public.{table} "
        f"   set {status_col} = 'skipped', {error_col} = null, updated_at = now() "
        f" where report_id = :id and {status_col} = 'pending'",
        {"id": str(report_id)},
    )


async def mark_failed(
    sessionmaker: async_sessionmaker[AsyncSession],
    report_id: uuid.UUID,
    error: str,
    *,
    table: str,
    status_col: str = "status",
    error_col: str = "error",
) -> None:
    await execute_write(
        sessionmaker,
        f"update public.{table} "
        f"   set {status_col} = 'failed', {error_col} = :error, updated_at = now() "
        f" where report_id = :id",
        {"id": str(report_id), "error": truncate_error(error)},
    )
