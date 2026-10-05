"""Staff account management via the Supabase Auth admin API.

Disable and password rotation also revoke the target's refresh-token families,
so live sessions end within one access-token TTL. Mutations require admin and
refuse both the caller's own account and any admin account; admins are demoted
or removed out-of-band.
"""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Request

from api.auth.bootstrap import mask_email
from api.auth.dependency import require_admin, require_coordinator
from api.auth.models import ADMIN_ROLE, STAFF_ROLES, Coordinator, role_of
from api.auth.revocation import revoke_refresh_families_for_user
from api.auth.supabase import SupabaseAuthError
from api.core.app_state import AppState, get_app_state
from api.core.rate_limit import limit
from api.schemas.coordinators import (
    CoordinatorOut,
    CreateCoordinatorRequest,
    DisableRequest,
    DisableResponse,
    RotatePasswordRequest,
    StatusResponse,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/coordinators", tags=["admin"])


def _parse_dt(raw: Any) -> datetime | None:
    if not isinstance(raw, str):
        return None
    try:
        return datetime.fromisoformat(raw)
    except ValueError:
        return None


def _coerce_user_id(raw: Any) -> uuid.UUID | None:
    if not isinstance(raw, str):
        return None
    try:
        return uuid.UUID(raw)
    except ValueError:
        return None


def _to_coordinator_out(
    row: dict[str, Any], *, caller_id: uuid.UUID, now: datetime | None = None
) -> CoordinatorOut | None:
    """None when id or created_at is missing. Disabled means banned_until is in the future."""
    user_id = _coerce_user_id(row.get("id"))
    if user_id is None:
        return None
    email_raw: Any = row.get("email")
    email = email_raw if isinstance(email_raw, str) else ""
    role = role_of(row, top_level_fallback=False) or ""
    created_at = _parse_dt(row.get("created_at"))
    if created_at is None:
        return None
    banned_until = _parse_dt(row.get("banned_until"))
    reference = now or datetime.now(tz=UTC)
    is_disabled = banned_until is not None and banned_until > reference
    return CoordinatorOut(
        id=user_id,
        email=email,
        role=role,
        created_at=created_at,
        banned_until=banned_until,
        is_disabled=is_disabled,
        is_self=user_id == caller_id,
    )


def _reject_self(coordinator: Coordinator, target: uuid.UUID) -> None:
    if coordinator.id == target:
        raise HTTPException(status_code=400, detail="cannot_modify_self")


async def _load_non_admin_target(
    state: AppState, actor: Coordinator, user_id: uuid.UUID
) -> dict[str, Any]:
    """Return the target's Supabase user dict; 403 if it is an admin, 404 if missing."""
    try:
        target = await state.supabase_auth_client.get_user(user_id)
    except SupabaseAuthError as exc:
        if exc.status_code == 404:
            raise HTTPException(status_code=404, detail="coordinator_not_found") from exc
        logger.error(
            "load target: supabase failed actor=%s target=%s status=%d",
            actor.id,
            user_id,
            exc.status_code,
        )
        raise HTTPException(status_code=502, detail="failed to load coordinator") from exc

    if role_of(target, top_level_fallback=False) == ADMIN_ROLE:
        raise HTTPException(status_code=403, detail="admin_immutable")
    return target


@router.get("", response_model=list[CoordinatorOut])
async def list_coordinators(
    coordinator: Annotated[Coordinator, Depends(require_coordinator)],
    state: Annotated[AppState, Depends(get_app_state)],
) -> list[CoordinatorOut]:
    """List staff accounts (both tiers), marking the caller's row with is_self."""
    try:
        users = await state.supabase_auth_client.admin_list_users(per_page=100)
    except SupabaseAuthError as exc:
        logger.error(
            "list coordinators: supabase failed actor=%s status=%d",
            coordinator.id,
            exc.status_code,
        )
        raise HTTPException(status_code=502, detail="failed to list coordinators") from exc

    now = datetime.now(tz=UTC)
    out: list[CoordinatorOut] = []
    for row in users:
        # Any other auth.users role (service accounts, stray rows) is hidden.
        if role_of(row, top_level_fallback=False) not in STAFF_ROLES:
            continue
        item = _to_coordinator_out(row, caller_id=coordinator.id, now=now)
        if item is not None:
            out.append(item)
    return out


@router.post("", response_model=CoordinatorOut, status_code=201)
@limit("10/minute")
async def create_coordinator(
    request: Request,
    payload: CreateCoordinatorRequest,
    coordinator: Annotated[Coordinator, Depends(require_admin)],
    state: Annotated[AppState, Depends(get_app_state)],
) -> CoordinatorOut:
    """Create a staff account; 409 on duplicate email. New admins cannot be removed via the API."""

    email = payload.email
    existing = await state.supabase_auth_client.admin_find_user_by_email(email)
    if existing is not None:
        raise HTTPException(status_code=409, detail="email_exists")

    try:
        created = await state.supabase_auth_client.admin_create_user(
            email=email, password=payload.password, role=payload.role
        )
    except SupabaseAuthError as exc:
        logger.error(
            "create coordinator: supabase failed actor=%s status=%d",
            coordinator.id,
            exc.status_code,
        )
        raise HTTPException(status_code=502, detail="failed to create coordinator") from exc

    result = _to_coordinator_out(created, caller_id=coordinator.id)
    if result is None:
        raise HTTPException(status_code=502, detail="supabase response missing identity fields")

    logger.info(
        "coordinator created actor=%s actor_email=%s target=%s target_email=%s",
        coordinator.id,
        mask_email(coordinator.email) if coordinator.email else "",
        result.id,
        mask_email(email),
    )
    return result


@router.patch("/{user_id}/password", response_model=StatusResponse)
@limit("10/minute")
async def rotate_coordinator_password(
    request: Request,
    user_id: uuid.UUID,
    payload: RotatePasswordRequest,
    coordinator: Annotated[Coordinator, Depends(require_admin)],
    state: Annotated[AppState, Depends(get_app_state)],
) -> StatusResponse:
    """Rotate a coordinator's password and revoke their refresh families.

    Self-rotation is refused because it would sign the caller out everywhere;
    use the CLI for that.
    """
    _reject_self(coordinator, user_id)
    target = await _load_non_admin_target(state, coordinator, user_id)

    try:
        # Re-stamp the target's existing role so rotation never changes its tier.
        await state.supabase_auth_client.admin_update_user_password(
            user_id=user_id,
            password=payload.password,
            role=role_of(target, top_level_fallback=False),
        )
    except SupabaseAuthError as exc:
        logger.error(
            "rotate password: supabase failed actor=%s target=%s status=%d",
            coordinator.id,
            user_id,
            exc.status_code,
        )
        raise HTTPException(status_code=502, detail="failed to rotate password") from exc

    redis_client = state.redis_client
    if redis_client is None:
        raise HTTPException(status_code=500, detail="refresh store unavailable")
    families_revoked = await revoke_refresh_families_for_user(
        redis_client, state.refresh_store, user_id
    )

    logger.info(
        "coordinator password rotated actor=%s actor_email=%s target=%s families_revoked=%d",
        coordinator.id,
        mask_email(coordinator.email) if coordinator.email else "",
        user_id,
        families_revoked,
    )
    return StatusResponse(status="ok")


@router.post("/{user_id}/enable", response_model=StatusResponse)
async def enable_coordinator(
    user_id: uuid.UUID,
    coordinator: Annotated[Coordinator, Depends(require_admin)],
    state: Annotated[AppState, Depends(get_app_state)],
) -> StatusResponse:
    """Un-ban a disabled coordinator. Idempotent."""
    _reject_self(coordinator, user_id)
    await _load_non_admin_target(state, coordinator, user_id)

    try:
        await state.supabase_auth_client.admin_enable_user(user_id)
    except SupabaseAuthError as exc:
        logger.error(
            "enable coordinator: supabase failed actor=%s target=%s status=%d",
            coordinator.id,
            user_id,
            exc.status_code,
        )
        raise HTTPException(status_code=502, detail="failed to enable user") from exc

    logger.info(
        "coordinator enabled actor=%s actor_email=%s target=%s",
        coordinator.id,
        mask_email(coordinator.email) if coordinator.email else "",
        user_id,
    )
    return StatusResponse(status="ok")


@router.post("/{user_id}/disable", response_model=DisableResponse)
async def disable_coordinator(
    user_id: uuid.UUID,
    payload: DisableRequest,
    coordinator: Annotated[Coordinator, Depends(require_admin)],
    state: Annotated[AppState, Depends(get_app_state)],
) -> DisableResponse:
    """Revoke the coordinator's refresh families, then ban the Supabase account."""
    _reject_self(coordinator, user_id)
    await _load_non_admin_target(state, coordinator, user_id)

    # Revoke first so sessions are cut even if the ban call fails.
    redis_client = state.redis_client
    if redis_client is None:
        raise HTTPException(status_code=500, detail="refresh store unavailable")
    families_revoked = await revoke_refresh_families_for_user(
        redis_client, state.refresh_store, user_id
    )

    try:
        await state.supabase_auth_client.admin_disable_user(user_id)
    except SupabaseAuthError as exc:
        logger.error(
            "coordinator disable: supabase disable failed actor=%s target=%s status=%d",
            coordinator.id,
            user_id,
            exc.status_code,
        )
        raise HTTPException(status_code=502, detail="failed to disable user") from exc

    disabled_at = datetime.now(tz=UTC)

    # One structured audit line per disable.
    logger.info(
        "coordinator disabled actor=%s actor_email=%s target=%s reason=%s "
        "families_revoked=%d disabled_at=%s",
        coordinator.id,
        mask_email(coordinator.email) if coordinator.email else "",
        user_id,
        payload.reason or "",
        families_revoked,
        disabled_at.isoformat(),
    )

    return DisableResponse(user_id=user_id, disabled_at=disabled_at)
