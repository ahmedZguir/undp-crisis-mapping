"""Auth principal and refresh-store errors (each maps to a 401 in /auth/refresh)."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any, cast

ADMIN_ROLE = "admin"
# Roles allowed into the coordinator console; any other role is not staff.
STAFF_ROLES = frozenset({"coordinator", ADMIN_ROLE})


def role_of(user: dict[str, Any], *, top_level_fallback: bool = True) -> str | None:
    """Read app_metadata.role (server-controlled in Supabase, not user-editable).

    The top-level role claim is a fallback for hand-issued test tokens; turn it
    off for raw auth.users rows, where it holds the Postgres role.
    """
    app_metadata: Any = user.get("app_metadata")
    if isinstance(app_metadata, dict):
        role = cast(dict[str, Any], app_metadata).get("role")
        if isinstance(role, str):
            return role
    if top_level_fallback:
        fallback = user.get("role")
        if isinstance(fallback, str):
            return fallback
    return None


@dataclass(frozen=True, slots=True)
class Coordinator:
    id: uuid.UUID
    email: str
    role: str  # "coordinator" or "admin"


class TokenNotFoundError(Exception):
    """Refresh-token jti is not in Redis (or expired)."""


class FamilyRevokedError(Exception):
    """Token family was revoked by logout or an earlier reuse detection."""


class ReuseDetectedError(Exception):
    """An already-rotated token was replayed; the store revokes the whole family."""
