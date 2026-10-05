"""DB-backed tests for the per-cell §10 impact SQL (`data.fetch_impact_cells`).

The merge math that turns these rows into displaced/dollar map cells is unit
-tested DB-free in `tests/analysis/test_impact_merge.py`; here we exercise the
SQL itself: that damaged buildings aggregate onto the right 0.02° cell, that
the WorldPop-anchored displaced band is population / building-stock x per-class
fraction (every class contributes, minimal included), that minimal damage
contributes economic loss but not displacing-building count, and the coverage
-absent degradation paths (a cell with damage but no LitPop shows displacing
buildings yet `economic_available=False`; a cell with no population shows
`population_available=False`).

Reports are seeded at deliberately remote *ocean* cells, where LitPop and
WorldPop have no real coverage, so the seeded values are the only ones at the
cell and the figures are exact. Requires `supabase start` locally for Postgres.
"""

from __future__ import annotations

import asyncio
import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from api.analysis import data
from api.analysis.constants import (
    DAMAGE_RATIO,
    DISPLACEMENT_FRACTION,
    DISPLACING_DAMAGE_CLASSES,
)
from api.core.config import get_settings

pytestmark = pytest.mark.integration

# Two remote South-Pacific ocean cells, ~0.2° apart (so the LitPop bounds scan
# stays tiny) and far from any real LitPop coverage. ix = floor(lon / 0.02),
# iy = floor(lat / 0.02).
_STEP = 0.02
_A_LON, _A_LAT = -150.31, -30.31  # cell A: LitPop seeded
_B_LON, _B_LAT = -150.11, -30.11  # cell B: no LitPop (degradation path)
_A_IX, _A_IY = -7516, -1516
_B_IX, _B_IY = -7506, -1506


async def _seed_crisis(url: str) -> uuid.UUID:
    crisis_id = uuid.uuid4()
    suffix = uuid.uuid4().hex[:8]
    engine = create_async_engine(url)
    try:
        async with engine.begin() as conn:
            await conn.execute(
                text(
                    "insert into public.crises (id, name, status, type, countries) "
                    "values (:id, :n, 'active', 'flood', ARRAY['QA']::text[])"
                ),
                {"id": str(crisis_id), "n": f"Impact-cells test {suffix}"},
            )
    finally:
        await engine.dispose()
    return crisis_id


async def _seed_report(
    url: str, *, crisis_id: uuid.UUID, damage_class: str, location: tuple[float, float]
) -> None:
    """A building-less report with an explicit GPS location (its own impact unit)."""
    report_id = uuid.uuid4()
    lon, lat = location
    engine = create_async_engine(url)
    try:
        async with engine.begin() as conn:
            await conn.execute(
                text(
                    "insert into public.reports "
                    "  (id, crisis_id, damage_class, photo_path, location, route_description) "
                    "values (:id, :cid, :dc, :photo, "
                    "        st_setsrid(st_makepoint(:lon, :lat), 4326)::geography, "
                    "        'test directions')"
                ),
                {
                    "id": str(report_id),
                    "cid": str(crisis_id),
                    "dc": damage_class,
                    "photo": f"test/{report_id}.jpg",
                    "lon": lon,
                    "lat": lat,
                },
            )
    finally:
        await engine.dispose()


async def _seed_grids(
    url: str, *, release: str, dataset: str, usd: float, people: float, n: int
) -> None:
    """Density-grid stock + LitPop value + WorldPop population at cell A only."""
    engine = create_async_engine(url)
    try:
        async with engine.begin() as conn:
            await conn.execute(
                text(
                    "insert into public.building_density_grid (release, ix, iy, n) "
                    "values (:rel, :ix, :iy, :n)"
                ),
                {"rel": release, "ix": _A_IX, "iy": _A_IY, "n": n},
            )
            await conn.execute(
                text(
                    "insert into public.litpop_value (dataset, ix, iy, usd) "
                    "values (:ds, :ix, :iy, :usd)"
                ),
                {"ds": dataset, "ix": _A_IX, "iy": _A_IY, "usd": usd},
            )
            await conn.execute(
                text(
                    "insert into public.population_value (dataset, ix, iy, people) "
                    "values (:ds, :ix, :iy, :people)"
                ),
                {"ds": dataset, "ix": _A_IX, "iy": _A_IY, "people": people},
            )
    finally:
        await engine.dispose()


