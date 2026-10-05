"""Route smoke tests for the building / division name autocompletes.

These are the search-filter pickers added in the
"location-by-name filtering" pass. The real SQL — `pg_trgm`
`similarity(...)` ranking against `buildings.name` /
`overture_divisions.names_search_text` — is exercised in
`tests/admin/test_admin_buildings_stats_route.py` (DB-backed). What we cover
here is the wire contract: the routes accept the documented query
strings, return the documented JSON shape, and refuse unknown crises
with a 404 (buildings).
"""

from __future__ import annotations

import asyncio
import uuid
from typing import Any, cast

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from api.core.config import get_settings
from api.main import app

pytestmark = pytest.mark.integration


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
                {"id": str(crisis_id), "n": f"Name-search test {suffix}"},
            )
    finally:
        await engine.dispose()
    return crisis_id


async def _seed_building_with_report(
    engine_url: str, *, crisis_id: uuid.UUID, name: str
) -> tuple[uuid.UUID, uuid.UUID]:
    building_id = uuid.uuid4()
    report_id = uuid.uuid4()
    source_id = uuid.uuid4().hex
    # Tiny footprint centred at (25.3, 51.5).
    lat, lng, delta = 25.30, 51.50, 0.00001
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
            await conn.execute(
                text(
                    "insert into public.reports "
                    "  (id, crisis_id, damage_class, photo_path, "
                    "   building_id, infra_type, route_description) "
                    "values "
                    "  (:id, :crisis_id, 'partial', :photo, :building_id, "
                    # route_description satisfies reports_location_or_route
                    "   ARRAY['school']::text[], 'test directions')"
                ),
                {
                    "id": str(report_id),
                    "crisis_id": str(crisis_id),
                    "photo": f"test/{report_id}.jpg",
                    "building_id": str(building_id),
                },
            )
    finally:
        await engine.dispose()
    return building_id, report_id


async def _cleanup(
    engine_url: str,
    *,
    crisis_ids: list[uuid.UUID],
    building_ids: list[uuid.UUID],
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
            await conn.execute(
                text("delete from public.buildings where id = any(:ids)"),
                {"ids": [str(i) for i in building_ids]},
            )
    finally:
        await engine.dispose()


def test_buildings_search_returns_matches_with_centroid_and_infra() -> None:
    settings = get_settings()
    crisis_id = asyncio.run(_seed_crisis(settings.database_url))
    name = f"Al-Falah Tower {uuid.uuid4().hex[:6]}"
    building_id, _report_id = asyncio.run(
        _seed_building_with_report(settings.database_url, crisis_id=crisis_id, name=name)
    )
    try:
        with TestClient(app) as client:
            resp = client.get(
                "/admin/buildings/search",
                params={"crisis_id": str(crisis_id), "q": "Falah", "limit": 5},
            )
        assert resp.status_code == 200, resp.text
        rows = resp.json()
        assert any(r["id"] == str(building_id) for r in rows)
        hit = next(r for r in rows if r["id"] == str(building_id))
        assert hit["name"] == name
        assert hit["centroid"]["lat"] == pytest.approx(25.30, abs=0.01)  # pyright: ignore[reportUnknownMemberType]
        assert hit["centroid"]["lng"] == pytest.approx(51.50, abs=0.01)  # pyright: ignore[reportUnknownMemberType]
        assert hit["n_reports"] == 1
        assert hit["infra_type"] == ["school"]
    finally:
        asyncio.run(
            _cleanup(
                settings.database_url,
                crisis_ids=[crisis_id],
                building_ids=[building_id],
            )
        )


def test_buildings_search_404_when_crisis_missing() -> None:
    with TestClient(app) as client:
        resp = client.get(
            "/admin/buildings/search",
            params={"crisis_id": str(uuid.uuid4()), "q": "anything"},
        )
    assert resp.status_code == 404


def test_divisions_search_returns_expected_shape() -> None:
    """Smoke check: an existing Overture row in Qatar is reachable.

    We don't seed divisions in tests; if the local DB has no Overture
    rows the response will be empty, in which case we just assert the
    200 + JSON-list contract instead of pinning specific results.
    """
    with TestClient(app) as client:
        resp = client.get(
            "/admin/divisions/search",
            params={"q": "doha", "country_code": "QA", "limit": 5},
        )
    assert resp.status_code == 200
    rows = resp.json()
    assert isinstance(rows, list)
    if rows:
        sample = cast(dict[str, Any], rows[0])
        assert {"id", "name", "admin_level", "country_code", "centroid"}.issubset(sample)
        centroid = cast(dict[str, Any], sample["centroid"])
        assert "lat" in centroid and "lng" in centroid
