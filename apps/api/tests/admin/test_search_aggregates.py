"""DB-backed tests for `compute_search_aggregates`.

`compute_search_aggregates` runs SQL `GROUP BY` over the **full filtered set**,
so its numbers stay correct even when the row payload is truncated at
`SearchRequest.limit`. These tests seed reports directly and assert the
full-set aggregates the coordinator dashboard's KPI ribbon + analytics charts
rely on.

Requires `supabase start` locally for the Postgres side. The semantic path
(query embedding join) is left to the route-level smoke test — it differs
from the structured path only by the embeddings join + distance predicate,
which mirror `run_search`'s proven semantic SQL.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from api.admin.search_aggregates import compute_search_aggregates
from api.core.config import get_settings
from api.schemas.admin_search import LocationFilter, SearchRequest, SearchStats

pytestmark = pytest.mark.integration

# A deliberately remote bbox so seeded buildings don't collide with the
# fixtures other DB tests leave around (which cluster near 25.3, 51.5).
_LAT = 10.123456
_LNG = 20.654321
_BBOX = (20.0, 10.0, 21.0, 11.0)  # (w, s, e, n)


# --- Seed helpers --------------------------------------------------------


async def _seed_crisis(url: str, *, buildings_ingested_count: int | None) -> uuid.UUID:
    crisis_id = uuid.uuid4()
    suffix = uuid.uuid4().hex[:8]
    engine = create_async_engine(url)
    try:
        async with engine.begin() as conn:
            await conn.execute(
                text(
                    "insert into public.crises "
                    "  (id, name, status, type, countries, buildings_ingested_count) "
                    "values (:id, :n, 'active', 'flood', ARRAY['QA']::text[], :bc)"
                ),
                {
                    "id": str(crisis_id),
                    "n": f"Aggregates test {suffix}",
                    "bc": buildings_ingested_count,
                },
            )
    finally:
        await engine.dispose()
    return crisis_id


async def _seed_building(url: str, *, centroid: tuple[float, float]) -> uuid.UUID:
    building_id = uuid.uuid4()
    lat, lng = centroid
    d = 0.00001
    wkt = (
        "MULTIPOLYGON((("
        f"{lng - d} {lat - d},{lng + d} {lat - d},{lng + d} {lat + d},"
        f"{lng - d} {lat + d},{lng - d} {lat - d})))"
    )
    engine = create_async_engine(url)
    try:
        async with engine.begin() as conn:
            await conn.execute(
                text(
                    "insert into public.buildings (id, source, source_id, footprint) "
                    "values (:id, 'test', :src, st_geogfromtext(:wkt))"
                ),
                {"id": str(building_id), "src": uuid.uuid4().hex, "wkt": wkt},
            )
    finally:
        await engine.dispose()
    return building_id


async def _seed_report(
    url: str,
    *,
    crisis_id: uuid.UUID,
    damage_class: str,
    infra: list[str] | None = None,
    debris: str | None = None,
    client_id: uuid.UUID | None = None,
    building_id: uuid.UUID | None = None,
    location: tuple[float, float] | None = None,
    created_at: datetime | None = None,
) -> uuid.UUID:
    report_id = uuid.uuid4()
    location_sql = (
        "st_setsrid(st_makepoint(:lng, :lat), 4326)::geography" if location is not None else "null"
    )
    created_at_sql = "cast(:created_at as timestamptz)" if created_at is not None else "now()"
    params: dict[str, object | None] = {
        "id": str(report_id),
        "crisis_id": str(crisis_id),
        "dc": damage_class,
        "photo": f"test/{report_id}.jpg",
        "infra": infra,
        "debris": debris,
        "client_id": str(client_id) if client_id is not None else None,
        "building_id": str(building_id) if building_id is not None else None,
    }
    if location is not None:
        params["lat"], params["lng"] = location[0], location[1]
    if created_at is not None:
        params["created_at"] = created_at

    engine = create_async_engine(url)
    try:
        async with engine.begin() as conn:
            await conn.execute(
                text(
                    "insert into public.reports "
                    "  (id, crisis_id, damage_class, photo_path, infra_type, debris, "
                    "   client_id, building_id, location, created_at, route_description) "
                    "values (:id, :crisis_id, :dc, :photo, cast(:infra as text[]), :debris, "
                    f"        cast(:client_id as uuid), :building_id, {location_sql}, "
                    f"        {created_at_sql}, 'test directions')"
                ),
                params,
            )
    finally:
        await engine.dispose()
    return report_id


async def _cleanup(url: str, *, crisis_ids: list[uuid.UUID], building_ids: list[uuid.UUID]) -> None:
    engine = create_async_engine(url)
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
            if building_ids:
                await conn.execute(
                    text("delete from public.buildings where id = any(:ids)"),
                    {"ids": [str(i) for i in building_ids]},
                )
    finally:
        await engine.dispose()


async def _aggregate(url: str, crisis_id: uuid.UUID, request: SearchRequest) -> SearchStats:
    engine = create_async_engine(url)
    try:
        async with AsyncSession(engine) as session:
            return await compute_search_aggregates(
                session, crisis_id=crisis_id, request=request, query_vector=None
            )
    finally:
        await engine.dispose()


# --- Tests ---------------------------------------------------------------


def test_aggregates_over_full_filtered_set() -> None:
    settings = get_settings()
    url = settings.database_url
    crisis_id = asyncio.run(_seed_crisis(url, buildings_ingested_count=99))
    building_id = asyncio.run(_seed_building(url, centroid=(_LAT, _LNG)))
    client_a, client_b = uuid.uuid4(), uuid.uuid4()
    now = datetime.now(UTC)

    async def seed_all() -> None:
        await _seed_report(
            url,
            crisis_id=crisis_id,
            damage_class="complete",
            infra=["residential"],
            debris="yes",
            client_id=client_a,
            building_id=building_id,
            location=(_LAT, _LNG),
            created_at=now,
        )
        await _seed_report(
            url,
            crisis_id=crisis_id,
            damage_class="complete",
            infra=["residential", "commercial"],
            debris="no",
            client_id=client_a,
            location=(_LAT + 0.0001, _LNG),
            created_at=now,
        )
        await _seed_report(
            url,
            crisis_id=crisis_id,
            damage_class="partial",
            infra=["commercial"],
            debris="unknown",
            client_id=client_b,
            building_id=building_id,
            created_at=now,
        )
        await _seed_report(
            url,
            crisis_id=crisis_id,
            damage_class="minimal",
            location=(_LAT, _LNG + 0.0001),
            created_at=now,
        )

    asyncio.run(seed_all())
    try:
        stats = asyncio.run(_aggregate(url, crisis_id, SearchRequest()))

        assert stats.total == 4
        assert stats.severity == {"complete": 2, "partial": 1, "minimal": 1}
        # infra membership: residential in r1,r2; commercial in r2,r3.
        assert stats.infra_breakdown == {"residential": 2, "commercial": 2}
        assert stats.top_infra_count == 2
        assert stats.top_infra in {"residential", "commercial"}
        # distinct non-null client ids: {a, b}.
        assert stats.unique_devices == 2
        # distinct non-null building ids: {building_id} (r1, r3).
        assert stats.buildings_affected == 1
        assert stats.with_building == 2
        assert stats.with_gps == 3
        assert stats.debris_yes == 1
        assert stats.debris_known == 2  # yes + no, not unknown
        # all seeded "now" → one daily bucket carrying the full severity split.
        assert len(stats.daily) == 1
        assert stats.daily[0].complete == 2
        assert stats.daily[0].partial == 1
        assert stats.daily[0].minimal == 1
        assert stats.last_24h == 4
        # no location filter → denominator is the crisis-wide ingest count.
        assert stats.buildings_total == 99
    finally:
        asyncio.run(_cleanup(url, crisis_ids=[crisis_id], building_ids=[building_id]))


def test_aggregates_ignore_the_row_limit() -> None:
    """The headline invariant: aggregates reflect the full set, not the cap."""
    settings = get_settings()
    url = settings.database_url
    crisis_id = asyncio.run(_seed_crisis(url, buildings_ingested_count=0))
    now = datetime.now(UTC)

    async def seed_all() -> None:
        for _ in range(5):
            await _seed_report(url, crisis_id=crisis_id, damage_class="complete", created_at=now)

    asyncio.run(seed_all())
    try:
        # limit=2 would cap `run_search` rows at 2, but aggregates see all 5.
        stats = asyncio.run(_aggregate(url, crisis_id, SearchRequest(limit=2)))
        assert stats.total == 5
        assert stats.severity["complete"] == 5
    finally:
        asyncio.run(_cleanup(url, crisis_ids=[crisis_id], building_ids=[]))


def test_buildings_total_is_always_the_crisis_wide_count() -> None:
    """`buildings_total` is the precomputed crisis-wide denominator regardless
    of the active location filter.

    We deliberately do NOT rescope the denominator to the bbox / drawn
    polygon: a live `ST_Intersects` count over the 28M-row global `buildings`
    table costs several seconds per call, and this aggregate runs on every
    keystroke, filter toggle, and map pan. The numerator (`buildings_affected`)
    still follows the filter. See `_count_buildings_total`.
    """
    settings = get_settings()
    url = settings.database_url
    crisis_id = asyncio.run(_seed_crisis(url, buildings_ingested_count=99))
    asyncio.run(
        _seed_report(url, crisis_id=crisis_id, damage_class="partial", location=(_LAT, _LNG))
    )
    try:
        # No location → crisis-wide precomputed denominator.
        crisis_wide = asyncio.run(_aggregate(url, crisis_id, SearchRequest()))
        assert crisis_wide.buildings_total == 99

        # A bbox filter narrows the numerator but the denominator stays
        # crisis-wide (the precomputed count, not a live spatial scan).
        scoped = asyncio.run(
            _aggregate(url, crisis_id, SearchRequest(location=LocationFilter(bbox=_BBOX)))
        )
        assert scoped.buildings_total == 99
    finally:
        asyncio.run(_cleanup(url, crisis_ids=[crisis_id], building_ids=[]))
