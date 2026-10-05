"""Happy-path coverage for `GET /crises/{id}/public/buildings`.

Seeds a `buildings`-mode crisis with three reports against two pins
(one building-matched group of two reports, one freeform pin), asserts
the response shape, feature count, and per-feature properties.

Skipped if Supabase is not reachable, matching the integration-test
pattern used across the suite.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from api.core.config import get_settings
from api.main import app

pytestmark = pytest.mark.integration


async def _seed(
    engine_url: str,
) -> tuple[uuid.UUID, uuid.UUID]:
    """Seed a buildings-mode crisis with:

        * 2 reports against one shared building (partial + complete)
        * 1 freeform report (no building_id, with location)

    Returns `(crisis_id, building_id)`. The building's centroid lives
    at (25.30, 51.50); the freeform report is offset to (25.31, 51.51).
    """
    crisis_id = uuid.uuid4()
    building_id = uuid.uuid4()
    engine = create_async_engine(engine_url)
    try:
        async with engine.begin() as conn:
            await conn.execute(
                text(
                    "insert into public.crises "
                    "  (id, name, status, created_at, public_visibility, "
                    "   heatmap_k_anonymity) "
                    "values (:id, :n, 'active', :t, 'buildings', 1)"
                ),
                {
                    "id": str(crisis_id),
                    "n": f"buildings-endpoint {uuid.uuid4().hex[:6]}",
                    "t": datetime.now(UTC) - timedelta(minutes=1),
                },
            )
            await conn.execute(
                text(
                    "insert into public.buildings "
                    "  (id, source, source_id, footprint) "
                    "values (:id, 'test', :src, "
                    "        st_geogfromtext("
                    "          'SRID=4326;MULTIPOLYGON(((51.499 25.299, 51.501 25.299, "
                    "           51.501 25.301, 51.499 25.301, 51.499 25.299)))'))"
                ),
                {"id": str(building_id), "src": uuid.uuid4().hex},
            )
            # Two reports on the same building, different classes.
            for damage in ("partial", "complete"):
                await conn.execute(
                    text(
                        "insert into public.reports "
                        "  (id, crisis_id, damage_class, photo_path, "
                        "   building_id, public_visible, route_description) "
                        # route_description satisfies reports_location_or_route
                        "values (:id, :cid, :d, :p, :bid, true, 'test directions')"
                    ),
                    {
                        "id": str(uuid.uuid4()),
                        "cid": str(crisis_id),
                        "d": damage,
                        "p": f"reports/{uuid.uuid4()}.jpg",
                        "bid": str(building_id),
                    },
                )
            # One freeform report — no building_id, but has a location.
            await conn.execute(
                text(
                    "insert into public.reports "
                    "  (id, crisis_id, damage_class, photo_path, "
                    "   location, public_visible, route_description) "
                    # route_description satisfies reports_location_or_route
                    "values (:id, :cid, 'minimal', :p, "
                    "        st_geogfromtext('SRID=4326;POINT(51.51 25.31)'), true, "
                    "        'test directions')"
                ),
                {
                    "id": str(uuid.uuid4()),
                    "cid": str(crisis_id),
                    "p": f"reports/{uuid.uuid4()}.jpg",
                },
            )
    finally:
        await engine.dispose()
    return crisis_id, building_id


async def _cleanup(engine_url: str, *, crisis_id: uuid.UUID, building_id: uuid.UUID) -> None:
    engine = create_async_engine(engine_url)
    try:
        async with engine.begin() as conn:
            await conn.execute(
                text("delete from public.reports where crisis_id = :cid"),
                {"cid": str(crisis_id)},
            )
            await conn.execute(
                text("delete from public.buildings where id = :id"),
                {"id": str(building_id)},
            )
            await conn.execute(
                text("delete from public.crises where id = :id"),
                {"id": str(crisis_id)},
            )
    finally:
        await engine.dispose()


def test_public_buildings_returns_feature_collection() -> None:
    """One pin per building + one pin per freeform report — two features
    in the response. Shape is a valid GeoJSON FeatureCollection with
    `[lng, lat]` ordering per RFC 7946."""
    settings = get_settings()
    crisis_id, building_id = asyncio.run(_seed(settings.database_url))
    try:
        with TestClient(app) as client:
            response = client.get(f"/crises/{crisis_id}/public/buildings")
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["type"] == "FeatureCollection"
        features = body["features"]
        assert len(features) == 2

        # Identify the building-matched feature vs the freeform one.
        building_feature = next(
            f for f in features if f["properties"]["building_id"] == str(building_id)
        )
        freeform_feature = next(f for f in features if f["properties"]["building_id"] is None)

        # Building pin: worst class is `complete` (one partial + one complete);
        # report_count is 2; coordinates land near the building's centroid.
        assert building_feature["properties"]["damage_class"] == "complete"
        assert building_feature["properties"]["report_count"] == 2
        lng, lat = building_feature["geometry"]["coordinates"]
        assert abs(lng - 51.50) < 0.01
        assert abs(lat - 25.30) < 0.01
        assert building_feature["geometry"]["type"] == "Point"

        # Freeform pin: single report, `minimal`, plotted at its own location.
        assert freeform_feature["properties"]["damage_class"] == "minimal"
        assert freeform_feature["properties"]["report_count"] == 1
        lng, lat = freeform_feature["geometry"]["coordinates"]
        assert abs(lng - 51.51) < 1e-6
        assert abs(lat - 25.31) < 1e-6
    finally:
        asyncio.run(_cleanup(settings.database_url, crisis_id=crisis_id, building_id=building_id))


async def _seed_crisis_only(
    engine_url: str,
    *,
    public_visibility: str,
    status: str = "active",
) -> uuid.UUID:
    crisis_id = uuid.uuid4()
    engine = create_async_engine(engine_url)
    try:
        async with engine.begin() as conn:
            await conn.execute(
                text(
                    "insert into public.crises "
                    "  (id, name, status, public_visibility, "
                    "   heatmap_k_anonymity) "
                    "values (:id, :n, :s, :pv, 1)"
                ),
                {
                    "id": str(crisis_id),
                    "n": f"buildings-gating {public_visibility}/{status} {uuid.uuid4().hex[:6]}",
                    "s": status,
                    "pv": public_visibility,
                },
            )
    finally:
        await engine.dispose()
    return crisis_id


async def _cleanup_crisis_only(engine_url: str, *, crisis_id: uuid.UUID) -> None:
    engine = create_async_engine(engine_url)
    try:
        async with engine.begin() as conn:
            await conn.execute(
                text("delete from public.reports where crisis_id = :cid"),
                {"cid": str(crisis_id)},
            )
            await conn.execute(
                text("delete from public.crises where id = :id"),
                {"id": str(crisis_id)},
            )
    finally:
        await engine.dispose()


@pytest.mark.parametrize("visibility", ["none", "aggregate_view", "full"])
def test_public_buildings_other_modes_return_404(visibility: str) -> None:
    """The buildings endpoint is bound to `buildings` mode — every other
    `public_visibility` value 404s (mirror the heat-tile mode gate)."""
    settings = get_settings()
    crisis_id = asyncio.run(_seed_crisis_only(settings.database_url, public_visibility=visibility))
    try:
        with TestClient(app) as client:
            response = client.get(f"/crises/{crisis_id}/public/buildings")
        assert response.status_code == 404, response.text
    finally:
        asyncio.run(_cleanup_crisis_only(settings.database_url, crisis_id=crisis_id))


@pytest.mark.parametrize("status", ["inactive", "archived"])
def test_public_buildings_non_active_status_returns_404(status: str) -> None:
    """An `inactive` or `archived` crisis 404s even when its mode is
    `buildings` — the public surface only serves `active` crises."""
    settings = get_settings()
    crisis_id = asyncio.run(
        _seed_crisis_only(settings.database_url, public_visibility="buildings", status=status)
    )
    try:
        with TestClient(app) as client:
            response = client.get(f"/crises/{crisis_id}/public/buildings")
        assert response.status_code == 404, response.text
    finally:
        asyncio.run(_cleanup_crisis_only(settings.database_url, crisis_id=crisis_id))


def test_public_buildings_unknown_crisis_returns_404() -> None:
    """An unknown `crisis_id` UUID 404s — the gate runs before any data
    query."""
    bogus = uuid.uuid4()
    with TestClient(app) as client:
        response = client.get(f"/crises/{bogus}/public/buildings")
    assert response.status_code == 404, response.text


def test_public_buildings_excludes_hidden_reports() -> None:
    """A report with `public_visible = false` does NOT contribute to the
    pin's `report_count` or `damage_class`. This is the per-report kill
    switch (`reports.public_visible`)."""
    settings = get_settings()
    crisis_id = uuid.uuid4()
    building_id = uuid.uuid4()

    async def _seed_hidden() -> None:
        engine = create_async_engine(settings.database_url)
        try:
            async with engine.begin() as conn:
                await conn.execute(
                    text(
                        "insert into public.crises "
                        "  (id, name, status, public_visibility, heatmap_k_anonymity) "
                        "values (:id, :n, 'active', 'buildings', 1)"
                    ),
                    {
                        "id": str(crisis_id),
                        "n": f"buildings-hidden {uuid.uuid4().hex[:6]}",
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
                # visible: partial. hidden: complete. The pin must
                # surface as `partial`, not `complete`, because the
                # hidden row does not influence the worst-class agg.
                await conn.execute(
                    text(
                        "insert into public.reports "
                        "  (id, crisis_id, damage_class, photo_path, "
                        "   building_id, public_visible, route_description) "
                        # route_description satisfies reports_location_or_route
                        "values (:id, :cid, 'partial', :p, :bid, true, 'test directions')"
                    ),
                    {
                        "id": str(uuid.uuid4()),
                        "cid": str(crisis_id),
                        "p": f"reports/{uuid.uuid4()}.jpg",
                        "bid": str(building_id),
                    },
                )
                await conn.execute(
                    text(
                        "insert into public.reports "
                        "  (id, crisis_id, damage_class, photo_path, "
                        "   building_id, public_visible, route_description) "
                        # route_description satisfies reports_location_or_route
                        "values (:id, :cid, 'complete', :p, :bid, false, 'test directions')"
                    ),
                    {
                        "id": str(uuid.uuid4()),
                        "cid": str(crisis_id),
                        "p": f"reports/{uuid.uuid4()}.jpg",
                        "bid": str(building_id),
                    },
                )
        finally:
            await engine.dispose()

    asyncio.run(_seed_hidden())
    try:
        with TestClient(app) as client:
            response = client.get(f"/crises/{crisis_id}/public/buildings")
        assert response.status_code == 200, response.text
        features = response.json()["features"]
        assert len(features) == 1
        props = features[0]["properties"]
        assert props["report_count"] == 1
        assert props["damage_class"] == "partial"
        assert props["building_id"] == str(building_id)
    finally:
        asyncio.run(_cleanup(settings.database_url, crisis_id=crisis_id, building_id=building_id))


def test_public_buildings_cache_header() -> None:
    """`Cache-Control: public, max-age=15` is set on every 200 response.
    Matches the platform's heat-tile cache TTL."""
    settings = get_settings()
    crisis_id = asyncio.run(_seed_crisis_only(settings.database_url, public_visibility="buildings"))
    try:
        with TestClient(app) as client:
            response = client.get(f"/crises/{crisis_id}/public/buildings")
        assert response.status_code == 200, response.text
        assert response.headers.get("Cache-Control") == "public, max-age=15"
    finally:
        asyncio.run(_cleanup_crisis_only(settings.database_url, crisis_id=crisis_id))


