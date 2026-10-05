"""Route-level tests for the OSM fallback on `GET /admin/areas/search`.

Covers the four interesting paths in the route handler:

1. Overture has hits → OSM is not called; response source defaults to
   `overture`.
2. Overture empty + `len(q) < OSM_FALLBACK_MIN_Q_LEN` → no OSM call,
   `[]` response.
3. Overture empty + `len(q) >= OSM_FALLBACK_MIN_Q_LEN` → OSM called
   exactly once; response carries `source="osm"` and inline `geometry`.
4. Overture empty + OSM returns nothing → `[]` response.

Uses dependency overrides for both the Overture reader and the OSM
searcher — no real database or HTTP needed.
"""

from __future__ import annotations

from typing import Any

from fastapi.testclient import TestClient

from api.admin.area_routes import (
    get_osm_area_searcher,
    get_overture_divisions_reader,
)
from api.areas import Area, Country, OSMArea, OvertureDivisionsReader, ResolvedArea
from api.main import app


class _FakeReader:
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


class _FakeOsmSearcher:
    def __init__(self, hits: list[OSMArea]) -> None:
        self.hits = hits
        self.calls: list[dict[str, Any]] = []

    async def search(self, q: str, country: str | None, limit: int) -> list[OSMArea]:
        self.calls.append({"q": q, "country": country, "limit": limit})
        return self.hits


def _override(reader: OvertureDivisionsReader, osm: _FakeOsmSearcher) -> None:
    app.dependency_overrides[get_overture_divisions_reader] = lambda: reader
    app.dependency_overrides[get_osm_area_searcher] = lambda: osm


def _clear_overrides() -> None:
    app.dependency_overrides.pop(get_overture_divisions_reader, None)
    app.dependency_overrides.pop(get_osm_area_searcher, None)


def _osm_hit() -> OSMArea:
    return OSMArea(
        osm_id="osm:relation/12345",
        name="Tikrit",
        subtype="osm:city",
        country="IQ",
        parents=["Saladin Governorate", "Iraq"],
        bbox=(43.6, 34.5, 43.8, 34.7),
        geometry={
            "type": "Polygon",
            "coordinates": [[[43.6, 34.5], [43.8, 34.5], [43.8, 34.7], [43.6, 34.5]]],
        },
    )


def test_overture_hits_short_circuit_osm() -> None:
    reader = _FakeReader(
        [
            Area(
                id="moscow",
                name="Moscow",
                subtype="locality",
                country="RU",
                parents=["Russia"],
                bbox=(37.3, 55.5, 37.9, 56.0),
            )
        ]
    )
    osm = _FakeOsmSearcher([_osm_hit()])
    _override(reader, osm)
    try:
        with TestClient(app) as client:
            response = client.get("/admin/areas/search", params={"q": "Mosc"})
    finally:
        _clear_overrides()

    assert response.status_code == 200, response.text
    body = response.json()
    assert len(body) == 1
    assert body[0]["id"] == "moscow"
    assert body[0]["source"] == "overture"
    assert body[0]["geometry"] is None
    assert osm.calls == []


def test_overture_empty_short_query_skips_osm() -> None:
    reader = _FakeReader([])
    osm = _FakeOsmSearcher([_osm_hit()])
    _override(reader, osm)
    try:
        with TestClient(app) as client:
            # `len("mos") == 3` is below the minimum length gate.
            response = client.get("/admin/areas/search", params={"q": "mos"})
    finally:
        _clear_overrides()

    assert response.status_code == 200, response.text
    assert response.json() == []
    assert osm.calls == []


def test_overture_empty_long_query_falls_back_to_osm() -> None:
    reader = _FakeReader([])
    osm = _FakeOsmSearcher([_osm_hit()])
    _override(reader, osm)
    try:
        with TestClient(app) as client:
            response = client.get("/admin/areas/search", params={"q": "tikrit", "country": "IQ"})
    finally:
        _clear_overrides()

    assert response.status_code == 200, response.text
    body = response.json()
    assert len(body) == 1
    row = body[0]
    assert row["id"] == "osm:relation/12345"
    assert row["source"] == "osm"
    assert row["geometry"] is not None
    assert row["geometry"]["type"] == "Polygon"
    assert row["country"] == "IQ"
    assert osm.calls == [{"q": "tikrit", "country": "IQ", "limit": 10}]


def test_force_source_osm_skips_overture() -> None:
    """`?source=osm` is the picker's post-click escape hatch: when an
    Overture hit had no polygon, we re-query OSM for the same name and
    show those alternatives — Overture must not be touched at all."""
    reader = _FakeReader(
        [
            Area(
                id="bh-manama",
                name="Manama",
                subtype="locality",
                country="BH",
                parents=["Bahrain"],
                bbox=(50.4, 26.1, 50.7, 26.3),
            )
        ]
    )
    osm = _FakeOsmSearcher([_osm_hit()])
    _override(reader, osm)
    try:
        with TestClient(app) as client:
            response = client.get(
                "/admin/areas/search",
                params={"q": "manama", "source": "osm", "country": "BH"},
            )
    finally:
        _clear_overrides()

    assert response.status_code == 200, response.text
    body = response.json()
    assert len(body) == 1
    assert body[0]["source"] == "osm"
    assert reader.calls == []
    assert osm.calls == [{"q": "manama", "country": "BH", "limit": 10}]


def test_force_source_osm_short_query_returns_empty_without_calling_osm() -> None:
    reader = _FakeReader([])
    osm = _FakeOsmSearcher([_osm_hit()])
    _override(reader, osm)
    try:
        with TestClient(app) as client:
            response = client.get("/admin/areas/search", params={"q": "ma", "source": "osm"})
    finally:
        _clear_overrides()

    assert response.status_code == 200, response.text
    assert response.json() == []
    assert reader.calls == []
    assert osm.calls == []


def test_overture_and_osm_both_empty_returns_empty() -> None:
    reader = _FakeReader([])
    osm = _FakeOsmSearcher([])
    _override(reader, osm)
    try:
        with TestClient(app) as client:
            response = client.get("/admin/areas/search", params={"q": "asdfasdf"})
    finally:
        _clear_overrides()

    assert response.status_code == 200, response.text
    assert response.json() == []
    assert len(osm.calls) == 1
