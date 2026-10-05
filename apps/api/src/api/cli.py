"""Operator CLI. Needs SUPABASE_SERVICE_ROLE_KEY and the rest of .env.

uv run python -m api.cli admin create
uv run python -m api.cli admin set-password <email>
uv run python -m api.cli embeddings backfill [--crisis-id UUID] [--batch-size N]
"""

from __future__ import annotations

import asyncio
import logging
import sys
import uuid
from typing import Any

import click

from api.auth.bootstrap import mask_email
from api.auth.refresh import RefreshTokenStore
from api.auth.revocation import revoke_refresh_families_for_user
from api.auth.supabase import SupabaseAuthClient, SupabaseAuthClientLike
from api.core.config import Settings, get_settings

# Audit lines are logged at INFO.
logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger("api.cli")


def _build_supabase_client(settings: Settings) -> SupabaseAuthClient:
    return SupabaseAuthClient(settings)


def _build_redis_client(settings: Settings) -> Any:
    import redis.asyncio as redis_asyncio

    return redis_asyncio.from_url(  # pyright: ignore[reportUnknownMemberType]
        settings.redis_dsn, decode_responses=True
    )


async def _rotate_admin_password(
    sb: SupabaseAuthClientLike,
    redis: Any,
    store: RefreshTokenStore,
    existing: dict[str, Any],
    email: str,
    password: str,
    *,
    command: str,
    success_suffix: str,
) -> int:
    """Set an existing admin's password and revoke their refresh tokens. Returns the exit code."""
    user_id_raw: Any = existing.get("id")
    if not isinstance(user_id_raw, str):
        logger.error("%s email=%s: existing user has no id", command, mask_email(email))
        return 2
    try:
        user_id = uuid.UUID(user_id_raw)
    except ValueError:
        logger.error("%s email=%s: existing user id is not a uuid", command, mask_email(email))
        return 2

    await sb.admin_update_user_password(user_id=user_id, password=password, role="admin")
    revoked = await revoke_refresh_families_for_user(redis, store, user_id)
    logger.info(
        "%s email=%s user_id=%s %s",
        command,
        mask_email(email),
        user_id,
        success_suffix.format(revoked=revoked),
    )
    return 0


async def admin_create_async(
    email: str,
    password: str,
    *,
    supabase_client: SupabaseAuthClientLike | None = None,
    redis_client: Any | None = None,
) -> int:
    """Create the admin, or reset the password if the email exists. Returns the exit code."""
    settings = get_settings()
    sb = supabase_client or _build_supabase_client(settings)
    redis = redis_client or _build_redis_client(settings)
    store = RefreshTokenStore(redis)
    try:
        existing = await sb.admin_find_user_by_email(email)
        if existing is None:
            created = await sb.admin_create_user(email=email, password=password, role="admin")
            user_id_raw: Any = created.get("id")
            user_id_str = user_id_raw if isinstance(user_id_raw, str) else "?"
            logger.info(
                "admin create email=%s user_id=%s outcome=created",
                mask_email(email),
                user_id_str,
            )
            return 0

        return await _rotate_admin_password(
            sb,
            redis,
            store,
            existing,
            email,
            password,
            command="admin create",
            success_suffix="outcome=updated families_revoked={revoked}",
        )
    finally:
        # Only close clients we built.
        if supabase_client is None:
            await sb.aclose()
        if redis_client is None:
            await redis.aclose()


async def admin_set_password_async(
    email: str,
    password: str,
    *,
    supabase_client: SupabaseAuthClientLike | None = None,
    redis_client: Any | None = None,
) -> int:
    """Set the password and revoke refresh tokens. Returns the exit code."""
    settings = get_settings()
    sb = supabase_client or _build_supabase_client(settings)
    redis = redis_client or _build_redis_client(settings)
    store = RefreshTokenStore(redis)
    try:
        existing = await sb.admin_find_user_by_email(email)
        if existing is None:
            logger.error("admin set-password email=%s: user not found", mask_email(email))
            return 1
        return await _rotate_admin_password(
            sb,
            redis,
            store,
            existing,
            email,
            password,
            command="admin set-password",
            success_suffix="families_revoked={revoked} outcome=ok",
        )
    finally:
        if supabase_client is None:
            await sb.aclose()
        if redis_client is None:
            await redis.aclose()


