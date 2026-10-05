"""Integration tests for PATCH /admin/crises/{id}.

The PATCH endpoint is the sole writer on `status` for transitions and the only
endpoint that mutates an existing crisis. Tests pin: per-field updates leave
other columns untouched, unknown fields fail validation (`extra='forbid'`),
and unknown ids return `404`.
"""

from __future__ import annotations

import asyncio
import uuid

import pytest
from fastapi.testclient import TestClient
from shapely.geometry import MultiPolygon, Polygon
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from api.admin.crisis_routes import get_crisis_polygon_resolver
from api.areas import OvertureDivisionsReader, ResolvedArea
from api.areas.overture_divisions_reader import Area, Country
from api.buildings.crisis_polygon_resolver import CrisisPolygonResolver
from api.core.config import get_settings
from api.main import app

pytestmark = pytest.mark.integration


@pytest.fixture(autouse=True)
def _reset_overture_cache() -> None:  # pyright: ignore[reportUnusedFunction]
    """Clear the Overture polygon resolver cache between tests so a
    `dependency_overrides` swap in one test cannot leak its fake reader
    into a later test."""
    # No-op: get_crisis_polygon_resolver is no longer @lru_cache.


def _seed_crisis(name: str, status: str = "inactive") -> uuid.UUID:
    settings = get_settings()
    crisis_id = uuid.uuid4()

    async def _run() -> None:
        engine = create_async_engine(settings.database_url)
        try:
            async with engine.begin() as conn:
                await conn.execute(
                    text("insert into public.crises (id, name, status) values (:id, :n, :s)"),
                    {"id": str(crisis_id), "n": name, "s": status},
                )
        finally:
            await engine.dispose()

    asyncio.run(_run())
    return crisis_id


def _delete_seed(ids: list[uuid.UUID]) -> None:
    settings = get_settings()

    async def _run() -> None:
        engine = create_async_engine(settings.database_url)
        try:
            async with engine.begin() as conn:
                await conn.execute(
                    text("delete from public.crises where id = any(:ids)"),
                    {"ids": [str(i) for i in ids]},
                )
        finally:
            await engine.dispose()

    asyncio.run(_run())


def _read_row(crisis_id: uuid.UUID) -> tuple[str, str]:
    settings = get_settings()

    async def _run() -> tuple[str, str]:
        engine = create_async_engine(settings.database_url)
        try:
            async with engine.connect() as conn:
                row = (
                    await conn.execute(
                        text("select name, status from public.crises where id = :id"),
                        {"id": str(crisis_id)},
                    )
                ).first()
        finally:
            await engine.dispose()
        assert row is not None
        return row.name, row.status

    return asyncio.run(_run())


def test_patch_status_transitions_inactive_to_active() -> None:
    name = f"Patch status {uuid.uuid4().hex[:8]}"
    crisis_id = _seed_crisis(name, status="inactive")

    try:
        with TestClient(app) as client:
            response = client.patch(f"/admin/crises/{crisis_id}", json={"status": "active"})

        assert response.status_code == 200, response.text
        body = response.json()
        assert body["id"] == str(crisis_id)
        assert body["status"] == "active"
        assert body["name"] == name

        _, db_status = _read_row(crisis_id)
        assert db_status == "active"
    finally:
        _delete_seed([crisis_id])


def test_patch_name_only_leaves_status_unchanged() -> None:
    name = f"Original {uuid.uuid4().hex[:8]}"
    new_name = f"Renamed {uuid.uuid4().hex[:8]}"
    crisis_id = _seed_crisis(name, status="active")

    try:
        with TestClient(app) as client:
            response = client.patch(f"/admin/crises/{crisis_id}", json={"name": new_name})

        assert response.status_code == 200, response.text
        body = response.json()
        assert body["name"] == new_name
        assert body["status"] == "active"

        db_name, db_status = _read_row(crisis_id)
        assert db_name == new_name
        assert db_status == "active"
    finally:
        _delete_seed([crisis_id])


def test_patch_unknown_field_returns_422() -> None:
    name = f"Unknown field {uuid.uuid4().hex[:8]}"
    crisis_id = _seed_crisis(name)

    try:
        with TestClient(app) as client:
            response = client.patch(
                f"/admin/crises/{crisis_id}",
                json={"name": "x", "totally_made_up": "value"},
            )
        assert response.status_code == 422, response.text
    finally:
        _delete_seed([crisis_id])


def test_patch_missing_crisis_returns_404() -> None:
    with TestClient(app) as client:
        response = client.patch(f"/admin/crises/{uuid.uuid4()}", json={"status": "active"})
    assert response.status_code == 404, response.text


