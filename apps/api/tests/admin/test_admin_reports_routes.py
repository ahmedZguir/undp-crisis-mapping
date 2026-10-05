"""Integration tests for the admin reports read surface.

Covers both endpoints:

- `GET /admin/crises/{crisis_id}/reports` — list mode (cursor) and map
  mode (bbox), including pagination correctness under live inserts,
  bbox filtering, truncation, NULL-location handling, building-centroid
  fallback, the damage_class filter, the bbox/cursor mutual-exclusion
  400, and 404 on unknown crisis.
- `GET /admin/reports/{report_id}` — full row + a signed photo URL,
  minted via a `PhotoUrlSigner` stub so the test doesn't depend on the
  Supabase Storage backend.

Requires `supabase start` locally for the Postgres side; the storage
side is stubbed.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from api.admin.report_routes import get_photo_url_signer
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
                {"id": str(crisis_id), "n": f"Admin reports test {suffix}"},
            )
    finally:
        await engine.dispose()
    return crisis_id


async def _seed_report(
    engine_url: str,
    *,
    crisis_id: uuid.UUID,
    damage_class: str = "partial",
    location: tuple[float, float] | None = (25.30, 51.50),  # (lat, lng)
    building_id: uuid.UUID | None = None,
    created_at: datetime | None = None,
    photo_path: str | None = None,
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
        "photo": photo_path or f"test/{report_id}.jpg",
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
                    # route_description satisfies `reports_location_or_route`
                    # for the NULL-location rows these tests rely on; it has no
                    # effect on map_point.
                    f"        :building_id, {created_at_sql}, 'test directions')"
                ),
                params,
            )
    finally:
        await engine.dispose()
    return report_id


async def _seed_building(
    engine_url: str,
    *,
    centroid: tuple[float, float],  # (lat, lng)
) -> uuid.UUID:
    building_id = uuid.uuid4()
    source_id = uuid.uuid4().hex
    lat, lng = centroid
    # Tiny 1m-ish square around the requested centroid so the generated
    # `centroid` column lands where the test expects.
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
                    "  (id, source, source_id, footprint) "
                    "values "
                    "  (:id, 'test', :src, st_geogfromtext(:wkt))"
                ),
                {"id": str(building_id), "src": source_id, "wkt": wkt},
            )
    finally:
        await engine.dispose()
    return building_id


async def _seed_geocode(
    engine_url: str,
    *,
    report_id: uuid.UUID,
    lat: float,
    lon: float,
    radius_m: float | None = 120.0,
    area_only: bool = False,
    confidence: float | None = 0.7,
) -> None:
    """Attach an AI-geocode row to an existing report.

    Cascades away with the report on cleanup (FK `on delete cascade`), so
    no separate teardown is needed.
    """
    engine = create_async_engine(engine_url)
    try:
        async with engine.begin() as conn:
            await conn.execute(
                text(
                    "insert into public.report_geocodes "
                    "  (report_id, lat, lon, radius_m, area_only, confidence, status) "
                    "values (:rid, :lat, :lon, :radius_m, :area_only, :confidence, 'ready')"
                ),
                {
                    "rid": str(report_id),
                    "lat": lat,
                    "lon": lon,
                    "radius_m": radius_m,
                    "area_only": area_only,
                    "confidence": confidence,
                },
            )
    finally:
        await engine.dispose()


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


# --- Fakes ---------------------------------------------------------------


class _FakeSigner:
    """Stub `PhotoUrlSigner` so detail tests don't hit Supabase Storage."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, int]] = []

    async def sign_photo_url(self, photo_path: str, ttl_seconds: int) -> str:
        self.calls.append((photo_path, ttl_seconds))
        return f"https://signed.test/{photo_path}?ttl={ttl_seconds}"


# --- Tests: list mode (cursor) -------------------------------------------


def test_list_returns_page_in_recency_order() -> None:
    settings = get_settings()
    base_ts = datetime.now(UTC) - timedelta(hours=1)
    crisis_id = asyncio.run(_seed_crisis(settings.database_url))
    older = asyncio.run(
        _seed_report(settings.database_url, crisis_id=crisis_id, created_at=base_ts)
    )
    middle = asyncio.run(
        _seed_report(
            settings.database_url, crisis_id=crisis_id, created_at=base_ts + timedelta(minutes=1)
        )
    )
    newer = asyncio.run(
        _seed_report(
            settings.database_url, crisis_id=crisis_id, created_at=base_ts + timedelta(minutes=2)
        )
    )

    try:
        with TestClient(app) as client:
            response = client.get(f"/admin/crises/{crisis_id}/reports")
        assert response.status_code == 200, response.text
        body = response.json()
        ids = [item["id"] for item in body["items"]]
        assert ids == [str(newer), str(middle), str(older)]
        assert body["next_cursor"] is None
        assert body["truncated"] is False
        assert body["total_in_bbox"] is None
    finally:
        asyncio.run(_cleanup(settings.database_url, crisis_ids=[crisis_id]))