@click.group()
def cli() -> None:
    """RASID admin CLI."""


@cli.group()
def admin() -> None:
    """Admin coordinator lifecycle commands."""


@admin.command("create")
@click.option("--email", prompt="Email", help="Email for the new admin coordinator.")
@click.password_option(
    "--password",
    prompt="Password",
    confirmation_prompt=True,
    help="Password (hidden, confirmed). Argv flag is intended for tests only.",
)
def admin_create(email: str, password: str) -> None:
    """Create (or update) an admin coordinator with the given email."""
    exit_code = asyncio.run(admin_create_async(email, password))
    sys.exit(exit_code)


@admin.command("set-password")
@click.argument("email")
@click.password_option(
    "--password",
    prompt="Password",
    confirmation_prompt=True,
    help="Password (hidden, confirmed). Argv flag is intended for tests only.",
)
def admin_set_password(email: str, password: str) -> None:
    """Set the password for an existing coordinator and revoke their sessions."""
    exit_code = asyncio.run(admin_set_password_async(email, password))
    sys.exit(exit_code)


@cli.group()
def embeddings() -> None:
    """Embedding-pipeline maintenance commands."""


async def embeddings_backfill_async(
    crisis_id: uuid.UUID | None,
    batch_size: int,
) -> int:
    """Enqueue embed_report for every finalised report without an embedding. Idempotent."""
    from arq import create_pool
    from arq.connections import ArqRedis, RedisSettings
    from sqlalchemy import text

    from api.core.db import make_engine, make_sessionmaker

    settings = get_settings()
    engine = make_engine(settings.database_url)
    sessionmaker = make_sessionmaker(engine)
    pool: ArqRedis = await create_pool(RedisSettings.from_dsn(settings.redis_dsn))

    select_sql = text(
        """
        select r.id
          from public.reports r
          left join public.report_embeddings e on e.report_id = r.id
         where r.enrichment_finalized_at is not null
           and e.report_id is null
           and (cast(:crisis_id as uuid) is null or r.crisis_id = cast(:crisis_id as uuid))
         order by r.created_at asc
        """
    )

    enqueued = 0
    try:
        async with sessionmaker() as session:
            result = await session.execute(
                select_sql, {"crisis_id": str(crisis_id) if crisis_id else None}
            )
            ids = [row.id for row in result.all()]

        for offset in range(0, len(ids), batch_size):
            chunk = ids[offset : offset + batch_size]
            for rid in chunk:
                await pool.enqueue_job("embed_report", str(rid))
            enqueued += len(chunk)
            logger.info(
                "embeddings backfill progress enqueued=%d total=%d",
                enqueued,
                len(ids),
            )
        logger.info(
            "embeddings backfill done crisis_id=%s enqueued=%d",
            str(crisis_id) if crisis_id else "any",
            enqueued,
        )
    finally:
        await pool.close()
        await engine.dispose()

    return 0


@embeddings.command("backfill")
@click.option(
    "--crisis-id",
    type=click.UUID,
    default=None,
    help="Restrict to a single crisis. Omitted = all crises.",
)
@click.option(
    "--batch-size",
    type=click.IntRange(1, 1000),
    default=50,
    show_default=True,
    help="Number of jobs enqueued per progress log line.",
)
def embeddings_backfill(crisis_id: uuid.UUID | None, batch_size: int) -> None:
    """Enqueue embed_report for finalised reports missing an embedding row."""
    exit_code = asyncio.run(embeddings_backfill_async(crisis_id, batch_size))
    sys.exit(exit_code)


if __name__ == "__main__":
    cli()
