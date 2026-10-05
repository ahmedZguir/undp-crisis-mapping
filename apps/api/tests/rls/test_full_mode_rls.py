"""RLS policy test: `anon_select_reports_for_full_mode` +
`anon_select_buildings_for_full_mode` honour the `full`-mode gating.

Companion: migration `20260517150000_full_mode_rls.sql`.

The test seeds active crises in different `public_visibility` modes
(`full`, `aggregate_view`, `none`), inserts one `public_visible=true`
report against the full-mode crisis plus one `public_visible=false`
report against the same crisis, then queries `public.reports` /
`public.buildings` as the `anon` role and asserts only the full-mode
`public_visible=true` row is visible.

Mirrors the shape of `test_buildings_mode_rls.py` so a regression on
either policy surfaces against the same fixtures.

Connection: seeding runs as `api_service` (BYPASSRLS) via the
configured DSN; the anon SELECT opens a separate connection as
`postgres` (local Supabase superuser) so `SET LOCAL ROLE anon` is
permitted. Production callers reach anon via Supabase Auth JWTs
through PostgREST — the behavioural path this policy is built to
constrain.
"""

from __future__ import annotations

import asyncio
import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from api.core.config import get_settings

pytestmark = pytest.mark.integration


async def _seed_crisis_with_reports(
    *, public_visibility: str, with_hidden_report: bool = False
) -> tuple[uuid.UUID, uuid.UUID, uuid.UUID, uuid.UUID | None]:
    """Seed an `active` crisis plus one `public_visible=true` report
    against a fresh building. Optionally seed a second
    `public_visible=false` report against the same building.

    Returns `(crisis_id, building_id, visible_report_id,
    hidden_report_id_or_None)`.
    """
    settings = get_settings()
    crisis_id = uuid.uuid4()
    building_id = uuid.uuid4()
    visible_report_id = uuid.uuid4()
    hidden_report_id: uuid.UUID | None = None
    engine = create_async_engine(settings.database_url)
    try:
        async with engine.begin() as conn:
            await conn.execute(
                text(
                    "insert into public.crises "
                    "  (id, name, status, public_visibility, heatmap_k_anonymity) "
                    "values (:id, :n, 'active', :pv, 1)"
                ),
                {
                    "id": str(crisis_id),
                    "n": f"RLS full mode {public_visibility} {uuid.uuid4().hex[:6]}",
                    "pv": public_visibility,
                },
            )
            await conn.execute(
                text(
                    "insert into public.buildings (id, source, source_id, footprint) "
                    "values (:id, 'test', :src, "
                    "        st_geogfromtext("
                    "          'SRID=4326;MULTIPOLYGON(((51.499 25.299, 51.501 25.299, "
                    "           51.501 25.301, 51.499 25.301, 51.499 25.299)))'))"
                ),
                {"id": str(building_id), "src": uuid.uuid4().hex},
            )
            await conn.execute(
                text(
                    "insert into public.reports "
                    "  (id, crisis_id, damage_class, photo_path, "
                    "   building_id, public_visible, route_description) "
                    # route_description satisfies reports_location_or_route
                    "values (:id, :cid, 'partial', :p, :bid, true, 'test directions')"
                ),
                {
                    "id": str(visible_report_id),
                    "cid": str(crisis_id),
                    "p": f"reports/{visible_report_id}.jpg",
                    "bid": str(building_id),
                },
            )
            if with_hidden_report:
                hidden_report_id = uuid.uuid4()
                await conn.execute(
                    text(
                        "insert into public.reports "
                        "  (id, crisis_id, damage_class, photo_path, "
                        "   building_id, public_visible, route_description) "
                        # route_description satisfies reports_location_or_route
                        "values (:id, :cid, 'complete', :p, :bid, false, 'test directions')"
                    ),
                    {
                        "id": str(hidden_report_id),
                        "cid": str(crisis_id),
                        "p": f"reports/{hidden_report_id}.jpg",
                        "bid": str(building_id),
                    },
                )
    finally:
        await engine.dispose()
    return crisis_id, building_id, visible_report_id, hidden_report_id


async def _cleanup(crisis_ids: list[uuid.UUID]) -> None:
    settings = get_settings()
    engine = create_async_engine(settings.database_url)
    try:
        async with engine.begin() as conn:
            await conn.execute(
                text("delete from public.reports where crisis_id = any(:ids)"),
                {"ids": [str(i) for i in crisis_ids]},
            )
            await conn.execute(
                text("delete from public.crises where id = any(:ids)"),
                {"ids": [str(i) for i in crisis_ids]},
            )
            await conn.execute(
                text(
                    "delete from public.buildings "
                    " where source = 'test' "
                    "   and id not in (select building_id from public.reports "
                    "                    where building_id is not null)"
                )
            )
    finally:
        await engine.dispose()


def _postgres_dsn() -> str:
    """Build a local-superuser DSN by swapping out the role + password on
    the configured `database_url`. Mirrors the helper in
    `test_buildings_mode_rls.py` / `test_rls_heat_cells_visibility_policy.py`."""
    from urllib.parse import urlsplit, urlunsplit

    settings = get_settings()
    parts = urlsplit(settings.database_url)
    host_part = parts.netloc.split("@", 1)[1] if "@" in parts.netloc else parts.netloc
    new_netloc = f"postgres:postgres@{host_part}"
    return urlunsplit((parts.scheme, new_netloc, parts.path, parts.query, parts.fragment))


