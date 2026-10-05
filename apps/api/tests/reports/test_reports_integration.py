"""End-to-end integration tests for POST /reports.

Exercises the full spine: FastAPI route → PhotoValidator → CrisisService.require_active
→ Supabase Storage upload → Postgres insert. Requires `supabase start` to be
running locally; configuration is loaded from `.env` via Settings.
"""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from api.core.config import get_settings
from api.main import app

FIXTURE = Path(__file__).parent.parent / "fixtures" / "tiny.jpg"


pytestmark = pytest.mark.integration


def _seed_crisis(status: str = "active") -> uuid.UUID:
    """Insert a fresh non-reserved crisis row, return its UUID."""
    settings = get_settings()
    crisis_id = uuid.uuid4()
    suffix = uuid.uuid4().hex[:8]

    async def _run() -> None:
        engine = create_async_engine(settings.database_url)
        try:
            async with engine.begin() as conn:
                await conn.execute(
                    text(
                        "insert into public.crises (id, name, status, created_at) "
                        "values (:id, :n, :s, :t)"
                    ),
                    {
                        "id": str(crisis_id),
                        "n": f"Test crisis {suffix}",
                        "s": status,
                        "t": datetime.now(UTC) - timedelta(minutes=1),
                    },
                )
        finally:
            await engine.dispose()

    asyncio.run(_run())
    return crisis_id


def _delete_crisis(crisis_id: uuid.UUID) -> None:
    settings = get_settings()

    async def _run() -> None:
        engine = create_async_engine(settings.database_url)
        try:
            async with engine.begin() as conn:
                await conn.execute(
                    text("delete from public.crises where id = :id"),
                    {"id": str(crisis_id)},
                )
        finally:
            await engine.dispose()

    asyncio.run(_run())


def _count_reports_for(crisis_id: uuid.UUID) -> int:
    settings = get_settings()

    async def _run() -> int:
        engine = create_async_engine(settings.database_url)
        try:
            async with engine.connect() as conn:
                result = await conn.execute(
                    text("select count(*) from public.reports where crisis_id = :id"),
                    {"id": str(crisis_id)},
                )
                count = result.scalar_one()
                return int(count) if count is not None else 0
        finally:
            await engine.dispose()

    return asyncio.run(_run())


def test_post_reports_happy_path() -> None:
    settings = get_settings()
    crisis_id = _seed_crisis(status="active")
    try:
        with TestClient(app) as client:
            payload = {
                "crisis_id": str(crisis_id),
                "damage_class": "partial",
                "description": "Wall crack on south face",
                "location": {"lat": 25.2854, "lng": 51.5310},
                "client_id": str(uuid.uuid4()),
            }
            files = {
                "photo": ("tiny.jpg", FIXTURE.read_bytes(), "image/jpeg"),
                "data": (None, json.dumps(payload), "application/json"),
            }

            response = client.post("/reports", files=files)

            assert response.status_code == 200, response.text
            body = response.json()
            assert {"id", "crisis_id", "created_at"} <= set(body.keys())
            assert body["damage_class"] == "partial"
            assert body["description"] == "Wall crack on south face"
            assert body["location"] == {"lat": 25.2854, "lng": 51.5310}
            # Fields not sent are echoed as null.
            assert body["infra_type"] is None
            assert body["debris"] is None
            # Server bound to the supplied crisis_id, not the reserved row.
            assert body["crisis_id"] == str(crisis_id)
            report_id = uuid.UUID(body["id"])

            async def _verify_side_effects() -> None:
                engine = create_async_engine(settings.database_url)
                try:
                    async with engine.connect() as conn:
                        row = (
                            await conn.execute(
                                text(
                                    "select crisis_id, damage_class, photo_path "
                                    "from public.reports where id = :id"
                                ),
                                {"id": str(report_id)},
                            )
                        ).one()
                        assert row.crisis_id == crisis_id
                        assert row.damage_class == "partial"
                        photo_path = row.photo_path
                finally:
                    await engine.dispose()

                # Storage object exists — service-role HEAD on the private bucket path.
                url = (
                    f"{settings.supabase_url}/storage/v1/object/"
                    f"{settings.supabase_storage_bucket}/{photo_path}"
                )
                headers = {"Authorization": f"Bearer {settings.supabase_service_role_key}"}
                async with httpx.AsyncClient() as http:
                    head = await http.get(url, headers=headers)
                assert head.status_code == 200, head.text

            asyncio.run(_verify_side_effects())
    finally:
        # Clean up the report row first so the FK lets us delete the crisis.
        async def _cleanup_reports() -> None:
            engine = create_async_engine(settings.database_url)
            try:
                async with engine.begin() as conn:
                    await conn.execute(
                        text("delete from public.reports where crisis_id = :id"),
                        {"id": str(crisis_id)},
                    )
            finally:
                await engine.dispose()

        asyncio.run(_cleanup_reports())
        _delete_crisis(crisis_id)