def test_list_cursor_pagination_is_stable_under_insert() -> None:
    """A new report inserted mid-scroll must not duplicate or skip pages."""
    settings = get_settings()
    base_ts = datetime.now(UTC) - timedelta(hours=1)
    crisis_id = asyncio.run(_seed_crisis(settings.database_url))

    # Seed five reports with increasing created_at.
    seeded = [
        asyncio.run(
            _seed_report(
                settings.database_url,
                crisis_id=crisis_id,
                created_at=base_ts + timedelta(minutes=i),
            )
        )
        for i in range(5)
    ]
    # seeded[4] is newest; seeded[0] is oldest.

    try:
        with TestClient(app) as client:
            first = client.get(f"/admin/crises/{crisis_id}/reports?limit=3").json()
            assert [r["id"] for r in first["items"]] == [
                str(seeded[4]),
                str(seeded[3]),
                str(seeded[2]),
            ]
            cursor = first["next_cursor"]
            assert cursor is not None

            # Insert a new (latest) report between page loads.
            asyncio.run(
                _seed_report(
                    settings.database_url,
                    crisis_id=crisis_id,
                    created_at=base_ts + timedelta(minutes=10),
                )
            )

            second = client.get(f"/admin/crises/{crisis_id}/reports?limit=3&cursor={cursor}").json()
        # Page 2 must be the two older reports — no duplicate of seeded[2],
        # and the newly-inserted top row stays above the cursor.
        assert [r["id"] for r in second["items"]] == [str(seeded[1]), str(seeded[0])]
        assert second["next_cursor"] is None
    finally:
        asyncio.run(_cleanup(settings.database_url, crisis_ids=[crisis_id]))


def test_list_includes_null_location_rows() -> None:
    settings = get_settings()
    crisis_id = asyncio.run(_seed_crisis(settings.database_url))
    report_id = asyncio.run(_seed_report(settings.database_url, crisis_id=crisis_id, location=None))

    try:
        with TestClient(app) as client:
            body = client.get(f"/admin/crises/{crisis_id}/reports").json()
        items = {item["id"]: item for item in body["items"]}
        assert str(report_id) in items
        assert items[str(report_id)]["location"] is None
        assert items[str(report_id)]["map_point"] is None
    finally:
        asyncio.run(_cleanup(settings.database_url, crisis_ids=[crisis_id]))


def test_list_filters_by_damage_class() -> None:
    settings = get_settings()
    crisis_id = asyncio.run(_seed_crisis(settings.database_url))
    minimal_id = asyncio.run(
        _seed_report(settings.database_url, crisis_id=crisis_id, damage_class="minimal")
    )
    complete_id = asyncio.run(
        _seed_report(settings.database_url, crisis_id=crisis_id, damage_class="complete")
    )

    try:
        with TestClient(app) as client:
            body = client.get(f"/admin/crises/{crisis_id}/reports?damage_class=complete").json()
        ids = [item["id"] for item in body["items"]]
        assert str(complete_id) in ids
        assert str(minimal_id) not in ids
    finally:
        asyncio.run(_cleanup(settings.database_url, crisis_ids=[crisis_id]))


def test_list_returns_400_for_invalid_damage_class() -> None:
    settings = get_settings()
    crisis_id = asyncio.run(_seed_crisis(settings.database_url))
    try:
        with TestClient(app) as client:
            response = client.get(f"/admin/crises/{crisis_id}/reports?damage_class=severe")
        assert response.status_code == 400
    finally:
        asyncio.run(_cleanup(settings.database_url, crisis_ids=[crisis_id]))


def test_list_returns_404_for_unknown_crisis() -> None:
    unknown = uuid.uuid4()
    with TestClient(app) as client:
        response = client.get(f"/admin/crises/{unknown}/reports")
    assert response.status_code == 404


def test_list_rejects_bbox_and_cursor_together() -> None:
    settings = get_settings()
    crisis_id = asyncio.run(_seed_crisis(settings.database_url))
    try:
        with TestClient(app) as client:
            response = client.get(
                f"/admin/crises/{crisis_id}/reports?bbox=51,25,52,26&cursor=anyvalue"
            )
        assert response.status_code == 400
    finally:
        asyncio.run(_cleanup(settings.database_url, crisis_ids=[crisis_id]))


