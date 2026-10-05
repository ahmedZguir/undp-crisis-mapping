"""Shapes for /admin/coordinators."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


class CoordinatorOut(BaseModel):
    """Allowlist of Supabase Auth user fields safe to return; no credential material."""

    id: uuid.UUID
    email: str
    role: str
    created_at: datetime
    banned_until: datetime | None
    is_disabled: bool
    is_self: bool


class CreateCoordinatorRequest(BaseModel):
    # Plain str avoids the email-validator dependency; Supabase validates the address.
    email: str = Field(min_length=3, max_length=320)
    # Matches Supabase Auth's default minimum_password_length.
    password: str = Field(min_length=8, max_length=200)
    # Defaults to the lower tier so an omitted field never creates an admin.
    role: Literal["coordinator", "admin"] = "coordinator"


class RotatePasswordRequest(BaseModel):
    password: str = Field(min_length=8, max_length=200)


class DisableRequest(BaseModel):
    reason: str | None = Field(
        default=None,
        max_length=500,
        description="Optional free-text reason. Logged to stdout for the audit trail.",
    )


class DisableResponse(BaseModel):
    user_id: uuid.UUID
    disabled_at: datetime


class StatusResponse(BaseModel):
    status: str