def test_post_reports_persists_expanded_fields() -> None:
    settings = get_settings()
    crisis_id = _seed_crisis(status="active")
    try:
        with TestClient(app) as client:
            payload = {
                "crisis_id": str(crisis_id),
                "damage_class": "partial",
                "description": "central span sagging",
                "location": {"lat": 33.5138, "lng": 36.2765},
                "client_id": str(uuid.uuid4()),
                "infra_type": ["bridge", "transport"],
                "infra_name": "Main St crossing",
                "crisis_type": "natural_hazards",
                "crisis_type_detailed": "earthquake",
                "debris": "yes",
            }
            files = {
                "photo": ("tiny.jpg", FIXTURE.read_bytes(), "image/jpeg"),
                "data": (None, json.dumps(payload), "application/json"),
            }

            response = client.post("/reports", files=files)

            assert response.status_code == 200, response.text
            body = response.json()

            assert body["damage_class"] == "partial"
            assert body["description"] == "central span sagging"
            assert body["location"] == {"lat": 33.5138, "lng": 36.2765}
            assert body["infra_type"] == ["bridge", "transport"]
            assert body["infra_name"] == "Main St crossing"
            assert body["crisis_type"] == "natural_hazards"
            assert body["crisis_type_detailed"] == "earthquake"
            assert body["debris"] == "yes"

            report_id = uuid.UUID(body["id"])

            async def _verify_row() -> None:
                engine = create_async_engine(settings.database_url)
                try:
                    async with engine.connect() as conn:
                        row = (
                            await conn.execute(
                                text(
                                    "select infra_type, infra_name, "
                                    "crisis_type, crisis_type_detailed, debris "
                                    "from public.reports where id = :id"
                                ),
                                {"id": str(report_id)},
                            )
                        ).one()
                        assert row.infra_type == ["bridge", "transport"]
                        assert row.infra_name == "Main St crossing"
                        assert row.crisis_type == "natural_hazards"
                        assert row.crisis_type_detailed == "earthquake"
                        assert row.debris == "yes"
                finally:
                    await engine.dispose()

            asyncio.run(_verify_row())
    finally:

        async def _cleanup_reports() -> None:
            engine = create_async_engine(settings.database_url)
            try:
                async with engine.begin() as conn:
                    await conn.execute(
                        text("delete from public.reports where crisis_id = :id"),
                        {"id": str(crisis_id)},
                    )
            finally:
                await engine.dispose()

        asyncio.run(_cleanup_reports())
        _delete_crisis(crisis_id)