async def _fetch_cells(url: str, crisis_id: uuid.UUID, *, release: str) -> list[data.ImpactCellRow]:
    engine = create_async_engine(url)
    try:
        async with AsyncSession(engine) as session:
            return await data.fetch_impact_cells(
                session,
                crisis_id,
                displacing_classes=DISPLACING_DAMAGE_CLASSES,
                damage_ratio={c: (r.low, r.high) for c, r in DAMAGE_RATIO.items()},
                displacement_fraction={
                    c: (r.low, r.high) for c, r in DISPLACEMENT_FRACTION.items()
                },
                release=release,
            )
    finally:
        await engine.dispose()


async def _cleanup(url: str, *, crisis_id: uuid.UUID, release: str, dataset: str) -> None:
    engine = create_async_engine(url)
    try:
        async with engine.begin() as conn:
            await conn.execute(
                text("delete from public.reports where crisis_id = :id"), {"id": str(crisis_id)}
            )
            await conn.execute(
                text("delete from public.crises where id = :id"), {"id": str(crisis_id)}
            )
            await conn.execute(
                text("delete from public.building_density_grid where release = :rel"),
                {"rel": release},
            )
            await conn.execute(
                text("delete from public.litpop_value where dataset = :ds"), {"ds": dataset}
            )
            await conn.execute(
                text("delete from public.population_value where dataset = :ds"), {"ds": dataset}
            )
    finally:
        await engine.dispose()


def test_fetch_impact_cells_aggregates_per_cell_and_degrades_without_litpop() -> None:
    settings = get_settings()
    url = settings.database_url
    release = f"test-impact-{uuid.uuid4().hex[:8]}"
    dataset = f"test_litpop_{uuid.uuid4().hex[:8]}"
    crisis_id = asyncio.run(_seed_crisis(url))

    try:
        asyncio.run(
            _seed_grids(url, release=release, dataset=dataset, usd=1_000_000.0, people=200.0, n=10)
        )
        # Cell A: minimal + partial + complete (LitPop covered).
        for dc in ("minimal", "partial", "complete"):
            asyncio.run(
                _seed_report(url, crisis_id=crisis_id, damage_class=dc, location=(_A_LON, _A_LAT))
            )
        # Cell B: one partial report, no LitPop coverage.
        asyncio.run(
            _seed_report(
                url, crisis_id=crisis_id, damage_class="partial", location=(_B_LON, _B_LAT)
            )
        )

        rows = asyncio.run(_fetch_cells(url, crisis_id, release=release))
        by_cell = {(r.ix, r.iy): r for r in rows}

        # Cell A: 2 displacing buildings (partial+complete; minimal excluded),
        # economics over all three classes. per_building = 1_000_000 / 10.
        a = by_cell[(_A_IX, _A_IY)]
        assert a.displacing_buildings == 2
        assert a.economic_available is True
        assert a.economic_low_usd == pytest.approx(  # pyright: ignore[reportUnknownMemberType]
            100_000 * (0.02 + 0.25 + 0.75)
        )
        assert a.economic_high_usd == pytest.approx(  # pyright: ignore[reportUnknownMemberType]
            100_000 * (0.15 + 0.55 + 1.00)
        )
        # Displaced: population 200 / stock 10 = 20 people per building, summed
        # over all three damaged buildings x their per-class fraction (minimal
        # included): low = 20 x (0.1 + 0.3 + 0.9), high = 20 x (0.2 + 0.6 + 1.0).
        assert a.population_available is True
        assert a.displaced_low == pytest.approx(  # pyright: ignore[reportUnknownMemberType]
            20 * (0.1 + 0.3 + 0.9)
        )
        assert a.displaced_high == pytest.approx(  # pyright: ignore[reportUnknownMemberType]
            20 * (0.2 + 0.6 + 1.0)
        )

        # Cell B: damage present but no LitPop and no population → both degrade,
        # displacing-building count still reported.
        b = by_cell[(_B_IX, _B_IY)]
        assert b.displacing_buildings == 1
        assert b.economic_available is False
        assert b.economic_low_usd == 0.0
        assert b.economic_high_usd == 0.0
        assert b.population_available is False
        assert b.displaced_low == 0.0
        assert b.displaced_high == 0.0
    finally:
        asyncio.run(_cleanup(url, crisis_id=crisis_id, release=release, dataset=dataset))