def test_list_rejects_invalid_cursor() -> None:
    settings = get_settings()
    crisis_id = asyncio.run(_seed_crisis(settings.database_url))
    try:
        with TestClient(app) as client:
            response = client.get(f"/admin/crises/{crisis_id}/reports?cursor=!!!not-base64!!!")
        assert response.status_code == 400
    finally:
        asyncio.run(_cleanup(settings.database_url, crisis_ids=[crisis_id]))


# --- Tests: map mode (bbox) ----------------------------------------------


def test_bbox_returns_only_intersecting_points() -> None:
    settings = get_settings()
    crisis_id = asyncio.run(_seed_crisis(settings.database_url))
    inside = asyncio.run(
        _seed_report(settings.database_url, crisis_id=crisis_id, location=(25.30, 51.50))
    )
    outside = asyncio.run(
        _seed_report(settings.database_url, crisis_id=crisis_id, location=(40.00, 10.00))
    )

    try:
        with TestClient(app) as client:
            body = client.get(f"/admin/crises/{crisis_id}/reports?bbox=51.0,25.0,52.0,26.0").json()
        ids = [item["id"] for item in body["items"]]
        assert str(inside) in ids
        assert str(outside) not in ids
        assert body["truncated"] is False
        assert body["total_in_bbox"] == 1
        assert body["next_cursor"] is None
    finally:
        asyncio.run(_cleanup(settings.database_url, crisis_ids=[crisis_id]))


def test_bbox_excludes_null_location_when_no_building() -> None:
    settings = get_settings()
    crisis_id = asyncio.run(_seed_crisis(settings.database_url))
    asyncio.run(_seed_report(settings.database_url, crisis_id=crisis_id, location=None))

    try:
        with TestClient(app) as client:
            body = client.get(f"/admin/crises/{crisis_id}/reports?bbox=51.0,25.0,52.0,26.0").json()
        assert body["items"] == []
        assert body["total_in_bbox"] == 0
    finally:
        asyncio.run(_cleanup(settings.database_url, crisis_ids=[crisis_id]))


def test_bbox_falls_back_to_building_centroid() -> None:
    """A report with NULL location but a known building plots at the building."""
    settings = get_settings()
    crisis_id = asyncio.run(_seed_crisis(settings.database_url))
    building_id = asyncio.run(_seed_building(settings.database_url, centroid=(25.30, 51.50)))
    report_id = asyncio.run(
        _seed_report(
            settings.database_url,
            crisis_id=crisis_id,
            location=None,
            building_id=building_id,
        )
    )

    try:
        with TestClient(app) as client:
            body = client.get(f"/admin/crises/{crisis_id}/reports?bbox=51.0,25.0,52.0,26.0").json()
        ids = [item["id"] for item in body["items"]]
        assert str(report_id) in ids
        item = next(i for i in body["items"] if i["id"] == str(report_id))
        assert item["location"] is None
        assert item["map_point"] is not None
        assert abs(item["map_point"]["lat"] - 25.30) < 1e-3
        assert abs(item["map_point"]["lng"] - 51.50) < 1e-3
    finally:
        asyncio.run(
            _cleanup(
                settings.database_url,
                crisis_ids=[crisis_id],
                building_ids=[building_id],
            )
        )


def test_bbox_falls_back_to_ai_geocode() -> None:
    """A report with no GPS and no building plots at its AI geocode.

    Guards the position-precedence tail (`coalesce(location, building,
    geocode)`): the geocode lon column is aliased `geocode_lon`, so the row
    projector must read that exact name — a `geocode_lng` typo silently
    nulled `map_point` and dropped every AI-located point from the map.
    """
    settings = get_settings()
    crisis_id = asyncio.run(_seed_crisis(settings.database_url))
    report_id = asyncio.run(_seed_report(settings.database_url, crisis_id=crisis_id, location=None))
    asyncio.run(
        _seed_geocode(
            settings.database_url,
            report_id=report_id,
            lat=25.30,
            lon=51.50,
            radius_m=140.0,
            area_only=True,
            confidence=0.65,
        )
    )

    try:
        with TestClient(app) as client:
            body = client.get(f"/admin/crises/{crisis_id}/reports?bbox=51.0,25.0,52.0,26.0").json()
        ids = [item["id"] for item in body["items"]]
        assert str(report_id) in ids
        item = next(i for i in body["items"] if i["id"] == str(report_id))
        assert item["location"] is None
        assert item["map_point"] is not None
        assert abs(item["map_point"]["lat"] - 25.30) < 1e-3
        assert abs(item["map_point"]["lng"] - 51.50) < 1e-3
        assert item["location_source"] == "ai_geocode"
        # Uncertainty metadata rides along only for the geocode source.
        assert abs(item["location_radius_m"] - 140.0) < 1e-3
        assert item["location_area_only"] is True
        assert abs(item["location_confidence"] - 0.65) < 1e-3
    finally:
        asyncio.run(_cleanup(settings.database_url, crisis_ids=[crisis_id]))


