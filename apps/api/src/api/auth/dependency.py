"""Admin auth dependencies.

require_coordinator gates every /admin route at the router level. require_admin
depends on it, so a bad or missing token is a 401 before the role check's 403.
"""

from __future__ import annotations

import logging
import uuid
from typing import Annotated

import jwt
from fastapi import Depends, HTTPException, Request

from api.auth.models import ADMIN_ROLE, STAFF_ROLES, Coordinator, role_of
from api.core.app_state import AppState, get_app_state

logger = logging.getLogger(__name__)

_BEARER_PREFIX = "Bearer "


async def require_coordinator(
    request: Request,
    state: Annotated[AppState, Depends(get_app_state)],
) -> Coordinator:
    auth = request.headers.get("Authorization")
    if not auth or not auth.startswith(_BEARER_PREFIX):
        raise HTTPException(status_code=401, detail="missing bearer token")
    token = auth[len(_BEARER_PREFIX) :].strip()
    if not token:
        raise HTTPException(status_code=401, detail="empty bearer token")

    try:
        claims = state.jwt_verifier.verify(token)
    except jwt.PyJWTError as exc:
        # Log the exception type only; tokens stay out of logs.
        logger.warning("admin auth rejected: %s", type(exc).__name__)
        raise HTTPException(status_code=401, detail="invalid token") from exc

    role = role_of(claims)
    if role is None or role not in STAFF_ROLES:
        raise HTTPException(status_code=403, detail="not a coordinator")

    sub = claims.get("sub")
    if not isinstance(sub, str):
        raise HTTPException(status_code=401, detail="missing subject")
    try:
        user_id = uuid.UUID(sub)
    except ValueError as exc:
        raise HTTPException(status_code=401, detail="subject is not a uuid") from exc

    email = claims.get("email")
    coordinator = Coordinator(
        id=user_id,
        email=email if isinstance(email, str) else "",
        role=role,
    )
    # Read by caller_key in api.core.rate_limit to key limits per coordinator.
    request.state.coordinator = coordinator
    return coordinator


async def require_admin(
    coordinator: Annotated[Coordinator, Depends(require_coordinator)],
) -> Coordinator:
    if coordinator.role != ADMIN_ROLE:
        raise HTTPException(status_code=403, detail="admin role required")
    return coordinator
