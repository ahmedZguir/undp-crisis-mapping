"""Coordinator login, refresh-cookie rotation, and logout (unauthenticated routes)."""

from __future__ import annotations

import logging
import uuid
from typing import Annotated, Any, cast

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import JSONResponse

from api.auth.models import (
    STAFF_ROLES,
    FamilyRevokedError,
    ReuseDetectedError,
    TokenNotFoundError,
    role_of,
)
from api.auth.supabase import SupabaseAuthError
from api.core.app_state import AppState, get_app_state
from api.core.config import Settings, get_settings
from api.core.rate_limit import limit
from api.schemas.auth import LoginRequest, LoginResponse

logger = logging.getLogger(__name__)

# Matches Supabase Auth's access-token lifetime.
ACCESS_TOKEN_TTL_SECONDS = 15 * 60
REFRESH_COOKIE_MAX_AGE = 7 * 24 * 3600

router = APIRouter(prefix="/auth", tags=["auth"])


_VALID_SAMESITE = frozenset({"lax", "strict", "none"})


def _samesite_value(settings: Settings) -> str:
    """Fail loudly on a bad value; the browser would otherwise drop the cookie silently."""
    raw = (settings.auth_refresh_cookie_samesite or "lax").strip().lower()
    if raw not in _VALID_SAMESITE:
        raise RuntimeError(
            f"AUTH_REFRESH_COOKIE_SAMESITE must be one of "
            f"{sorted(_VALID_SAMESITE)}; got {settings.auth_refresh_cookie_samesite!r}"
        )
    return raw


def _set_refresh_cookie(
    response: Response,
    *,
    value: str,
    settings: Settings,
) -> None:
    samesite = _samesite_value(settings)
    # Browsers reject SameSite=None cookies that are not also Secure.
    secure = settings.auth_refresh_cookie_secure or samesite == "none"
    # set_cookie types samesite as a Literal; the value is validated above.
    response.set_cookie(
        key=settings.auth_refresh_cookie_name,
        value=value,
        httponly=True,
        secure=secure,
        samesite=samesite,  # pyright: ignore[reportArgumentType]
        max_age=REFRESH_COOKIE_MAX_AGE,
        path=settings.auth_refresh_cookie_path,
    )


def _clear_refresh_cookie(response: Response, *, settings: Settings) -> None:
    response.delete_cookie(
        key=settings.auth_refresh_cookie_name, path=settings.auth_refresh_cookie_path
    )


def _unauthorized_clearing_cookie(detail: str, settings: Settings) -> JSONResponse:
    """Return a 401 that clears the refresh cookie.

    Returned directly because HTTPException would drop the Set-Cookie header.
    """
    logger.warning("auth refresh 401: %s", detail)
    response = JSONResponse(status_code=401, content={"detail": detail})
    _clear_refresh_cookie(response, settings=settings)
    return response