def _read_metadata(crisis_id: uuid.UUID) -> tuple[str, list[str], object, object]:
    settings = get_settings()

    async def _run() -> tuple[str, list[str], object, object]:
        engine = create_async_engine(settings.database_url)
        try:
            async with engine.connect() as conn:
                row = (
                    await conn.execute(
                        text(
                            "select type, countries, started_at, ended_at "
                            "from public.crises where id = :id"
                        ),
                        {"id": str(crisis_id)},
                    )
                ).first()
        finally:
            await engine.dispose()
        assert row is not None
        return row.type, list(row.countries), row.started_at, row.ended_at

    return asyncio.run(_run())


def test_patch_type_only_changes_type() -> None:
    name = f"Patch type {uuid.uuid4().hex[:8]}"
    crisis_id = _seed_crisis(name)

    try:
        with TestClient(app) as client:
            response = client.patch(f"/admin/crises/{crisis_id}", json={"type": "earthquake"})
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["type"] == "earthquake"
        # Other fields preserved.
        assert body["name"] == name
        db_type, db_countries, _, _ = _read_metadata(crisis_id)
        assert db_type == "earthquake"
        assert db_countries == []
    finally:
        _delete_seed([crisis_id])


def test_patch_countries_only_replaces_countries() -> None:
    name = f"Patch countries {uuid.uuid4().hex[:8]}"
    crisis_id = _seed_crisis(name)

    try:
        with TestClient(app) as client:
            response = client.patch(f"/admin/crises/{crisis_id}", json={"countries": ["QA", "BH"]})
        assert response.status_code == 200, response.text
        assert response.json()["countries"] == ["QA", "BH"]
        _, db_countries, _, _ = _read_metadata(crisis_id)
        assert db_countries == ["QA", "BH"]
    finally:
        _delete_seed([crisis_id])


def test_patch_started_and_ended_at_round_trip() -> None:
    name = f"Patch times {uuid.uuid4().hex[:8]}"
    crisis_id = _seed_crisis(name)

    try:
        with TestClient(app) as client:
            response = client.patch(
                f"/admin/crises/{crisis_id}",
                json={
                    "started_at": "2026-03-01T00:00:00+00:00",
                    "ended_at": "2026-03-30T00:00:00+00:00",
                },
            )
        assert response.status_code == 200, response.text
        _, _, started, ended = _read_metadata(crisis_id)
        assert started is not None
        assert ended is not None
    finally:
        _delete_seed([crisis_id])


def test_patch_with_inverted_time_window_fails() -> None:
    name = f"Patch bad window {uuid.uuid4().hex[:8]}"
    crisis_id = _seed_crisis(name)

    try:
        with TestClient(app, raise_server_exceptions=False) as client:
            response = client.patch(
                f"/admin/crises/{crisis_id}",
                json={
                    "started_at": "2026-03-30T00:00:00+00:00",
                    "ended_at": "2026-03-01T00:00:00+00:00",
                },
            )
        assert response.status_code == 500
    finally:
        _delete_seed([crisis_id])


# --- PATCH geometry: invalidates ingest stamps -----------------------------


def _seed_crisis_with_ingest_stamps(name: str) -> uuid.UUID:
    """Seed an `inactive` crisis pre-populated with a bbox geometry,
    `pmtiles_url`, `buildings_ingested_at`, and `buildings_ingested_count`.
    The PATCH-geometry tests assert all of these get nulled the moment a
    new geometry lands."""
    settings = get_settings()
    crisis_id = uuid.uuid4()

    async def _run() -> None:
        engine = create_async_engine(settings.database_url)
        try:
            async with engine.begin() as conn:
                await conn.execute(
                    text(
                        "insert into public.crises "
                        "  (id, name, status, geometry, "
                        "   pmtiles_url, buildings_ingested_at, "
                        "   buildings_ingested_count) "
                        "values (:id, :n, 'inactive', "
                        "  st_multi(st_makeenvelope(0, 0, 1, 1, 4326))::geography, "
                        "  :url, now(), 1234)"
                    ),
                    {
                        "id": str(crisis_id),
                        "n": name,
                        "url": "https://example.test/old.pmtiles",
                    },
                )
        finally:
            await engine.dispose()

    asyncio.run(_run())
    return crisis_id


def _read_ingest_stamps(
    crisis_id: uuid.UUID,
) -> tuple[bool, str | None, object | None, int | None]:
    """Return `(has_geometry, pmtiles_url, buildings_ingested_at,
    buildings_ingested_count)`."""
    settings = get_settings()

    async def _run() -> tuple[bool, str | None, object | None, int | None]:
        engine = create_async_engine(settings.database_url)
        try:
            async with engine.connect() as conn:
                row = (
                    await conn.execute(
                        text(
                            "select geometry is not null as has_geom, "
                            "       pmtiles_url, buildings_ingested_at, "
                            "       buildings_ingested_count "
                            "from public.crises where id = :id"
                        ),
                        {"id": str(crisis_id)},
                    )
                ).first()
        finally:
            await engine.dispose()
        assert row is not None
        return (
            bool(row.has_geom),
            row.pmtiles_url,
            row.buildings_ingested_at,
            row.buildings_ingested_count,
        )

    return asyncio.run(_run())


