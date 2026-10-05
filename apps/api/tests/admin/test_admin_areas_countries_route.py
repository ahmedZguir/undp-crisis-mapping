"""Route tests for `GET /admin/areas/countries`.

Thin pass-through over `OvertureDivisionsReader.list_countries`. The PWA
caches the response at admin-app boot and uses it both for the country
multi-select and for `IQ → Iraq` rendering.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from api.admin.area_routes import get_overture_divisions_reader
from api.areas import Area, Country, OvertureDivisionsReader, ResolvedArea
from api.main import app


class _FakeReader:
    def __init__(self, countries: list[Country]) -> None:
        self.countries = countries

    async def search(self, q: str, country: str | None, limit: int) -> list[Area]:
        raise AssertionError("search must not be called for the countries route")

    async def list_countries(self) -> list[Country]:
        return self.countries

    async def read_area_geometry(self, area_id: str) -> ResolvedArea:
        raise AssertionError("read_area_geometry must not be called for the countries route")

    async def read_area_geojson(self, area_id: str) -> dict[str, object]:
        raise AssertionError("read_area_geojson must not be called for the countries route")

    async def read_countries_union(self, iso2_codes: list[str]) -> bytes | None:
        raise AssertionError("read_countries_union must not be called for the countries route")

    async def read_countries_geojson_union(self, iso2_codes: list[str]) -> dict[str, object] | None:
        raise AssertionError(
            "read_countries_geojson_union must not be called for the countries route"
        )


def _override(reader: OvertureDivisionsReader) -> None:
    app.dependency_overrides[get_overture_divisions_reader] = lambda: reader


def test_get_admin_areas_countries_returns_reader_results_in_response_shape() -> None:
    fake = _FakeReader(
        [
            Country(id="iq", iso2="IQ", name="Iraq"),
            Country(id="sy", iso2="SY", name="Syria"),
        ]
    )
    _override(fake)

    try:
        with TestClient(app) as client:
            response = client.get("/admin/areas/countries")
    finally:
        app.dependency_overrides.pop(get_overture_divisions_reader, None)

    assert response.status_code == 200, response.text
    assert response.json() == [
        {"id": "iq", "iso2": "IQ", "name": "Iraq"},
        {"id": "sy", "iso2": "SY", "name": "Syria"},
    ]
