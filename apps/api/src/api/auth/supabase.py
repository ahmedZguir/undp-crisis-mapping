"""Thin proxy to Supabase Auth's token and admin-user endpoints. Passwords are never stored."""

from __future__ import annotations

import logging
import uuid
from typing import Any, Protocol, cast

import httpx

from api.core.config import Settings

logger = logging.getLogger(__name__)


class SupabaseAuthClientLike(Protocol):
    async def password_login(self, email: str, password: str) -> dict[str, Any]: ...
    async def mint_access_token(self, refresh_token: str) -> tuple[str, str]: ...
    async def get_user(self, user_id: uuid.UUID) -> dict[str, Any]: ...
    async def aclose(self) -> None: ...
    # Admin-lifecycle verbs
    async def admin_create_user(
        self,
        *,
        email: str,
        password: str,
        role: str = "admin",
    ) -> dict[str, Any]: ...
    async def admin_update_user_password(
        self,
        *,
        user_id: uuid.UUID,
        password: str,
        role: str | None = None,
    ) -> dict[str, Any]: ...
    async def admin_find_user_by_email(self, email: str) -> dict[str, Any] | None: ...
    async def admin_verify_password(self, email: str, password: str) -> bool: ...
    async def admin_disable_user(self, user_id: uuid.UUID) -> None: ...
    async def admin_enable_user(self, user_id: uuid.UUID) -> None: ...
    async def admin_list_users(self, *, per_page: int = 100) -> list[dict[str, Any]]: ...


class SupabaseAuthError(RuntimeError):
    """status_code is Supabase's HTTP status; detail is the response body."""

    def __init__(self, status_code: int, detail: str) -> None:
        super().__init__(f"supabase auth error ({status_code}): {detail}")
        self.status_code = status_code
        self.detail = detail


class SupabaseAuthClient:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        # Server-to-server, so the internal URL, not supabase_public_url.
        self._client = httpx.AsyncClient(
            base_url=settings.supabase_url,
            timeout=httpx.Timeout(10.0),
            headers={
                "apikey": settings.supabase_service_role_key,
                # Admin endpoints want the service-role key as a bearer, the token
                # endpoint as apikey; one client serves both.
                "Authorization": f"Bearer {settings.supabase_service_role_key}",
            },
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    async def password_login(self, email: str, password: str) -> dict[str, Any]:
        """Return Supabase's token response; the caller does the role check."""
        response = await self._client.post(
            "/auth/v1/token",
            params={"grant_type": "password"},
            json={"email": email, "password": password},
        )
        if response.status_code != 200:
            # 400 means invalid credentials; the route maps it to 401.
            raise SupabaseAuthError(response.status_code, response.text)
        body: dict[str, Any] = response.json()
        return body

    async def get_user(self, user_id: uuid.UUID) -> dict[str, Any]:
        response = await self._client.get(f"/auth/v1/admin/users/{user_id}")
        if response.status_code != 200:
            raise SupabaseAuthError(response.status_code, response.text)
        body: dict[str, Any] = response.json()
        return body

    async def mint_access_token(self, refresh_token: str) -> tuple[str, str]:
        """Return (access_token, next_refresh_token).

        Supabase rotates its refresh token on every exchange, so the caller must persist
        the new one.

        """
        response = await self._client.post(
            "/auth/v1/token",
            params={"grant_type": "refresh_token"},
            json={"refresh_token": refresh_token},
        )
        if response.status_code != 200:
            raise SupabaseAuthError(response.status_code, response.text)
        body: dict[str, Any] = response.json()
        token = body.get("access_token")
        next_refresh = body.get("refresh_token")
        if not isinstance(token, str):
            raise SupabaseAuthError(
                response.status_code, f"missing access_token in response: {body!r}"
            )
        if not isinstance(next_refresh, str):
            raise SupabaseAuthError(
                response.status_code, f"missing refresh_token in response: {body!r}"
            )
        return token, next_refresh

    # Admin-lifecycle verbs (/auth/v1/admin/users)

    async def admin_create_user(
        self,
        *,
        email: str,
        password: str,
        role: str = "admin",
    ) -> dict[str, Any]:
        """Create an email-confirmed user. Supabase returns 422 if the email exists."""
        response = await self._client.post(
            "/auth/v1/admin/users",
            json={
                "email": email,
                "password": password,
                "email_confirm": True,
                "app_metadata": {"role": role},
            },
        )
        if response.status_code not in (200, 201):
            raise SupabaseAuthError(response.status_code, response.text)
        body: dict[str, Any] = response.json()
        return body

    async def admin_update_user_password(
        self,
        *,
        user_id: uuid.UUID,
        password: str,
        role: str | None = None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {"password": password}
        if role is not None:
            payload["app_metadata"] = {"role": role}
        response = await self._client.put(
            f"/auth/v1/admin/users/{user_id}",
            json=payload,
        )
        if response.status_code not in (200, 201):
            raise SupabaseAuthError(response.status_code, response.text)
        body: dict[str, Any] = response.json()
        return body

    async def admin_find_user_by_email(self, email: str) -> dict[str, Any] | None:
        """GoTrue's filter is a substring match, so exact (case-insensitive) match here."""
        response = await self._client.get(
            "/auth/v1/admin/users",
            params={"filter": email, "per_page": 50},
        )
        if response.status_code != 200:
            raise SupabaseAuthError(response.status_code, response.text)
        body: dict[str, Any] = response.json()
        users_raw: Any = body.get("users")
        if not isinstance(users_raw, list):
            return None
        users_list: list[Any] = cast(list[Any], users_raw)
        target = email.casefold()
        for row in users_list:
            if not isinstance(row, dict):
                continue
            row_typed = cast(dict[str, Any], row)
            row_email: Any = row_typed.get("email")
            if isinstance(row_email, str) and row_email.casefold() == target:
                return row_typed
        return None

    async def admin_verify_password(self, email: str, password: str) -> bool:
        """True iff a password login succeeds; the issued token is discarded."""
        try:
            await self.password_login(email, password)
            return True
        except SupabaseAuthError:
            return False

    async def admin_disable_user(self, user_id: uuid.UUID) -> None:
        """Ban for 100 years; reversible, and the user row is kept."""
        response = await self._client.put(
            f"/auth/v1/admin/users/{user_id}",
            json={"ban_duration": "876000h"},
        )
        if response.status_code not in (200, 201):
            raise SupabaseAuthError(response.status_code, response.text)

    async def admin_enable_user(self, user_id: uuid.UUID) -> None:
        response = await self._client.put(
            f"/auth/v1/admin/users/{user_id}",
            json={"ban_duration": "none"},
        )
        if response.status_code not in (200, 201):
            raise SupabaseAuthError(response.status_code, response.text)

    async def admin_list_users(self, *, per_page: int = 100) -> list[dict[str, Any]]:
        """Return the first page of users only.

        Rows include server-side fields such as encrypted_password; never return them
        to clients unprojected.
        """
        response = await self._client.get(
            "/auth/v1/admin/users",
            params={"per_page": per_page, "page": 1},
        )
        if response.status_code != 200:
            raise SupabaseAuthError(response.status_code, response.text)
        body: dict[str, Any] = response.json()
        users_raw: Any = body.get("users")
        if not isinstance(users_raw, list):
            return []
        users_list: list[Any] = cast(list[Any], users_raw)
        out: list[dict[str, Any]] = []
        for row in users_list:
            if isinstance(row, dict):
                out.append(cast(dict[str, Any], row))
        return out
