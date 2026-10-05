"""Redis-backed refresh-token rotation with family-tracked reuse detection.

Keys:

    auth:refresh:token:<jti>          -> {family_id, user_id, rotated, supabase_refresh}
    auth:refresh:family:<fid>:revoked -> "1"

supabase_refresh is the Supabase refresh token bound to the jti; it never leaves the API.
Presenting an already-rotated token revokes the whole family.
"""

from __future__ import annotations

import json
import secrets
import uuid
from typing import Any

from api.auth.models import FamilyRevokedError, ReuseDetectedError, TokenNotFoundError

REFRESH_TTL_SECONDS = 7 * 24 * 3600


# redis.asyncio.Redis overloads on get/set are too broad for pyright to narrow
# against a small Protocol.
_RedisLike = Any


def _token_key(jti: str) -> str:
    return f"auth:refresh:token:{jti}"


def _family_revoked_key(family_id: uuid.UUID) -> str:
    return f"auth:refresh:family:{family_id}:revoked"


class RefreshTokenStore:
    def __init__(self, client: _RedisLike) -> None:
        self._r: Any = client

    async def issue(
        self,
        user_id: uuid.UUID,
        *,
        family_id: uuid.UUID | None = None,
        supabase_refresh: str | None = None,
    ) -> tuple[str, uuid.UUID]:
        """Mint a token, starting a new family (login) unless family_id is given (rotation)."""
        jti = secrets.token_urlsafe(32)
        fid = family_id or uuid.uuid4()
        payload: dict[str, Any] = {
            "family_id": str(fid),
            "user_id": str(user_id),
            "rotated": False,
        }
        if supabase_refresh is not None:
            payload["supabase_refresh"] = supabase_refresh
        await self._r.set(
            _token_key(jti),
            json.dumps(payload),
            ex=REFRESH_TTL_SECONDS,
        )
        return jti, fid

    async def rotate(self, jti: str) -> tuple[str, uuid.UUID, uuid.UUID, str | None]:
        """Return (new_jti, family_id, user_id, prior_supabase_refresh).

        The caller exchanges prior_supabase_refresh with Supabase and stores the result
        on new_jti via set_supabase_refresh. Raises TokenNotFoundError, FamilyRevokedError, or
        ReuseDetectedError.
        """
        raw = await self._r.get(_token_key(jti))
        if raw is None:
            raise TokenNotFoundError
        row = json.loads(raw)
        fid = uuid.UUID(row["family_id"])
        user_id = uuid.UUID(row["user_id"])
        prior_sb_refresh: str | None = row.get("supabase_refresh")

        if await self._r.get(_family_revoked_key(fid)) is not None:
            # Re-revoke to refresh the flag's TTL.
            await self.revoke_family(fid)
            raise FamilyRevokedError

        if row["rotated"]:
            await self.revoke_family(fid)
            raise ReuseDetectedError

        row["rotated"] = True
        await self._r.set(_token_key(jti), json.dumps(row), ex=REFRESH_TTL_SECONDS)
        new_jti, _ = await self.issue(user_id, family_id=fid)
        return new_jti, fid, user_id, prior_sb_refresh

    async def set_supabase_refresh(self, jti: str, supabase_refresh: str) -> None:
        raw = await self._r.get(_token_key(jti))
        if raw is None:
            raise TokenNotFoundError
        row = json.loads(raw)
        row["supabase_refresh"] = supabase_refresh
        await self._r.set(_token_key(jti), json.dumps(row), ex=REFRESH_TTL_SECONDS)

    async def revoke_family(self, family_id: uuid.UUID) -> None:
        await self._r.set(_family_revoked_key(family_id), "1", ex=REFRESH_TTL_SECONDS)

    async def family_for(self, jti: str) -> uuid.UUID | None:
        raw = await self._r.get(_token_key(jti))
        if raw is None:
            return None
        return uuid.UUID(json.loads(raw)["family_id"])
