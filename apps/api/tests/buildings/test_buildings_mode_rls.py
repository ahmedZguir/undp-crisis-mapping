"""RLS policy test: `anon_select_reports_for_buildings_mode` and
`anon_select_buildings_for_buildings_mode` honour the `buildings`-mode
gating.

Companion: migration `20260517140000_buildings_mode_rls.sql`.

The test seeds three `active` crises in different `public_visibility`
modes (`buildings`, `aggregate_view`, `none`), inserts one
`public_visible=true` report against the buildings-mode crisis plus
one `public_visible=false` report against the same crisis, then
queries `public.reports` as the `anon` role and asserts only the
buildings-mode `public_visible=true` row is visible. This mirrors the
shape of `test_rls_heat_cells_visibility_policy.py`.

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
) -> tuple[uuid.UUID, uuid.UUID, uuid.UUID | None]:
    """Seed an `active` crisis plus one `public_visible=true` report
    against a fresh building. Optionally seed a second
    `public_visible=false` report against the same building.

    Returns `(crisis_id, visible_report_id, hidden_report_id_or_None)`.
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
                    "n": f"RLS buildings mode {public_visibility} {uuid.uuid4().hex[:6]}",
                    "pv": public_visibility,
                },
            )
            # Buildings live in their own table — the offset on the polygon
            # corner avoids any (vanishingly small) conflict on the unique
            # (source, source_id) key across parallel test runs.
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
    return crisis_id, visible_report_id, hidden_report_id


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
            # Buildings have no FK back to crises; orphan rows linger
            # otherwise. Delete the buildings that only this test
            # touched via the 'test' source tag — narrow enough not to
            # interfere with other tests, broad enough to stay clean.
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
    `test_rls_heat_cells_visibility_policy.py`."""
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


def test_anon_sees_buildings_mode_visible_report() -> None:
    """Sanity: a `public_visible=true` report against a `buildings`-mode
    crisis IS visible to anon. Catches a regression where the policy is
    over-tightened."""
    crisis_id, visible_id, _ = asyncio.run(_seed_crisis_with_reports(public_visibility="buildings"))
    try:
        assert asyncio.run(_anon_can_see_report(visible_id)) is True
    finally:
        asyncio.run(_cleanup([crisis_id]))


def test_anon_cannot_see_buildings_mode_hidden_report() -> None:
    """A `public_visible=false` report against a `buildings`-mode crisis
    is NOT visible to anon — the per-report kill switch (`reports.public_visible`) is
    enforced at the RLS layer too, not just by the application."""
    crisis_id, _, hidden_id = asyncio.run(
        _seed_crisis_with_reports(public_visibility="buildings", with_hidden_report=True)
    )
    assert hidden_id is not None
    try:
        assert asyncio.run(_anon_can_see_report(hidden_id)) is False
    finally:
        asyncio.run(_cleanup([crisis_id]))


@pytest.mark.parametrize("visibility", ["none", "aggregate_view"])
def test_anon_cannot_see_reports_for_non_buildings_modes(visibility: str) -> None:
    """A leaked anon key cannot pull reports for a crisis whose
    coordinator set `public_visibility` to `none` or `aggregate_view` —
    even if the report has `public_visible=true`. The API endpoint also
    refuses these requests; this policy is the defence-in-depth layer at
    the DB.

    Note: `full` is intentionally NOT parametrized here: full mode added
    `anon_select_reports_for_full_mode` (migration
    `20260517150000_full_mode_rls.sql`) which OR-merges with this
    policy and admits visible reports for `full`-mode crises. The
    full-mode policy is asserted in `test_full_mode_rls.py`."""
    crisis_id, visible_id, _ = asyncio.run(_seed_crisis_with_reports(public_visibility=visibility))
    try:
        assert asyncio.run(_anon_can_see_report(visible_id)) is False
    finally:
        asyncio.run(_cleanup([crisis_id]))