def test_post_reports_with_archived_crisis_returns_409() -> None:
    crisis_id = _seed_crisis(status="archived")
    try:
        with TestClient(app) as client:
            payload = {
                "crisis_id": str(crisis_id),
                "damage_class": "minimal",
                "location": {"lat": 33.5, "lng": 36.3},
            }
            files = {
                "photo": ("tiny.jpg", FIXTURE.read_bytes(), "image/jpeg"),
                "data": (None, json.dumps(payload), "application/json"),
            }

            response = client.post("/reports", files=files)

            assert response.status_code == 409, response.text
            assert response.json() == {"detail": "crisis_archived_or_unknown"}
            # No row inserted, and no upload happened (validation runs before upload).
            assert _count_reports_for(crisis_id) == 0
    finally:
        _delete_crisis(crisis_id)


def test_post_reports_with_unknown_crisis_returns_409() -> None:
    bogus = uuid.uuid4()
    with TestClient(app) as client:
        payload = {
            "crisis_id": str(bogus),
            "damage_class": "minimal",
            "location": {"lat": 33.5, "lng": 36.3},
        }
        files = {
            "photo": ("tiny.jpg", FIXTURE.read_bytes(), "image/jpeg"),
            "data": (None, json.dumps(payload), "application/json"),
        }

        response = client.post("/reports", files=files)

        assert response.status_code == 409, response.text
        assert response.json() == {"detail": "crisis_archived_or_unknown"}
        assert _count_reports_for(bogus) == 0


# ---- building_id resolution at submit ----------------------------------


_BUILDING_FOOTPRINT_WKT = (
    "SRID=4326;MULTIPOLYGON(((10.0 10.0,10.001 10.0,10.001 10.001,10.0 10.001,10.0 10.0)))"
)


def _seed_overture_building(source_id: str) -> uuid.UUID:
    settings = get_settings()
    building_id = uuid.uuid4()

    async def _run() -> None:
        engine = create_async_engine(settings.database_url)
        try:
            async with engine.begin() as conn:
                await conn.execute(
                    text(
                        "insert into public.buildings (id, source, source_id, footprint) "
                        "values (:id, 'overture', :sid, st_geogfromtext(:wkt))"
                    ),
                    {
                        "id": str(building_id),
                        "sid": source_id,
                        "wkt": _BUILDING_FOOTPRINT_WKT,
                    },
                )
        finally:
            await engine.dispose()

    asyncio.run(_run())
    return building_id


def _delete_building(building_id: uuid.UUID) -> None:
    settings = get_settings()

    async def _run() -> None:
        engine = create_async_engine(settings.database_url)
        try:
            async with engine.begin() as conn:
                await conn.execute(
                    text("delete from public.buildings where id = :id"),
                    {"id": str(building_id)},
                )
        finally:
            await engine.dispose()

    asyncio.run(_run())


def _read_building_id_for_report(report_id: uuid.UUID) -> uuid.UUID | None:
    settings = get_settings()

    async def _run() -> uuid.UUID | None:
        engine = create_async_engine(settings.database_url)
        try:
            async with engine.connect() as conn:
                row = (
                    await conn.execute(
                        text("select building_id from public.reports where id = :id"),
                        {"id": str(report_id)},
                    )
                ).one()
                if row.building_id is None:
                    return None
                return uuid.UUID(str(row.building_id))
        finally:
            await engine.dispose()

    return asyncio.run(_run())


def test_post_reports_resolves_known_gers_to_building_uuid() -> None:
    crisis_id = _seed_crisis(status="active")
    gers = f"gers-{uuid.uuid4().hex}"
    seeded_building_id = _seed_overture_building(gers)
    report_id: uuid.UUID | None = None
    try:
        with TestClient(app) as client:
            payload = {
                "crisis_id": str(crisis_id),
                "damage_class": "minimal",
                "building_id": gers,
                # A building tap carries a map location; required to satisfy the
                # minimum-content gate (location OR route). Does not affect GERS
                # resolution.
                "location": {"lat": 10.0005, "lng": 10.0005},
            }
            files = {
                "photo": ("tiny.jpg", FIXTURE.read_bytes(), "image/jpeg"),
                "data": (None, json.dumps(payload), "application/json"),
            }

            response = client.post("/reports", files=files)

            assert response.status_code == 200, response.text
            body = response.json()
            assert body["building_id"] == str(seeded_building_id)
            report_id = uuid.UUID(body["id"])
            assert _read_building_id_for_report(report_id) == seeded_building_id
    finally:

        async def _cleanup_reports() -> None:
            settings = get_settings()
            engine = create_async_engine(settings.database_url)
            try:
                async with engine.begin() as conn:
                    await conn.execute(
                        text("delete from public.reports where crisis_id = :id"),
                        {"id": str(crisis_id)},
                    )
            finally:
                await engine.dispose()

        asyncio.run(_cleanup_reports())
        _delete_building(seeded_building_id)
        _delete_crisis(crisis_id)