def _override_resolver_with_qa() -> None:
    """Wire a fake reader so PATCH /geometry doesn't hit live S3."""
    qa_poly = MultiPolygon(
        [Polygon([(50.7, 24.4), (51.7, 24.4), (51.7, 26.2), (50.7, 26.2), (50.7, 24.4)])]
    )

    class _Reader:
        async def search(self, q: str, country: str | None, limit: int) -> list[Area]:
            raise AssertionError("unused")

        async def list_countries(self) -> list[Country]:
            raise AssertionError("unused")

        async def read_area_geometry(self, area_id: str) -> ResolvedArea:
            return ResolvedArea(wkb=qa_poly.wkb, country="QA")

        async def read_area_geojson(self, area_id: str) -> dict[str, object]:
            raise AssertionError("unused")

        async def read_countries_union(self, iso2_codes: list[str]) -> bytes | None:
            raise AssertionError("unused")

        async def read_countries_geojson_union(
            self, iso2_codes: list[str]
        ) -> dict[str, object] | None:
            raise AssertionError("unused")

    fake: OvertureDivisionsReader = _Reader()

    def override() -> CrisisPolygonResolver:
        return CrisisPolygonResolver(reader=fake)

    app.dependency_overrides[get_crisis_polygon_resolver] = override


def test_patch_geometry_writes_new_polygon_and_nulls_ingest_stamps() -> None:
    """Sending `{geometry: ...}` rewrites `crises.geometry` AND clears
    `pmtiles_url` + `buildings_ingested_at` in the same UPDATE — admin must
    re-run ingest before the citizen map serves anything for this crisis.
    """
    name = f"Patch geom {uuid.uuid4().hex[:8]}"
    crisis_id = _seed_crisis_with_ingest_stamps(name)

    _override_resolver_with_qa()
    try:
        with TestClient(app) as client:
            response = client.patch(
                f"/admin/crises/{crisis_id}",
                json={"geometry": {"division_id": "qa-country"}},
            )
    finally:
        app.dependency_overrides.pop(get_crisis_polygon_resolver, None)

    try:
        assert response.status_code == 200, response.text
        # Same response as create: has_geometry True after the write.
        assert response.json()["has_geometry"] is True

        has_geom, pmtiles_url, ingested_at, ingested_count = _read_ingest_stamps(crisis_id)
        assert has_geom is True  # new geometry stamped
        assert pmtiles_url is None
        assert ingested_at is None
        assert ingested_count is None
    finally:
        _delete_seed([crisis_id])


def test_patch_without_geometry_leaves_ingest_stamps_untouched() -> None:
    """Patches that omit `geometry` must NOT touch `pmtiles_url`,
    `buildings_ingested_at`, or `buildings_ingested_count`. That belongs
    only to the geometry rewrite path.
    """
    name = f"Patch no-geom {uuid.uuid4().hex[:8]}"
    crisis_id = _seed_crisis_with_ingest_stamps(name)

    try:
        with TestClient(app) as client:
            response = client.patch(f"/admin/crises/{crisis_id}", json={"name": "renamed"})
        assert response.status_code == 200, response.text

        has_geom, pmtiles_url, ingested_at, ingested_count = _read_ingest_stamps(crisis_id)
        # Stamps survive untouched — this is the regression guard.
        assert has_geom is True
        assert pmtiles_url == "https://example.test/old.pmtiles"
        assert ingested_at is not None
        assert ingested_count == 1234
    finally:
        _delete_seed([crisis_id])


def test_patch_geometry_with_bbox_branch_also_invalidates_stamps() -> None:
    """The invalidation rule is per-PATCH-with-geometry, not per-branch —
    bbox geometries clear the stamps just like division_id does."""
    name = f"Patch bbox geom {uuid.uuid4().hex[:8]}"
    crisis_id = _seed_crisis_with_ingest_stamps(name)

    try:
        with TestClient(app) as client:
            response = client.patch(
                f"/admin/crises/{crisis_id}",
                json={"geometry": {"bbox": [50.0, 24.0, 52.0, 27.0]}},
            )
        assert response.status_code == 200, response.text

        _, pmtiles_url, ingested_at, ingested_count = _read_ingest_stamps(crisis_id)
        assert pmtiles_url is None
        assert ingested_at is None
        assert ingested_count is None
    finally:
        _delete_seed([crisis_id])
