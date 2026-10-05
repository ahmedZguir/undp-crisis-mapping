"""Idempotent boot-time reconcile of the env-var admin against Supabase Auth.

Failures are logged and swallowed: a bad ADMIN_PASSWORD must not keep the
API down, or the operator cannot get in to investigate.
"""

from __future__ import annotations

import logging
import uuid
from enum import StrEnum
from typing import Any

from api.auth.refresh import RefreshTokenStore
from api.auth.revocation import revoke_refresh_families_for_user
from api.auth.supabase import SupabaseAuthClientLike
from api.core.config import Settings

logger = logging.getLogger(__name__)


class BootstrapResult(StrEnum):
    disabled = "disabled"
    created = "created"
    unchanged = "unchanged"
    password_rotated = "password_rotated"
    failed = "failed"


def mask_email(email: str) -> str:
    """Mask an email for logs as j***@example.com, keeping the domain greppable."""
    if "@" not in email:
        return email[:1] + "***"
    local, domain = email.split("@", 1)
    head = local[:1] if local else ""
    return f"{head}***@{domain}"


async def ensure_env_var_admin(
    *,
    settings: Settings,
    supabase_client: SupabaseAuthClientLike,
    redis_client: Any,
    refresh_store: RefreshTokenStore,
) -> BootstrapResult:
    """Create the admin if absent, rotate its password (and revoke sessions) if it differs."""
    email = settings.admin_email
    password = settings.admin_password
    if not email or not password:
        logger.info("env-var admin bootstrap skipped: env vars not set")
        return BootstrapResult.disabled

    masked = mask_email(email)

    try:
        existing = await supabase_client.admin_find_user_by_email(email)
        if existing is None:
            await supabase_client.admin_create_user(email=email, password=password, role="admin")
            logger.info(
                "env-var admin ensured email=%s password_rotated=%s outcome=%s",
                masked,
                False,
                BootstrapResult.created.value,
            )
            return BootstrapResult.created

        user_id_raw: Any = existing.get("id")
        if not isinstance(user_id_raw, str):
            logger.error("env-var admin bootstrap: existing user has no id field; aborting")
            return BootstrapResult.failed
        try:
            user_id = uuid.UUID(user_id_raw)
        except ValueError:
            logger.error("env-var admin bootstrap: existing user id %r is not a uuid", user_id_raw)
            return BootstrapResult.failed

        password_matches = await supabase_client.admin_verify_password(email, password)
        if password_matches:
            logger.info(
                "env-var admin ensured email=%s password_rotated=%s outcome=%s",
                masked,
                False,
                BootstrapResult.unchanged.value,
            )
            return BootstrapResult.unchanged

        await supabase_client.admin_update_user_password(
            user_id=user_id, password=password, role="admin"
        )
        revoked = await revoke_refresh_families_for_user(redis_client, refresh_store, user_id)
        logger.info(
            "env-var admin ensured email=%s password_rotated=%s families_revoked=%d outcome=%s",
            masked,
            True,
            revoked,
            BootstrapResult.password_rotated.value,
        )
        return BootstrapResult.password_rotated
    except Exception:
        # Non-fatal; the log line is the only signal.
        logger.exception("env-var admin bootstrap failed email=%s", masked)
        return BootstrapResult.failed
