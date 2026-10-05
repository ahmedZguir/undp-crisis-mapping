"""Route tests for `GET /admin/areas/search`.

The handler is a thin pass-through over `OvertureDivisionsReader.search` —
these tests pin the wire shape and the dependency-override seam, not the
SQL query.
"""

from __future__ import annotations

from typing import Any

from fastapi.testclient import TestClient

from api.admin.area_routes import get_osm_area_searcher, get_overture_divisions_reader
from api.areas import Area, Country, OSMArea, OvertureDivisionsReader, ResolvedArea
from api.main import app


class _FakeReader:
    """Records every call and returns whatever the test pre-loaded."""

    def __init__(self, areas: list[Area]) -> None:
        self.areas = areas
        self.calls: list[dict[str, Any]] = []

    async def search(self, q: str, country: str | None, limit: int) -> list[Area]:
        self.calls.append({"q": q, "country": country, "limit": limit})
        return self.areas

    async def list_countries(self) -> list[Country]:
        raise AssertionError("list_countries must not be called for the search route")

    async def read_area_geometry(self, area_id: str) -> ResolvedArea:
        raise AssertionError("read_area_geometry must not be called for the search route")

    async def read_area_geojson(self, area_id: str) -> dict[str, object]:
        raise AssertionError("read_area_geojson must not be called for the search route")

    async def read_countries_union(self, iso2_codes: list[str]) -> bytes | None:
        raise AssertionError("read_countries_union must not be called for the search route")

    async def read_countries_geojson_union(self, iso2_codes: list[str]) -> dict[str, object] | None:
        raise AssertionError("read_countries_geojson_union must not be called for the search route")


class _NullOsmSearcher:
    """The OSM fallback is never reached when Overture has hits; for the
    empty-hits route test we still want a deterministic no-op."""

    async def search(self, q: str, country: str | None, limit: int) -> list[OSMArea]:
        return []


def _override_reader(reader: OvertureDivisionsReader) -> None:
    app.dependency_overrides[get_overture_divisions_reader] = lambda: reader
    app.dependency_overrides[get_osm_area_searcher] = _NullOsmSearcher


def test_get_admin_areas_search_returns_reader_results_in_response_shape() -> None:
    fake = _FakeReader(
        [
            Area(
                id="moscow",
                name="Moscow",
                subtype="locality",
                country="RU",
                parents=["Russia"],
                bbox=(37.3, 55.5, 37.9, 56.0),
            ),
        ]
    )
    _override_reader(fake)

    try:
        with TestClient(app) as client:
            response = client.get("/admin/areas/search", params={"q": "Mos"})
    finally:
        app.dependency_overrides.pop(get_overture_divisions_reader, None)
        app.dependency_overrides.pop(get_osm_area_searcher, None)

    assert response.status_code == 200, response.text
    body = response.json()
    assert body == [
        {
            "id": "moscow",
            "name": "Moscow",
            "subtype": "locality",
            "country": "RU",
            "parents": ["Russia"],
            "bbox": [37.3, 55.5, 37.9, 56.0],
            "source": "overture",
            "geometry": None,
        }
    ]
    assert fake.calls == [{"q": "Mos", "country": None, "limit": 10}]


def test_get_admin_areas_search_passes_country_and_limit_through() -> None:
    fake = _FakeReader([])
    _override_reader(fake)

    try:
        with TestClient(app) as client:
            response = client.get(
                "/admin/areas/search",
                params={"q": "Mos", "country": "IQ", "limit": 25},
            )
    finally:
        app.dependency_overrides.pop(get_overture_divisions_reader, None)
        app.dependency_overrides.pop(get_osm_area_searcher, None)

    assert response.status_code == 200, response.text
    assert fake.calls == [{"q": "Mos", "country": "IQ", "limit": 25}]


def test_get_admin_areas_search_requires_q() -> None:
    with TestClient(app) as client:
        response = client.get("/admin/areas/search")
        # FastAPI surfaces missing required query params as 422.
        assert response.status_code == 422
