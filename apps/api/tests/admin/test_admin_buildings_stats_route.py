"""Integration tests for the admin buildings-with-stats read surface.

Covers `GET /admin/crises/{crisis_id}/buildings/stats?bbox=…`:

  - Returns a GeoJSON FeatureCollection of buildings with at least one
    report in the crisis. Each feature carries footprint geometry and
    aggregated stats (per-class counts, latest report's `damage_class`,
    latest `created_at`).
  - Buildings without any report for this crisis are NOT in the
    response (they're shown via the PMTiles underlay instead).
  - Bbox filtering: buildings whose footprint doesn't intersect the
    requested envelope are excluded.
  - 404 when the crisis does not exist.
  - 400 on a malformed bbox.

Requires `supabase start` locally for the Postgres side.
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


# --- Seed helpers --------------------------------------------------------


async def _seed_crisis(engine_url: str) -> uuid.UUID:
    crisis_id = uuid.uuid4()
    suffix = uuid.uuid4().hex[:8]
    engine = create_async_engine(engine_url)
    try:
        async with engine.begin() as conn:
            await conn.execute(
                text(
                    "insert into public.crises "
                    "  (id, name, status, type, countries) "
                    "values "
                    "  (:id, :n, 'active', 'flood', ARRAY['QA']::text[])"
                ),
                {"id": str(crisis_id), "n": f"Admin buildings test {suffix}"},
            )
    finally:
        await engine.dispose()
    return crisis_id


async def _seed_building(
    engine_url: str,
    *,
    centroid: tuple[float, float],  # (lat, lng)
    name: str | None = None,
) -> uuid.UUID:
    building_id = uuid.uuid4()
    source_id = uuid.uuid4().hex
    lat, lng = centroid
    delta = 0.00001
    wkt = (
        "MULTIPOLYGON((("
        f"{lng - delta} {lat - delta},"
        f"{lng + delta} {lat - delta},"
        f"{lng + delta} {lat + delta},"
        f"{lng - delta} {lat + delta},"
        f"{lng - delta} {lat - delta}"
        ")))"
    )
    engine = create_async_engine(engine_url)
    try:
        async with engine.begin() as conn:
            await conn.execute(
                text(
                    "insert into public.buildings "
                    "  (id, source, source_id, footprint, name) "
                    "values "
                    "  (:id, 'test', :src, st_geogfromtext(:wkt), :name)"
                ),
                {"id": str(building_id), "src": source_id, "wkt": wkt, "name": name},
            )
    finally:
        await engine.dispose()
    return building_id


async def _seed_report(
    engine_url: str,
    *,
    crisis_id: uuid.UUID,
    damage_class: str,
    building_id: uuid.UUID | None,
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
        "building_id": str(building_id) if building_id is not None else None,
    }
    if location is not None:
        params["lat"] = location[0]
        params["lng"] = location[1]
    if created_at is not None:
        params["created_at"] = created_at

    engine = create_async_engine(engine_url)
    try:
        async with engine.begin() as conn:
            await conn.execute(
                text(
                    "insert into public.reports "
                    "  (id, crisis_id, damage_class, photo_path, "
                    "   location, building_id, created_at, route_description) "
                    f"values (:id, :crisis_id, :dc, :photo, {location_sql}, "
                    # route_description satisfies reports_location_or_route
                    f"        :building_id, {created_at_sql}, 'test directions')"
                ),
                params,
            )
    finally:
        await engine.dispose()
    return report_id


async def _cleanup(
    engine_url: str,
    *,
    crisis_ids: list[uuid.UUID],
    building_ids: list[uuid.UUID] | None = None,
) -> None:
    engine = create_async_engine(engine_url)
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


# --- Tests ---------------------------------------------------------------


def test_returns_buildings_with_aggregated_stats() -> None:
    settings = get_settings()
    crisis_id = asyncio.run(_seed_crisis(settings.database_url))
    # Building inside bbox with three reports: one minimal, one partial,
    # one complete (the newest). Expect latest_damage_class == "complete".
    building_id = asyncio.run(
        _seed_building(settings.database_url, centroid=(25.30, 51.50), name="Test Tower")
    )
    base_ts = datetime.now(UTC) - timedelta(hours=2)
    asyncio.run(
        _seed_report(
            settings.database_url,
            crisis_id=crisis_id,
            damage_class="minimal",
            building_id=building_id,
            created_at=base_ts,
        )
    )
    asyncio.run(
        _seed_report(
            settings.database_url,
            crisis_id=crisis_id,
            damage_class="partial",
            building_id=building_id,
            created_at=base_ts + timedelta(minutes=10),
        )
    )
    asyncio.run(
        _seed_report(
            settings.database_url,
            crisis_id=crisis_id,
            damage_class="complete",
            building_id=building_id,
            created_at=base_ts + timedelta(minutes=20),
        )
    )

    try:
        with TestClient(app) as client:
            resp = client.post(
                f"/admin/crises/{crisis_id}/buildings/stats",
                json={"bbox": [51.0, 25.0, 52.0, 26.0]},
            )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["type"] == "FeatureCollection"
        assert body["truncated"] is False
        assert len(body["features"]) == 1
        feature = body["features"][0]
        assert feature["geometry"]["type"] == "MultiPolygon"
        props = feature["properties"]
        assert props["building_id"] == str(building_id)
        assert props["name"] == "Test Tower"
        assert props["report_count"] == 3
        assert props["minimal_count"] == 1
        assert props["partial_count"] == 1
        assert props["complete_count"] == 1
        assert props["latest_damage_class"] == "complete"
    finally:
        asyncio.run(
            _cleanup(settings.database_url, crisis_ids=[crisis_id], building_ids=[building_id])
        )


def test_skips_buildings_with_no_reports_in_crisis() -> None:
    settings = get_settings()
    crisis_id = asyncio.run(_seed_crisis(settings.database_url))
    # Building exists but has zero reports in this crisis.
    building_id = asyncio.run(_seed_building(settings.database_url, centroid=(25.30, 51.50)))

    try:
        with TestClient(app) as client:
            resp = client.post(
                f"/admin/crises/{crisis_id}/buildings/stats",
                json={"bbox": [51.0, 25.0, 52.0, 26.0]},
            )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["features"] == []
    finally:
        asyncio.run(
            _cleanup(settings.database_url, crisis_ids=[crisis_id], building_ids=[building_id])
        )


def test_bbox_filters_buildings_outside_viewport() -> None:
    settings = get_settings()
    crisis_id = asyncio.run(_seed_crisis(settings.database_url))
    inside_id = asyncio.run(_seed_building(settings.database_url, centroid=(25.30, 51.50)))
    outside_id = asyncio.run(_seed_building(settings.database_url, centroid=(40.00, 10.00)))
    asyncio.run(
        _seed_report(
            settings.database_url,
            crisis_id=crisis_id,
            damage_class="partial",
            building_id=inside_id,
        )
    )
    asyncio.run(
        _seed_report(
            settings.database_url,
            crisis_id=crisis_id,
            damage_class="complete",
            building_id=outside_id,
        )
    )

    try:
        with TestClient(app) as client:
            resp = client.post(
                f"/admin/crises/{crisis_id}/buildings/stats",
                json={"bbox": [51.0, 25.0, 52.0, 26.0]},
            )
        assert resp.status_code == 200, resp.text
        ids = [f["properties"]["building_id"] for f in resp.json()["features"]]
        assert ids == [str(inside_id)]
    finally:
        asyncio.run(
            _cleanup(
                settings.database_url,
                crisis_ids=[crisis_id],
                building_ids=[inside_id, outside_id],
            )
        )


def test_unknown_crisis_returns_404() -> None:
    with TestClient(app) as client:
        resp = client.post(
            f"/admin/crises/{uuid.uuid4()}/buildings/stats",
            json={"bbox": [0.0, 0.0, 1.0, 1.0]},
        )
    assert resp.status_code == 404


def test_malformed_bbox_returns_422() -> None:
    # The bbox is now a typed 4-tuple in the request body, so a wrong-length
    # bbox is a Pydantic validation error (422), not the old query-param 400.
    with TestClient(app) as client:
        resp = client.post(
            f"/admin/crises/{uuid.uuid4()}/buildings/stats",
            json={"bbox": [0.0, 0.0]},
        )
    assert resp.status_code == 422


def test_damage_class_filter_narrows_to_matching_reports() -> None:
    """The buildings surface is filter-aware: a damage_class
    filter restricts each building's counts to matching reports, and a
    building with no matching report drops out entirely."""
    settings = get_settings()
    crisis_id = asyncio.run(_seed_crisis(settings.database_url))
    # Tower A: one complete + one minimal report. Tower B: minimal only.
    tower_a = asyncio.run(
        _seed_building(settings.database_url, centroid=(25.30, 51.50), name="Tower A")
    )
    tower_b = asyncio.run(
        _seed_building(settings.database_url, centroid=(25.31, 51.51), name="Tower B")
    )
    asyncio.run(
        _seed_report(
            settings.database_url, crisis_id=crisis_id, damage_class="complete", building_id=tower_a
        )
    )
    asyncio.run(
        _seed_report(
            settings.database_url, crisis_id=crisis_id, damage_class="minimal", building_id=tower_a
        )
    )
    asyncio.run(
        _seed_report(
            settings.database_url, crisis_id=crisis_id, damage_class="minimal", building_id=tower_b
        )
    )
    try:
        with TestClient(app) as client:
            # Unfiltered: both towers present.
            allf = client.post(
                f"/admin/crises/{crisis_id}/buildings/stats",
                json={"bbox": [51.0, 25.0, 52.0, 26.0]},
            )
            # damage_class=complete: only Tower A, and its count is just the
            # one matching report.
            filtered = client.post(
                f"/admin/crises/{crisis_id}/buildings/stats",
                json={"bbox": [51.0, 25.0, 52.0, 26.0], "damage_class": "complete"},
            )
        assert allf.status_code == 200, allf.text
        assert {f["properties"]["name"] for f in allf.json()["features"]} == {"Tower A", "Tower B"}

        assert filtered.status_code == 200, filtered.text
        feats = filtered.json()["features"]
        assert len(feats) == 1
        assert feats[0]["properties"]["name"] == "Tower A"
        assert feats[0]["properties"]["report_count"] == 1
        assert feats[0]["properties"]["complete_count"] == 1
        assert feats[0]["properties"]["minimal_count"] == 0
        assert feats[0]["properties"]["latest_damage_class"] == "complete"
    finally:
        asyncio.run(
            _cleanup(
                settings.database_url,
                crisis_ids=[crisis_id],
                building_ids=[tower_a, tower_b],
            )
        )
