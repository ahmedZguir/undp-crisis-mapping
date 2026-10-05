"""Saved search views: a stored filter payload re-run against live data on open."""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, Field

from api.schemas.admin_search import SearchRequest


class SavedViewCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    filter: SearchRequest


class SavedViewUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    filter: SearchRequest | None = None


class SavedViewResponse(BaseModel):
    id: uuid.UUID
    crisis_id: uuid.UUID
    name: str
    filter: SearchRequest
    filter_signature: str
    created_at: datetime
    updated_at: datetime