def test_post_reports_with_unknown_gers_stores_null_and_logs_warning(
    caplog: pytest.LogCaptureFixture,
) -> None:
    crisis_id = _seed_crisis(status="active")
    bogus_gers = f"unknown-gers-{uuid.uuid4().hex}"
    try:
        with TestClient(app) as client:
            payload = {
                "crisis_id": str(crisis_id),
                "damage_class": "minimal",
                "building_id": bogus_gers,
                # Satisfies the minimum-content gate; an explicit (even unknown)
                # building_id still suppresses snap, so building_id stays null.
                "location": {"lat": 25.0, "lng": 51.0},
            }
            files = {
                "photo": ("tiny.jpg", FIXTURE.read_bytes(), "image/jpeg"),
                "data": (None, json.dumps(payload), "application/json"),
            }

            with caplog.at_level(logging.WARNING):
                response = client.post("/reports", files=files)

            assert response.status_code == 200, response.text
            body = response.json()
            assert body["building_id"] is None
            report_id = uuid.UUID(body["id"])
            assert _read_building_id_for_report(report_id) is None

            # Structured warning emitted with the offending GERS string.
            warnings = [
                r
                for r in caplog.records
                if r.levelno == logging.WARNING
                and getattr(r, "event", None) == "report.building_id.unknown_gers"
            ]
            assert warnings, f"no unknown_gers warning logged; got {caplog.records!r}"
            assert getattr(warnings[0], "source_id", None) == bogus_gers
    finally:

        async def _cleanup_reports() -> None:
            settings = get_settings()
            engine = create_async_engine(settings.database_url)
            try:
                async with engine.begin() as conn:
                    await conn.execute(
                        text("delete from public.reports where crisis_id = :id"),
                        {"id": str(crisis_id)},
                    )
            finally:
                await engine.dispose()

        asyncio.run(_cleanup_reports())
        _delete_crisis(crisis_id)


# ---- snap-to-nearest fallback at submit --------------------------------


def _seed_overture_building_at(lat: float, lng: float) -> uuid.UUID:
    """Seed an Overture building whose centroid sits exactly at (lat, lng)."""
    settings = get_settings()
    building_id = uuid.uuid4()
    source_id = f"gers-snap-{uuid.uuid4().hex}"
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

    async def _run() -> None:
        engine = create_async_engine(settings.database_url)
        try:
            async with engine.begin() as conn:
                await conn.execute(
                    text(
                        "insert into public.buildings "
                        "(id, source, source_id, footprint) "
                        "values (:id, 'overture', :sid, st_geogfromtext(:wkt))"
                    ),
                    {"id": str(building_id), "sid": source_id, "wkt": wkt},
                )
        finally:
            await engine.dispose()

    asyncio.run(_run())
    return building_id