def test_public_buildings_empty_crisis_returns_empty_feature_collection() -> None:
    """A `buildings`-mode crisis with no reports yet returns an empty
    FeatureCollection (not 204). The PWA's renderer wants a parseable
    payload either way."""
    settings = get_settings()
    crisis_id = uuid.uuid4()

    async def _seed_empty() -> None:
        engine = create_async_engine(settings.database_url)
        try:
            async with engine.begin() as conn:
                await conn.execute(
                    text(
                        "insert into public.crises "
                        "  (id, name, status, public_visibility, "
                        "   heatmap_k_anonymity) "
                        "values (:id, :n, 'active', 'buildings', 1)"
                    ),
                    {
                        "id": str(crisis_id),
                        "n": f"buildings-empty {uuid.uuid4().hex[:6]}",
                    },
                )
        finally:
            await engine.dispose()

    async def _cleanup_empty() -> None:
        engine = create_async_engine(settings.database_url)
        try:
            async with engine.begin() as conn:
                await conn.execute(
                    text("delete from public.crises where id = :id"),
                    {"id": str(crisis_id)},
                )
        finally:
            await engine.dispose()

    asyncio.run(_seed_empty())
    try:
        with TestClient(app) as client:
            response = client.get(f"/crises/{crisis_id}/public/buildings")
        assert response.status_code == 200, response.text
        body = response.json()
        assert body == {"type": "FeatureCollection", "features": []}
    finally:
        asyncio.run(_cleanup_empty())