async def _anon_can_see_report(report_id: uuid.UUID) -> bool:
    """Run a SELECT against `public.reports` as the `anon` role.
    Returns True if the row is visible, False otherwise."""
    engine = create_async_engine(_postgres_dsn())
    try:
        async with engine.connect() as conn, conn.begin():
            await conn.execute(text("set local role anon"))
            row = (
                await conn.execute(
                    text("select count(*) as n from public.reports where id = :rid"),
                    {"rid": str(report_id)},
                )
            ).first()
        assert row is not None
        return int(row.n) > 0
    finally:
        await engine.dispose()


# --- Reports policy tests -----------------------------------------------


def test_anon_sees_full_mode_visible_report() -> None:
    """Sanity: a `public_visible=true` report against a `full`-mode
    crisis IS visible to anon. Catches a regression where the policy is
    over-tightened."""
    crisis_id, _, visible_id, _ = asyncio.run(_seed_crisis_with_reports(public_visibility="full"))
    try:
        assert asyncio.run(_anon_can_see_report(visible_id)) is True
    finally:
        asyncio.run(_cleanup([crisis_id]))


def test_anon_cannot_see_full_mode_hidden_report() -> None:
    """A `public_visible=false` report against a `full`-mode crisis is
    NOT visible to anon — the per-report `public_visible` kill switch is
    enforced at the RLS layer too, not just by the application."""
    crisis_id, _, _, hidden_id = asyncio.run(
        _seed_crisis_with_reports(public_visibility="full", with_hidden_report=True)
    )
    assert hidden_id is not None
    try:
        assert asyncio.run(_anon_can_see_report(hidden_id)) is False
    finally:
        asyncio.run(_cleanup([crisis_id]))


@pytest.mark.parametrize("visibility", ["none", "aggregate_view"])
def test_anon_cannot_see_reports_for_non_live_modes(visibility: str) -> None:
    """A leaked anon key cannot pull reports for a crisis whose
    coordinator set `public_visibility` to anything other than
    `buildings` or `full` — even if the report has `public_visible=true`.
    The API endpoint also refuses these requests; this policy is the
    defence-in-depth layer at the DB.

    Note: `buildings` is intentionally NOT parametrized here — the
    `anon_select_reports_for_buildings_mode` policy admits visible
    reports for `buildings`-mode crises; the OR-merge with the full-mode
    policy is asserted in `test_buildings_mode_rls.py`."""
    crisis_id, _, visible_id, _ = asyncio.run(
        _seed_crisis_with_reports(public_visibility=visibility)
    )
    try:
        assert asyncio.run(_anon_can_see_report(visible_id)) is False
    finally:
        asyncio.run(_cleanup([crisis_id]))


# --- Buildings policy tests ---------------------------------------------


async def _anon_can_see_building(building_id: uuid.UUID) -> bool:
    """Run a SELECT against `public.buildings` as the `anon` role,
    bounded to ONLY the `anon_select_buildings_for_full_mode` policy.

    The broader `anon_select_buildings` policy from
    `20260514120100_rls_enable_all_public.sql` admits every building
    row (Overture footprints are public geodata) — so a flat `select`
    test would always pass. To isolate the policy under test we
    temporarily drop the broad policy, run the assertion, and restore
    it. The savepoint pattern (`begin … rollback`) means the broad
    policy is never actually missing once the transaction unwinds.

    The drop+restore happens inside a `BEGIN` we explicitly roll back,
    so the live DB schema is unchanged after the test — only the
    inner SELECT sees the narrowed posture.
    """
    engine = create_async_engine(_postgres_dsn())
    try:
        async with engine.connect() as conn, conn.begin():
            # Drop the broad policy inside a transaction so the rollback
            # restores it. `set local role anon` only affects this
            # transaction.
            await conn.execute(
                text("drop policy if exists anon_select_buildings on public.buildings")
            )
            await conn.execute(text("set local role anon"))
            row = (
                await conn.execute(
                    text("select count(*) as n from public.buildings where id = :bid"),
                    {"bid": str(building_id)},
                )
            ).first()
            # Implicit rollback at context-exit restores the policy.
            await conn.execute(text("reset role"))
            await conn.execute(text("rollback"))
        assert row is not None
        return int(row.n) > 0
    finally:
        await engine.dispose()


def test_anon_buildings_for_full_mode_visible() -> None:
    """Sanity: a building tied to a `public_visible=true` report on a
    `full`-mode crisis IS visible to anon under the narrow policy."""
    crisis_id, building_id, _, _ = asyncio.run(_seed_crisis_with_reports(public_visibility="full"))
    try:
        assert asyncio.run(_anon_can_see_building(building_id)) is True
    finally:
        asyncio.run(_cleanup([crisis_id]))


def test_anon_buildings_for_aggregate_view_not_visible_under_narrow_policy() -> None:
    """The narrow policy on `public.buildings`
    (`anon_select_buildings_for_full_mode`) blocks a building tied to an
    `aggregate_view` crisis. The broader `anon_select_buildings` policy
    would still admit the row in production — but this test isolates
    the narrow policy's predicate."""
    crisis_id, building_id, _, _ = asyncio.run(
        _seed_crisis_with_reports(public_visibility="aggregate_view")
    )
    try:
        assert asyncio.run(_anon_can_see_building(building_id)) is False
    finally:
        asyncio.run(_cleanup([crisis_id]))
