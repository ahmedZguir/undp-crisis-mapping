"""Request guards shared by the admin routes."""

from __future__ import annotations

import uuid

from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from api.admin.report_queries import ensure_crisis_exists


async def require_crisis(session: AsyncSession, crisis_id: uuid.UUID) -> None:
    """404 unless the crisis exists, whatever its status."""
    if not await ensure_crisis_exists(session, crisis_id):
        raise HTTPException(status_code=404, detail="crisis not found")