def test_post_reports_snaps_location_to_nearest_building_when_no_building_id() -> None:
    """No `building_id`, location ~10 m from a seeded centroid → bind to it."""
    crisis_id = _seed_crisis(status="active")
    # Centroid at (33.5, 36.3); probe ~10 m north at lat 33.5.
    centroid_lat, centroid_lng = 33.5, 36.3
    seeded_building_id = _seed_overture_building_at(centroid_lat, centroid_lng)
    probe_lat = centroid_lat + (10.0 / 110_574.0)
    try:
        with TestClient(app) as client:
            payload = {
                "crisis_id": str(crisis_id),
                "damage_class": "minimal",
                "location": {"lat": probe_lat, "lng": centroid_lng},
            }
            files = {
                "photo": ("tiny.jpg", FIXTURE.read_bytes(), "image/jpeg"),
                "data": (None, json.dumps(payload), "application/json"),
            }

            response = client.post("/reports", files=files)

            assert response.status_code == 200, response.text
            body = response.json()
            assert body["building_id"] == str(seeded_building_id)
            report_id = uuid.UUID(body["id"])
            assert _read_building_id_for_report(report_id) == seeded_building_id
    finally:

        async def _cleanup_reports() -> None:
            settings = get_settings()
            engine = create_async_engine(settings.database_url)
            try:
                async with engine.begin() as conn:
                    await conn.execute(
                        text("delete from public.reports where crisis_id = :id"),
                        {"id": str(crisis_id)},
                    )
            finally:
                await engine.dispose()

        asyncio.run(_cleanup_reports())
        _delete_building(seeded_building_id)
        _delete_crisis(crisis_id)


def test_post_reports_snap_miss_stores_null_building_id() -> None:
    """No `building_id`, location far from any centroid → building_id is null."""
    crisis_id = _seed_crisis(status="active")
    # Seed a building far from the probe so the result isn't an empty-DB artifact.
    seeded_building_id = _seed_overture_building_at(33.5, 36.3)
    try:
        with TestClient(app) as client:
            payload = {
                "crisis_id": str(crisis_id),
                "damage_class": "minimal",
                # ~10 degrees away from the seeded centroid — well beyond 50 m.
                "location": {"lat": 23.5, "lng": 26.3},
            }
            files = {
                "photo": ("tiny.jpg", FIXTURE.read_bytes(), "image/jpeg"),
                "data": (None, json.dumps(payload), "application/json"),
            }

            response = client.post("/reports", files=files)

            assert response.status_code == 200, response.text
            body = response.json()
            assert body["building_id"] is None
            report_id = uuid.UUID(body["id"])
            assert _read_building_id_for_report(report_id) is None
    finally:

        async def _cleanup_reports() -> None:
            settings = get_settings()
            engine = create_async_engine(settings.database_url)
            try:
                async with engine.begin() as conn:
                    await conn.execute(
                        text("delete from public.reports where crisis_id = :id"),
                        {"id": str(crisis_id)},
                    )
            finally:
                await engine.dispose()

        asyncio.run(_cleanup_reports())
        _delete_building(seeded_building_id)
        _delete_crisis(crisis_id)


def test_post_reports_unknown_building_id_does_not_snap_even_with_nearby_location() -> None:
    """Respect the tap: explicit `building_id` (even unknown) suppresses snap.

    Citizen tapped a building, but its GERS string is unknown to the server.
    A nearby seeded centroid exists that snap_to_nearest *would* bind to —
    but the tap branch must store `null`, not the snap result.
    """
    crisis_id = _seed_crisis(status="active")
    centroid_lat, centroid_lng = 33.5, 36.3
    seeded_building_id = _seed_overture_building_at(centroid_lat, centroid_lng)
    bogus_gers = f"unknown-gers-{uuid.uuid4().hex}"
    probe_lat = centroid_lat + (10.0 / 110_574.0)
    try:
        with TestClient(app) as client:
            payload = {
                "crisis_id": str(crisis_id),
                "damage_class": "minimal",
                "building_id": bogus_gers,
                "location": {"lat": probe_lat, "lng": centroid_lng},
            }
            files = {
                "photo": ("tiny.jpg", FIXTURE.read_bytes(), "image/jpeg"),
                "data": (None, json.dumps(payload), "application/json"),
            }

            response = client.post("/reports", files=files)

            assert response.status_code == 200, response.text
            body = response.json()
            # NOT the snap result — the tap was respected and stored null.
            assert body["building_id"] is None
            report_id = uuid.UUID(body["id"])
            assert _read_building_id_for_report(report_id) is None
    finally:

        async def _cleanup_reports() -> None:
            settings = get_settings()
            engine = create_async_engine(settings.database_url)
            try:
                async with engine.begin() as conn:
                    await conn.execute(
                        text("delete from public.reports where crisis_id = :id"),
                        {"id": str(crisis_id)},
                    )
            finally:
                await engine.dispose()

        asyncio.run(_cleanup_reports())
        _delete_building(seeded_building_id)
        _delete_crisis(crisis_id)


