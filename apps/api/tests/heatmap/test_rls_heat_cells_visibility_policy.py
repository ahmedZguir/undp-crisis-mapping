"""RLS policy test: `anon_select_active_heat_cells` honours the new
`crises.public_visibility` column.

Migration `20260514130000_crisis_public_visibility.sql` drops and
re-creates the policy so it requires `public_visibility =
'aggregate_view'` in addition to the existing active-crisis and
non-reserved-name filters.

The test seeds two `active` crises (one `aggregate_view`, one `none`),
inserts one `heat_cells` row for each, then queries the table as the
`anon` role and asserts only the `aggregate_view` row is visible. This
is defence-in-depth: the API tile/stats handlers also refuse to render
`none` crises, but this policy ensures a leaked anon key cannot bypass
the API by hitting PostgREST directly.

Connection: seeding runs as `api_service` via the configured DSN
(BYPASSRLS), then the anon SELECT opens a separate connection as
`postgres` (the local Supabase superuser) so `SET LOCAL ROLE anon`
is permitted. This is a test-only side door — production callers
reach anon via Supabase Auth JWTs through PostgREST, which is the
behavioural path this policy is built to constrain.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from api.core.config import get_settings
from api.core.h3 import cell_for_location

pytestmark = pytest.mark.integration


async def _seed(public_visibility: str) -> tuple[uuid.UUID, int]:
    """Seed a single active crisis with one `heat_cells` row. Returns
    `(crisis_id, h3_cell)` so the anon-side query can match exactly."""
    settings = get_settings()
    crisis_id = uuid.uuid4()
    cell = cell_for_location(25.30, 51.50 + 0.001 * len(public_visibility))
    engine = create_async_engine(settings.database_url)
    try:
        async with engine.begin() as conn:
            await conn.execute(
                text(
                    "insert into public.crises "
                    "  (id, name, status, created_at, public_visibility, "
                    "   heatmap_k_anonymity) "
                    "values (:id, :n, 'active', :t, :pv, 1)"
                ),
                {
                    "id": str(crisis_id),
                    "n": f"RLS vis test {public_visibility} {uuid.uuid4().hex[:6]}",
                    "t": datetime.now(UTC) - timedelta(minutes=1),
                    "pv": public_visibility,
                },
            )
            await conn.execute(
                text(
                    "insert into public.heat_cells "
                    "  (crisis_id, h3_cell, report_count, partial_count, latest_at) "
                    "values (:cid, :cell, 1, 1, :ts)"
                ),
                {"cid": str(crisis_id), "cell": cell, "ts": datetime.now(UTC)},
            )
    finally:
        await engine.dispose()
    return crisis_id, cell


async def _cleanup(crisis_ids: list[uuid.UUID]) -> None:
    settings = get_settings()
    engine = create_async_engine(settings.database_url)
    try:
        async with engine.begin() as conn:
            await conn.execute(
                text("delete from public.heat_cells where crisis_id = any(:ids)"),
                {"ids": [str(i) for i in crisis_ids]},
            )
            await conn.execute(
                text("delete from public.crises where id = any(:ids)"),
                {"ids": [str(i) for i in crisis_ids]},
            )
    finally:
        await engine.dispose()


def _postgres_dsn() -> str:
    """Build a local-superuser DSN by swapping out the role + password on
    the configured `database_url`.

    The configured DSN runs as `api_service`, which has `BYPASSRLS` but
    is NOT a member of `anon` and therefore cannot `SET LOCAL ROLE anon`.
    For the policy test we need a role that CAN switch into anon — the
    `postgres` superuser is the standard local-stack option.

    URLs in this codebase use the asyncpg dialect:
        postgresql+asyncpg://api_service:<pw>@host:port/db
    We replace the userinfo prefix only; everything after the `@` stays.
    """
    from urllib.parse import urlsplit, urlunsplit

    settings = get_settings()
    parts = urlsplit(settings.database_url)
    # Force the netloc's userinfo to postgres:postgres; keep host/port.
    host_part = parts.netloc.split("@", 1)[1] if "@" in parts.netloc else parts.netloc
    new_netloc = f"postgres:postgres@{host_part}"
    return urlunsplit((parts.scheme, new_netloc, parts.path, parts.query, parts.fragment))


async def _anon_can_see(crisis_id: uuid.UUID) -> bool:
    """Run a SELECT against `heat_cells` as the `anon` role. Returns True
    if any row for the given crisis is visible to anon, False otherwise.

    `SET LOCAL ROLE anon` flips the role for the duration of the
    transaction; pg restores the calling role on COMMIT/ROLLBACK. The
    role switch makes the query subject to the same RLS policies as a
    PostgREST anon read. Uses a `postgres` DSN because `api_service`
    is not a member of `anon` and cannot switch into it.
    """
    engine = create_async_engine(_postgres_dsn())
    try:
        async with engine.connect() as conn, conn.begin():
            await conn.execute(text("set local role anon"))
            row = (
                await conn.execute(
                    text("select count(*) as n from public.heat_cells where crisis_id = :cid"),
                    {"cid": str(crisis_id)},
                )
            ).first()
        assert row is not None
        return int(row.n) > 0
    finally:
        await engine.dispose()


def test_anon_sees_aggregate_view_heat_cells() -> None:
    """Sanity check: the policy still admits the `aggregate_view` row.
    This catches a regression where the policy is over-tightened (i.e.
    the active-crisis branch breaks)."""
    crisis_id, _ = asyncio.run(_seed("aggregate_view"))
    try:
        assert asyncio.run(_anon_can_see(crisis_id)) is True
    finally:
        asyncio.run(_cleanup([crisis_id]))


@pytest.mark.parametrize("visibility", ["none", "buildings", "full"])
def test_anon_cannot_see_non_aggregate_visibility(visibility: str) -> None:
    """A leaked anon key cannot pull heat cells for a crisis whose
    coordinator set `public_visibility` to anything other than
    `aggregate_view`. The API handler also refuses these requests; this
    policy is the defence-in-depth layer at the DB.
    """
    crisis_id, _ = asyncio.run(_seed(visibility))
    try:
        assert asyncio.run(_anon_can_see(crisis_id)) is False
    finally:
        asyncio.run(_cleanup([crisis_id]))
