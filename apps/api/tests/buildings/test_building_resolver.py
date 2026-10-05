"""Unit tests for BuildingResolver.resolve_gers and snap_to_nearest.

Exercises the deep-module boundary against the real DB: seed a handful of
`buildings` rows directly, then assert what the resolver returns. No mocking
of PostGIS — the spatial behavior (equality on `(source, source_id)` for
GERS resolution; metric `ST_DWithin` / `ST_Distance` over `geography`
centroids for snap-to-nearest) is the contract.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from api.buildings.resolver import BuildingResolver
from api.core.config import get_settings
from api.schemas import LocationIn

pytestmark = pytest.mark.integration


_FOOTPRINT_WKT = (
    "SRID=4326;MULTIPOLYGON(((10.0 10.0,10.001 10.0,10.001 10.001,10.0 10.001,10.0 10.0)))"
)


@pytest_asyncio.fixture
async def session() -> AsyncIterator[AsyncSession]:
    settings = get_settings()
    engine = create_async_engine(settings.database_url)
    sessionmaker = async_sessionmaker(engine, expire_on_commit=False)
    async with sessionmaker() as s:
        try:
            yield s
        finally:
            await s.rollback()
    await engine.dispose()


async def _seed_building(session: AsyncSession, source: str, source_id: str) -> uuid.UUID:
    result = await session.execute(
        text(
            "insert into public.buildings (source, source_id, footprint) "
            "values (:s, :sid, st_geogfromtext(:wkt)) returning id"
        ),
        {"s": source, "sid": source_id, "wkt": _FOOTPRINT_WKT},
    )
    row = result.one()
    await session.commit()
    return uuid.UUID(str(row.id))


async def _delete_building(session: AsyncSession, building_id: uuid.UUID) -> None:
    await session.execute(
        text("delete from public.buildings where id = :id"),
        {"id": str(building_id)},
    )
    await session.commit()


async def test_resolve_gers_returns_id_for_known_overture_source_id(
    session: AsyncSession,
) -> None:
    source_id = f"gers-{uuid.uuid4().hex}"
    seeded_id = await _seed_building(session, "overture", source_id)
    try:
        resolver = BuildingResolver(session)
        resolved = await resolver.resolve_gers(source_id)
        assert resolved == seeded_id
    finally:
        await _delete_building(session, seeded_id)


async def test_resolve_gers_returns_none_for_unknown_source_id(
    session: AsyncSession,
) -> None:
    resolver = BuildingResolver(session)
    bogus = f"gers-missing-{uuid.uuid4().hex}"
    assert await resolver.resolve_gers(bogus) is None


async def test_resolve_gers_returns_none_when_source_id_only_exists_under_other_source(
    session: AsyncSession,
) -> None:
    """v1 hard-codes source='overture'. A row with the same source_id under a
    different `source` must NOT match — otherwise an OSM id colliding with a
    GERS string would silently bind."""
    source_id = f"gers-other-{uuid.uuid4().hex}"
    seeded_id = await _seed_building(session, "osm", source_id)
    try:
        resolver = BuildingResolver(session)
        assert await resolver.resolve_gers(source_id) is None
    finally:
        await _delete_building(session, seeded_id)


# ---- snap_to_nearest ----------------------------------------------------


async def _seed_building_at(session: AsyncSession, lat: float, lng: float) -> uuid.UUID:
    """Insert an Overture building whose centroid sits at (lat, lng).

    The footprint is a tiny square symmetrical around (lng, lat); the
    generated `centroid` column therefore evaluates to (lng, lat).
    """
    half = 0.00005
    wkt = (
        f"SRID=4326;MULTIPOLYGON((("
        f"{lng - half} {lat - half},"
        f"{lng + half} {lat - half},"
        f"{lng + half} {lat + half},"
        f"{lng - half} {lat + half},"
        f"{lng - half} {lat - half}"
        f")))"
    )
    source_id = f"gers-snap-{uuid.uuid4().hex}"
    result = await session.execute(
        text(
            "insert into public.buildings (source, source_id, footprint) "
            "values ('overture', :sid, st_geogfromtext(:wkt)) returning id"
        ),
        {"sid": source_id, "wkt": wkt},
    )
    row = result.one()
    await session.commit()
    return uuid.UUID(str(row.id))


# Latitude band ~10°: 1° lat ≈ 110_574 m. We pick offsets that put a probe
# point clearly inside / outside the 50 m radius of a seeded centroid.
# 49 m → ~0.0004431° lat; 51 m → ~0.0004613° lat.
_LAT_OFFSET_49M = 49.0 / 110_574.0
_LAT_OFFSET_51M = 51.0 / 110_574.0


async def test_snap_to_nearest_returns_building_when_point_within_50m(
    session: AsyncSession,
) -> None:
    seeded_id = await _seed_building_at(session, lat=10.0, lng=10.0)
    try:
        resolver = BuildingResolver(session)
        probe = LocationIn(lat=10.0 + _LAT_OFFSET_49M, lng=10.0)
        assert await resolver.snap_to_nearest(probe) == seeded_id
    finally:
        await _delete_building(session, seeded_id)


async def test_snap_to_nearest_returns_none_when_point_just_outside_50m(
    session: AsyncSession,
) -> None:
    seeded_id = await _seed_building_at(session, lat=10.0, lng=10.0)
    try:
        resolver = BuildingResolver(session)
        probe = LocationIn(lat=10.0 + _LAT_OFFSET_51M, lng=10.0)
        assert await resolver.snap_to_nearest(probe) is None
    finally:
        await _delete_building(session, seeded_id)


async def test_snap_to_nearest_returns_closest_when_multiple_within_range(
    session: AsyncSession,
) -> None:
    """Probe sits between two seeded centroids, both inside 50 m. The query
    must order by distance and return the nearer of the two."""
    # Probe at (10.0, 10.0). Near building ~10 m north, far building ~40 m south.
    near_offset = 10.0 / 110_574.0
    far_offset = 40.0 / 110_574.0
    near_id = await _seed_building_at(session, lat=10.0 + near_offset, lng=10.0)
    far_id = await _seed_building_at(session, lat=10.0 - far_offset, lng=10.0)
    try:
        resolver = BuildingResolver(session)
        probe = LocationIn(lat=10.0, lng=10.0)
        assert await resolver.snap_to_nearest(probe) == near_id
    finally:
        await _delete_building(session, near_id)
        await _delete_building(session, far_id)


async def test_snap_to_nearest_returns_none_in_empty_region(
    session: AsyncSession,
) -> None:
    """A probe in a region with no buildings within 50 m returns None even
    if buildings exist far away."""
    # Seed a building at (10, 10); probe at the antipode-ish (-10, -10).
    seeded_id = await _seed_building_at(session, lat=10.0, lng=10.0)
    try:
        resolver = BuildingResolver(session)
        probe = LocationIn(lat=-10.0, lng=-10.0)
        assert await resolver.snap_to_nearest(probe) is None
    finally:
        await _delete_building(session, seeded_id)