def test_post_reports_with_same_client_submission_id_dedupes() -> None:
    """Two POSTs with the same `client_submission_id` → one row, same id, 200/200,
    and exactly one storage object at the content-addressed key.

    Closes the contract end-to-end across Layer 1 (row dedup) and Layer 2
    (content-addressed photo dedup) of submission dedup.

    We drive the app with a single `httpx.AsyncClient(ASGITransport)` inside
    one `asyncio.run` so both POSTs share a loop with the cached async engine
    (TestClient starts a fresh portal per call, which would cross-loop the
    second request against the engine bound by the first).
    """
    from httpx import ASGITransport, AsyncClient

    from api.reports.photo import PhotoAddress

    settings = get_settings()
    crisis_id = _seed_crisis(status="active")
    submission_id = uuid.uuid4()
    photo_bytes = FIXTURE.read_bytes()
    expected_key = PhotoAddress.key_for(photo_bytes, "image/jpeg")
    payload = {
        "crisis_id": str(crisis_id),
        "damage_class": "minimal",
        "client_submission_id": str(submission_id),
        # Satisfies the minimum-content gate (location OR route).
        "location": {"lat": 25.2854, "lng": 51.5310},
    }

    async def _drive() -> tuple[int, dict[str, object], int, dict[str, object]]:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as http:
            files = {
                "photo": ("tiny.jpg", photo_bytes, "image/jpeg"),
                "data": (None, json.dumps(payload), "application/json"),
            }
            r1 = await http.post("/reports", files=files)
            files2 = {
                "photo": ("tiny.jpg", photo_bytes, "image/jpeg"),
                "data": (None, json.dumps(payload), "application/json"),
            }
            r2 = await http.post("/reports", files=files2)
            return r1.status_code, r1.json(), r2.status_code, r2.json()

    try:
        s1, b1, s2, b2 = asyncio.run(_drive())

        assert s1 == 200, b1
        assert s2 == 200, b2
        assert b1["id"] == b2["id"]
        assert _count_reports_for(crisis_id) == 1

        # The row points at the content-addressed key; both POSTs resolved
        # to the same object.
        async def _verify_path() -> None:
            engine = create_async_engine(settings.database_url)
            try:
                async with engine.connect() as conn:
                    path = (
                        await conn.execute(
                            text("select photo_path from public.reports where id = :id"),
                            {"id": str(b1["id"])},
                        )
                    ).scalar_one()
                    assert path == expected_key
            finally:
                await engine.dispose()

        asyncio.run(_verify_path())

        url = (
            f"{settings.supabase_url}/storage/v1/object/"
            f"{settings.supabase_storage_bucket}/{expected_key}"
        )
        headers = {"Authorization": f"Bearer {settings.supabase_service_role_key}"}
        head = httpx.get(url, headers=headers, timeout=5.0)
        assert head.status_code == 200, head.text
    finally:

        async def _cleanup_reports() -> None:
            engine = create_async_engine(settings.database_url)
            try:
                async with engine.begin() as conn:
                    await conn.execute(
                        text("delete from public.reports where crisis_id = :id"),
                        {"id": str(crisis_id)},
                    )
            finally:
                await engine.dispose()

        asyncio.run(_cleanup_reports())
        # Best-effort: remove the photo blob the test created. Other tests
        # may legitimately share the same content-addressed key, so a 4xx is
        # not a teardown failure.
        delete_url = (
            f"{settings.supabase_url}/storage/v1/object/"
            f"{settings.supabase_storage_bucket}/{expected_key}"
        )
        httpx.delete(
            delete_url,
            headers={"Authorization": f"Bearer {settings.supabase_service_role_key}"},
            timeout=5.0,
        )
        _delete_crisis(crisis_id)


