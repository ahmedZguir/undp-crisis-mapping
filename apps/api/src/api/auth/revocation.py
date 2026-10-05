"""Revoke all refresh-token families of a user (password reset, bootstrap rotation, off-boarding).

Scans every token key; acceptable because these lifecycle events are rare.
"""

from __future__ import annotations

import json
import logging
import uuid
from typing import Any, cast

from api.auth.refresh import RefreshTokenStore

logger = logging.getLogger(__name__)

_SCAN_COUNT = 500
_TOKEN_KEY_PATTERN = "auth:refresh:token:*"


async def revoke_refresh_families_for_user(
    redis_client: Any,
    store: RefreshTokenStore,
    user_id: uuid.UUID,
) -> int:
    """Return the number of distinct families revoked."""
    target = str(user_id)
    revoked_families: set[uuid.UUID] = set()

    async for key in redis_client.scan_iter(match=_TOKEN_KEY_PATTERN, count=_SCAN_COUNT):
        raw: Any = await redis_client.get(key)
        if raw is None:
            # Expired between SCAN and GET.
            continue
        try:
            row: Any = json.loads(raw)
        except (TypeError, ValueError):
            logger.warning("refresh token at %s has non-JSON value; skipping", key)
            continue
        if not isinstance(row, dict):
            continue
        row_dict = cast(dict[str, Any], row)
        row_user_id: Any = row_dict.get("user_id")
        if row_user_id != target:
            continue
        family_id_raw: Any = row_dict.get("family_id")
        if not isinstance(family_id_raw, str):
            continue
        try:
            family_id = uuid.UUID(family_id_raw)
        except ValueError:
            continue
        if family_id in revoked_families:
            continue
        await store.revoke_family(family_id)
        revoked_families.add(family_id)

    return len(revoked_families)
