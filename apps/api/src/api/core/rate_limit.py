"""slowapi limiter, kept apart from api.main so route modules can import it.

Keys, most specific first: coordinator id, citizen client id (X-Client-Id
header or client_id cookie), then remote IP.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, TypeVar

from fastapi import Request
from fastapi.responses import JSONResponse
from slowapi import Limiter
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address

from api.core.config import get_settings


def caller_key(request: Request) -> str:
    # Set by require_coordinator.
    coordinator: Any = getattr(request.state, "coordinator", None)
    coord_id = getattr(coordinator, "id", None)
    if coord_id is not None:
        return f"coord:{coord_id}"

    client_id = request.headers.get("X-Client-Id") or request.cookies.get("client_id")
    if client_id:
        return f"client:{client_id}"

    # get_remote_address honours X-Forwarded-For.
    return f"ip:{get_remote_address(request)}"


_settings = get_settings()

limiter = Limiter(
    key_func=caller_key,
    storage_uri=_settings.redis_dsn,
    # One INCR + EXPIRE per request; the window-boundary burst is acceptable.
    strategy="fixed-window",
)


_F = TypeVar("_F", bound=Callable[..., Any])


def ip_key(request: Request) -> str:
    """Key for destructive routes such as POST /reports/delete, where client-id
    keying would give an attacker one fresh bucket per client_id they hold."""
    return f"ip:{get_remote_address(request)}"


def limit(spec: str, key_func: Callable[[Request], str] | None = None) -> Callable[[_F], _F]:
    """Typed limiter.limit; spec is a slowapi string such as "5/minute;30/hour".

    key_func overrides the default caller_key.
    """

    def _apply(func: _F) -> _F:
        decorator = limiter.limit(spec, key_func=key_func)  # pyright: ignore[reportUnknownMemberType, reportUnknownVariableType]
        wrapped: Any = decorator(func)  # pyright: ignore[reportUnknownVariableType]
        return wrapped  # pyright: ignore[reportUnknownVariableType]

    return _apply


def rate_limited_response(request: Request, exc: Exception) -> JSONResponse:
    """429 with Retry-After; falls back to 60 s if slowapi omits retry_after."""
    retry_after_raw = getattr(exc, "retry_after", None)
    try:
        retry_after = int(retry_after_raw) if retry_after_raw is not None else 60
    except (TypeError, ValueError):
        retry_after = 60
    if not isinstance(exc, RateLimitExceeded):
        retry_after = 60
    return JSONResponse(
        status_code=429,
        content={"detail": "rate_limited", "retry_after": retry_after},
        headers={"Retry-After": str(retry_after)},
    )