def _read_report_columns(report_id: uuid.UUID) -> dict[str, object]:
    """Read photo_path / route_description / location-presence for a report."""
    settings = get_settings()

    async def _run() -> dict[str, object]:
        engine = create_async_engine(settings.database_url)
        try:
            async with engine.connect() as conn:
                row = (
                    await conn.execute(
                        text(
                            "select photo_path, description, route_description, "
                            "(location is not null) as has_location "
                            "from public.reports where id = :id"
                        ),
                        {"id": str(report_id)},
                    )
                ).one()
                return {
                    "photo_path": row.photo_path,
                    "description": row.description,
                    "route_description": row.route_description,
                    "has_location": row.has_location,
                }
        finally:
            await engine.dispose()

    return asyncio.run(_run())


def _cleanup_reports_for(crisis_id: uuid.UUID) -> None:
    settings = get_settings()

    async def _run() -> None:
        engine = create_async_engine(settings.database_url)
        try:
            async with engine.begin() as conn:
                await conn.execute(
                    text("delete from public.reports where crisis_id = :id"),
                    {"id": str(crisis_id)},
                )
        finally:
            await engine.dispose()

    asyncio.run(_run())


def test_post_reports_description_only_no_photo_persists_null_photo_path() -> None:
    """A description-only report (no `photo` part) inserts with photo_path NULL."""
    crisis_id = _seed_crisis(status="active")
    try:
        with TestClient(app) as client:
            payload = {
                "crisis_id": str(crisis_id),
                "damage_class": "partial",
                "description": "Collapsed stairwell, no safe way to photograph",
                "location": {"lat": 25.2854, "lng": 51.5310},
                "client_id": str(uuid.uuid4()),
            }
            # No `photo` file part — description-only submission.
            files = {"data": (None, json.dumps(payload), "application/json")}

            response = client.post("/reports", files=files)

            assert response.status_code == 200, response.text
            report_id = uuid.UUID(response.json()["id"])
            cols = _read_report_columns(report_id)
            assert cols["photo_path"] is None
            assert cols["description"] == "Collapsed stairwell, no safe way to photograph"
    finally:
        _cleanup_reports_for(crisis_id)
        _delete_crisis(crisis_id)


def test_post_reports_neither_photo_nor_description_returns_422() -> None:
    """No photo and no description fails pair 1 of the gate -> 422, no row."""
    crisis_id = _seed_crisis(status="active")
    try:
        with TestClient(app) as client:
            payload = {
                "crisis_id": str(crisis_id),
                "damage_class": "partial",
                "location": {"lat": 25.2854, "lng": 51.5310},
            }
            files = {"data": (None, json.dumps(payload), "application/json")}

            response = client.post("/reports", files=files)

            assert response.status_code == 422, response.text
            assert response.json()["detail"] == "photo_or_description_required"
            assert _count_reports_for(crisis_id) == 0
    finally:
        _delete_crisis(crisis_id)


