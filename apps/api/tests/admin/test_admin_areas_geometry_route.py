"""Route tests for `GET /admin/areas/{division_id}/geometry`.

The handler is a thin pass-through over `read_area_geojson` — these tests
pin the wire shape, the dependency-override seam, and the
`AreaNotFoundError → 404` mapping.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from api.admin.area_routes import get_overture_divisions_reader
from api.areas import Area, AreaNotFoundError, Country, OvertureDivisionsReader, ResolvedArea
from api.main import app


class _FakeReader:
    def __init__(self, geom: dict[str, object] | None) -> None:
        self.geom = geom
        self.calls: list[str] = []

    async def search(self, q: str, country: str | None, limit: int) -> list[Area]:
        raise AssertionError("search must not be called for the geometry route")

    async def list_countries(self) -> list[Country]:
        raise AssertionError("list_countries must not be called for the geometry route")

    async def read_area_geometry(self, area_id: str) -> ResolvedArea:
        raise AssertionError("read_area_geometry must not be called for the geometry route")

    async def read_area_geojson(self, area_id: str) -> dict[str, object]:
        self.calls.append(area_id)
        if self.geom is None:
            raise AreaNotFoundError(area_id)
        return self.geom

    async def read_countries_union(self, iso2_codes: list[str]) -> bytes | None:
        raise AssertionError("read_countries_union must not be called for the geometry route")

    async def read_countries_geojson_union(self, iso2_codes: list[str]) -> dict[str, object] | None:
        raise AssertionError(
            "read_countries_geojson_union must not be called for the geometry route"
        )


def _override_reader(reader: OvertureDivisionsReader) -> None:
    app.dependency_overrides[get_overture_divisions_reader] = lambda: reader


def test_get_admin_areas_geometry_returns_polygon_in_response_shape() -> None:
    geom: dict[str, object] = {
        "type": "MultiPolygon",
        "coordinates": [[[[0.0, 0.0], [1.0, 0.0], [1.0, 1.0], [0.0, 0.0]]]],
    }
    fake = _FakeReader(geom)
    _override_reader(fake)

    try:
        with TestClient(app) as client:
            response = client.get("/admin/areas/qa-country/geometry")
    finally:
        app.dependency_overrides.pop(get_overture_divisions_reader, None)

    assert response.status_code == 200, response.text
    assert response.json() == geom
    assert fake.calls == ["qa-country"]


def test_get_admin_areas_geometry_returns_404_for_unknown_id() -> None:
    fake = _FakeReader(None)
    _override_reader(fake)

    try:
        with TestClient(app) as client:
            response = client.get("/admin/areas/does-not-exist/geometry")
    finally:
        app.dependency_overrides.pop(get_overture_divisions_reader, None)

    assert response.status_code == 404, response.text