def test_bbox_truncation_flag_and_total() -> None:
    settings = get_settings()
    crisis_id = asyncio.run(_seed_crisis(settings.database_url))
    for _ in range(6):
        asyncio.run(
            _seed_report(settings.database_url, crisis_id=crisis_id, location=(25.30, 51.50))
        )

    try:
        with TestClient(app) as client:
            body = client.get(
                f"/admin/crises/{crisis_id}/reports?bbox=51.0,25.0,52.0,26.0&limit=4"
            ).json()
        assert len(body["items"]) == 4
        assert body["total_in_bbox"] == 6
        assert body["truncated"] is True
    finally:
        asyncio.run(_cleanup(settings.database_url, crisis_ids=[crisis_id]))


def test_bbox_invalid_shape_returns_400() -> None:
    settings = get_settings()
    crisis_id = asyncio.run(_seed_crisis(settings.database_url))
    try:
        with TestClient(app) as client:
            # Three values, not four.
            r1 = client.get(f"/admin/crises/{crisis_id}/reports?bbox=51,25,52")
            # West >= east.
            r2 = client.get(f"/admin/crises/{crisis_id}/reports?bbox=52,25,51,26")
            # Longitude out of range.
            r3 = client.get(f"/admin/crises/{crisis_id}/reports?bbox=-200,25,52,26")
        assert r1.status_code == 400
        assert r2.status_code == 400
        assert r3.status_code == 400
    finally:
        asyncio.run(_cleanup(settings.database_url, crisis_ids=[crisis_id]))


# --- Tests: detail endpoint ---------------------------------------------


def test_detail_returns_full_row_and_signed_url() -> None:
    settings = get_settings()
    crisis_id = asyncio.run(_seed_crisis(settings.database_url))
    report_id = asyncio.run(
        _seed_report(settings.database_url, crisis_id=crisis_id, location=(25.30, 51.50))
    )

    fake = _FakeSigner()
    app.dependency_overrides[get_photo_url_signer] = lambda: fake
    try:
        with TestClient(app) as client:
            response = client.get(f"/admin/reports/{report_id}")
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["id"] == str(report_id)
        assert body["photo_url"].startswith("https://signed.test/")
        assert body["location"] is not None
        assert abs(body["location"]["lat"] - 25.30) < 1e-3
        assert abs(body["location"]["lng"] - 51.50) < 1e-3
        # Signer was called with the 15-minute TTL the handler enforces.
        assert fake.calls and fake.calls[0][1] == 15 * 60
    finally:
        app.dependency_overrides.pop(get_photo_url_signer, None)
        asyncio.run(_cleanup(settings.database_url, crisis_ids=[crisis_id]))


def test_detail_includes_building_centroid() -> None:
    settings = get_settings()
    crisis_id = asyncio.run(_seed_crisis(settings.database_url))
    building_id = asyncio.run(_seed_building(settings.database_url, centroid=(25.30, 51.50)))
    report_id = asyncio.run(
        _seed_report(
            settings.database_url,
            crisis_id=crisis_id,
            location=None,
            building_id=building_id,
        )
    )

    app.dependency_overrides[get_photo_url_signer] = lambda: _FakeSigner()
    try:
        with TestClient(app) as client:
            body = client.get(f"/admin/reports/{report_id}").json()
        assert body["location"] is None
        assert body["building_centroid"] is not None
        assert abs(body["building_centroid"]["lat"] - 25.30) < 1e-3
        assert abs(body["building_centroid"]["lng"] - 51.50) < 1e-3
    finally:
        app.dependency_overrides.pop(get_photo_url_signer, None)
        asyncio.run(
            _cleanup(
                settings.database_url,
                crisis_ids=[crisis_id],
                building_ids=[building_id],
            )
        )


def test_detail_returns_404_for_unknown_report() -> None:
    app.dependency_overrides[get_photo_url_signer] = lambda: _FakeSigner()
    try:
        with TestClient(app) as client:
            response = client.get(f"/admin/reports/{uuid.uuid4()}")
        assert response.status_code == 404
    finally:
        app.dependency_overrides.pop(get_photo_url_signer, None)