@router.post("/login")
@limit("10/minute")
async def login(
    request: Request,
    payload: LoginRequest,
    state: Annotated[AppState, Depends(get_app_state)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> Response:
    """Exchange email and password for an access token plus refresh cookie."""
    # slowapi reads the caller key from request. This IP limit complements
    # Supabase Auth's per-email lockout.
    try:
        sb = await state.supabase_auth_client.password_login(payload.email, payload.password)
    except SupabaseAuthError as exc:
        # Supabase returns 400 for invalid credentials.
        logger.warning("login rejected: supabase status=%s", exc.status_code)
        raise HTTPException(status_code=401, detail="invalid credentials") from exc

    user_raw: Any = sb.get("user")
    user: dict[str, Any] = cast(dict[str, Any], user_raw) if isinstance(user_raw, dict) else {}
    role = role_of(user)
    if role not in STAFF_ROLES:
        # Email stays out of logs.
        logger.warning("login rejected: role=%r not coordinator", role)
        raise HTTPException(status_code=403, detail="not a coordinator")

    user_id: Any = user.get("id")
    if not isinstance(user_id, str):
        raise HTTPException(status_code=502, detail="supabase response missing user id")

    try:
        uid = uuid.UUID(user_id)
    except ValueError as exc:
        raise HTTPException(status_code=502, detail="supabase user id is not a uuid") from exc

    access_token = sb.get("access_token")
    if not isinstance(access_token, str):
        raise HTTPException(status_code=502, detail="supabase response missing access_token")

    # Kept server-side on the jti for /auth/refresh. If absent, refresh 401s and
    # the user logs in again.
    sb_refresh_raw: Any = sb.get("refresh_token")
    sb_refresh = sb_refresh_raw if isinstance(sb_refresh_raw, str) else None

    refresh_jti, _ = await state.refresh_store.issue(uid, supabase_refresh=sb_refresh)
    body = LoginResponse(access_token=access_token, expires_in=ACCESS_TOKEN_TTL_SECONDS)
    out = JSONResponse(status_code=200, content=body.model_dump())
    _set_refresh_cookie(out, value=refresh_jti, settings=settings)
    return out


@router.post("/refresh")
async def refresh(
    request: Request,
    state: Annotated[AppState, Depends(get_app_state)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> Response:
    """Rotate the refresh cookie and return a fresh access token.

    Every failure is a 401 that clears the cookie so the browser stops resending it.
    """
    jti = request.cookies.get(settings.auth_refresh_cookie_name)
    if not jti:
        # Origin and cookie names (not values) distinguish a cookie the browser
        # withheld from one scoped to the wrong path or domain.
        origin = request.headers.get("origin")
        cookie_names = sorted(request.cookies.keys())
        logger.warning(
            "refresh 401: missing refresh cookie (origin=%s, cookies=%s)",
            origin,
            cookie_names,
        )
        return _unauthorized_clearing_cookie("missing refresh cookie", settings)

    try:
        new_jti, _, _user_id, prior_sb_refresh = await state.refresh_store.rotate(jti)
    except TokenNotFoundError:
        logger.info("refresh rejected: unknown jti")
        return _unauthorized_clearing_cookie("refresh token not found", settings)
    except FamilyRevokedError:
        logger.warning("refresh rejected: family revoked")
        return _unauthorized_clearing_cookie("refresh family revoked", settings)
    except ReuseDetectedError:
        logger.warning("refresh-token reuse detected; family revoked")
        return _unauthorized_clearing_cookie("refresh token reuse", settings)

    if prior_sb_refresh is None:
        # Login did not return a Supabase refresh token; send the client back to login.
        logger.info("refresh rejected: no supabase refresh bound to jti")
        return _unauthorized_clearing_cookie("refresh token unavailable", settings)

    try:
        access_token, next_sb_refresh = await state.supabase_auth_client.mint_access_token(
            prior_sb_refresh
        )
    except SupabaseAuthError as exc:
        # Usually an expired or already-rotated Supabase refresh token.
        logger.warning("refresh: supabase refresh-grant rejected: %s", exc.status_code)
        return _unauthorized_clearing_cookie("refresh token rejected upstream", settings)

    # If this fails, the next rotation 401s and the user logs in again.
    try:
        await state.refresh_store.set_supabase_refresh(new_jti, next_sb_refresh)
    except TokenNotFoundError:  # race against TTL eviction
        logger.warning("refresh: new jti vanished before sb-refresh attach")

    body = LoginResponse(access_token=access_token, expires_in=ACCESS_TOKEN_TTL_SECONDS)
    out = JSONResponse(status_code=200, content=body.model_dump())
    _set_refresh_cookie(out, value=new_jti, settings=settings)
    return out


@router.post("/logout", status_code=204)
async def logout(
    request: Request,
    state: Annotated[AppState, Depends(get_app_state)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> Response:
    """Revoke the family for the presented cookie and clear it. Idempotent."""
    response = Response(status_code=204)
    jti = request.cookies.get(settings.auth_refresh_cookie_name)
    if jti:
        fid = await state.refresh_store.family_for(jti)
        if fid is not None:
            await state.refresh_store.revoke_family(fid)
    _clear_refresh_cookie(response, settings=settings)
    return response