def test_post_reports_route_only_no_location_persists() -> None:
    """A report with a photo and a free-text route (no GPS) satisfies pair 2."""
    crisis_id = _seed_crisis(status="active")
    try:
        with TestClient(app) as client:
            payload = {
                "crisis_id": str(crisis_id),
                "damage_class": "minimal",
                "route_description": "Behind the central market, second alley on the left",
            }
            files = {
                "photo": ("tiny.jpg", FIXTURE.read_bytes(), "image/jpeg"),
                "data": (None, json.dumps(payload), "application/json"),
            }

            response = client.post("/reports", files=files)

            assert response.status_code == 200, response.text
            report_id = uuid.UUID(response.json()["id"])
            cols = _read_report_columns(report_id)
            assert cols["has_location"] is False
            assert cols["route_description"] == (
                "Behind the central market, second alley on the left"
            )
    finally:
        _cleanup_reports_for(crisis_id)
        _delete_crisis(crisis_id)


def test_post_reports_neither_location_nor_route_returns_422() -> None:
    """A photo with no GPS and no route fails pair 2 of the gate -> 422, no row."""
    crisis_id = _seed_crisis(status="active")
    try:
        with TestClient(app) as client:
            payload = {
                "crisis_id": str(crisis_id),
                "damage_class": "minimal",
            }
            files = {
                "photo": ("tiny.jpg", FIXTURE.read_bytes(), "image/jpeg"),
                "data": (None, json.dumps(payload), "application/json"),
            }

            response = client.post("/reports", files=files)

            assert response.status_code == 422, response.text
            assert response.json()["detail"] == "location_or_route_required"
            assert _count_reports_for(crisis_id) == 0
    finally:
        _delete_crisis(crisis_id)


def test_reports_photo_or_description_check_constraint() -> None:
    """Defense-in-depth: the DB CHECK rejects a photo-less, description-less row
    and accepts a photo-less row that carries a description. Exercised via a
    direct INSERT, bypassing the service gate."""
    from sqlalchemy.exc import IntegrityError

    settings = get_settings()
    crisis_id = _seed_crisis(status="active")

    async def _insert(description: str | None) -> None:
        engine = create_async_engine(settings.database_url)
        try:
            async with engine.begin() as conn:
                await conn.execute(
                    text(
                        "insert into public.reports "
                        "(crisis_id, damage_class, photo_path, description, route_description) "
                        "values (:cid, 'minimal', null, :desc, 'second alley on the left')"
                    ),
                    {"cid": str(crisis_id), "desc": description},
                )
        finally:
            await engine.dispose()

    try:
        # photo_path NULL + description NULL -> CHECK violation.
        with pytest.raises(IntegrityError):
            asyncio.run(_insert(None))
        # photo_path NULL + description present -> accepted.
        asyncio.run(_insert("a written account of the damage"))
        assert _count_reports_for(crisis_id) == 1
    finally:
        _cleanup_reports_for(crisis_id)
        _delete_crisis(crisis_id)


def test_post_reports_without_building_id_or_location_inserts_with_null() -> None:
    crisis_id = _seed_crisis(status="active")
    try:
        with TestClient(app) as client:
            payload = {
                "crisis_id": str(crisis_id),
                "damage_class": "minimal",
                # No GPS and no building tap; a free-text route satisfies the
                # minimum-content gate so location stays null.
                "route_description": "Near the old water tower, end of the lane",
            }
            files = {
                "photo": ("tiny.jpg", FIXTURE.read_bytes(), "image/jpeg"),
                "data": (None, json.dumps(payload), "application/json"),
            }

            response = client.post("/reports", files=files)

            assert response.status_code == 200, response.text
            body = response.json()
            assert body["building_id"] is None
            assert body["location"] is None
            report_id = uuid.UUID(body["id"])
            assert _read_building_id_for_report(report_id) is None
    finally:

        async def _cleanup_reports() -> None:
            settings = get_settings()
            engine = create_async_engine(settings.database_url)
            try:
                async with engine.begin() as conn:
                    await conn.execute(
                        text("delete from public.reports where crisis_id = :id"),
                        {"id": str(crisis_id)},
                    )
            finally:
                await engine.dispose()

        asyncio.run(_cleanup_reports())
        _delete_crisis(crisis_id)
