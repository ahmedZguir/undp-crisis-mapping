"""Integration tests for POST /admin/crises.

Pins the wire contract for crisis creation, including the three geometry
input forms that all collapse to one stored MultiPolygon. The country-code
branch is exercised against an injected fake `DivisionsReader` so the test
never hits live S3.
"""

from __future__ import annotations

import asyncio
import uuid
from typing import Any

import pytest
from fastapi.testclient import TestClient
from shapely.geometry import MultiPolygon, Polygon, mapping
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from api.admin.crisis_routes import get_crisis_polygon_resolver
from api.areas import AreaNotFoundError, OvertureDivisionsReader, ResolvedArea
from api.areas.overture_divisions_reader import Area, Country
from api.buildings.crisis_polygon_resolver import CrisisPolygonResolver
from api.core.config import get_settings
from api.main import app

pytestmark = pytest.mark.integration


@pytest.fixture(autouse=True)
def _reset_overture_cache() -> None:  # pyright: ignore[reportUnusedFunction]
    """The Overture polygon resolver is cached per-process. Clear between
    tests so a `dependency_overrides` swap in one test cannot leak its fake
    reader into a later test."""
    # No-op: get_crisis_polygon_resolver is no longer @lru_cache.


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


def _read_crisis_geometry(crisis_id: uuid.UUID) -> tuple[bool, float | None, str | None]:
    """Return (has_geometry, area_m2, geometry_type) for the given crisis."""
    settings = get_settings()

    async def _run() -> tuple[bool, float | None, str | None]:
        engine = create_async_engine(settings.database_url)
        try:
            async with engine.connect() as conn:
                row = (
                    await conn.execute(
                        text(
                            "select geometry is not null as has_geom, "
                            "case when geometry is null then null "
                            "  else st_area(geometry) end as area_m2, "
                            "case when geometry is null then null "
                            "  else st_geometrytype(geometry::geometry) end as gtype "
                            "from public.crises where id = :id"
                        ),
                        {"id": str(crisis_id)},
                    )
                ).first()
        finally:
            await engine.dispose()
        assert row is not None
        return row.has_geom, row.area_m2, row.gtype

    return asyncio.run(_run())


def test_post_crisis_without_geometry_creates_row_with_null_geometry() -> None:
    name = f"Test crisis no geom {uuid.uuid4().hex[:8]}"

    with TestClient(app) as client:
        response = client.post("/admin/crises", json={"name": name, "type": "earthquake"})

    assert response.status_code == 201, response.text
    body: dict[str, Any] = response.json()
    assert body["name"] == name
    assert body["status"] == "inactive"
    assert body["has_geometry"] is False
    assert body["type"] == "earthquake"
    assert body["countries"] == []
    assert body["started_at"] is None
    assert body["ended_at"] is None
    crisis_id = uuid.UUID(body["id"])

    try:
        has_geom, _, _ = _read_crisis_geometry(crisis_id)
        assert has_geom is False
    finally:
        _delete_seed([crisis_id])


def _read_crisis_status(crisis_id: uuid.UUID) -> str:
    settings = get_settings()

    async def _run() -> str:
        engine = create_async_engine(settings.database_url)
        try:
            async with engine.connect() as conn:
                row = (
                    await conn.execute(
                        text("select status from public.crises where id = :id"),
                        {"id": str(crisis_id)},
                    )
                ).first()
        finally:
            await engine.dispose()
        assert row is not None
        return row.status

    return asyncio.run(_run())


def test_post_crisis_default_status_is_inactive_and_absent_from_public_list() -> None:
    """A new crisis lands `inactive` and does not appear in `GET /crises`."""
    name = f"Test inactive default {uuid.uuid4().hex[:8]}"

    with TestClient(app) as client:
        create_resp = client.post("/admin/crises", json={"name": name, "type": "flood"})
        assert create_resp.status_code == 201, create_resp.text
        crisis_id = uuid.UUID(create_resp.json()["id"])

        try:
            assert _read_crisis_status(crisis_id) == "inactive"

            list_resp = client.get("/crises")
            assert list_resp.status_code == 200
            listed_ids = [item["id"] for item in list_resp.json()]
            assert str(crisis_id) not in listed_ids
        finally:
            _delete_seed([crisis_id])


def test_post_crisis_explicit_active_status_still_works() -> None:
    name = f"Test active explicit {uuid.uuid4().hex[:8]}"

    with TestClient(app) as client:
        response = client.post(
            "/admin/crises",
            json={"name": name, "status": "active", "type": "earthquake"},
        )

    assert response.status_code == 201, response.text
    body: dict[str, Any] = response.json()
    assert body["status"] == "active"
    crisis_id = uuid.UUID(body["id"])

    try:
        assert _read_crisis_status(crisis_id) == "active"
    finally:
        _delete_seed([crisis_id])


def test_post_crisis_with_bbox_normalizes_to_multipolygon() -> None:
    name = f"Test crisis bbox {uuid.uuid4().hex[:8]}"

    with TestClient(app) as client:
        response = client.post(
            "/admin/crises",
            json={
                "name": name,
                "type": "flood",
                "geometry": {"bbox": [50.7, 24.4, 51.7, 26.2]},
            },
        )

    assert response.status_code == 201, response.text
    body: dict[str, Any] = response.json()
    assert body["has_geometry"] is True
    crisis_id = uuid.UUID(body["id"])

    try:
        has_geom, area, gtype = _read_crisis_geometry(crisis_id)
        assert has_geom is True
        assert area is not None and area > 0
        assert gtype == "ST_MultiPolygon"
    finally:
        _delete_seed([crisis_id])


def test_post_crisis_with_polygon_normalizes_to_multipolygon() -> None:
    name = f"Test crisis polygon {uuid.uuid4().hex[:8]}"
    poly = Polygon([(0.0, 0.0), (1.0, 0.0), (1.0, 1.0), (0.0, 1.0), (0.0, 0.0)])

    with TestClient(app) as client:
        response = client.post(
            "/admin/crises",
            json={
                "name": name,
                "type": "earthquake",
                "geometry": {"polygon": dict(mapping(poly))},
            },
        )

    assert response.status_code == 201, response.text
    crisis_id = uuid.UUID(response.json()["id"])

    try:
        has_geom, area, gtype = _read_crisis_geometry(crisis_id)
        assert has_geom is True
        assert area is not None and area > 0
        assert gtype == "ST_MultiPolygon"
    finally:
        _delete_seed([crisis_id])


class _FakeAreaReader:
    """Minimal `OvertureDivisionsReader` stand-in.

    `read_area_geometry` is the only method the create flow exercises;
    everything else asserts-unused so a stray call surfaces immediately.
    """

    def __init__(self, areas: dict[str, ResolvedArea]) -> None:
        self._areas = areas
        self.read_calls: list[str] = []

    async def search(self, q: str, country: str | None, limit: int) -> list[Area]:
        raise AssertionError("search must not be called for create flow")

    async def list_countries(self) -> list[Country]:
        raise AssertionError("list_countries must not be called for create flow")

    async def read_area_geometry(self, area_id: str) -> ResolvedArea:
        self.read_calls.append(area_id)
        if area_id not in self._areas:
            raise AreaNotFoundError(area_id)
        return self._areas[area_id]

    async def read_area_geojson(self, area_id: str) -> dict[str, object]:
        raise AssertionError("read_area_geojson must not be called for create flow")

    async def read_countries_union(self, iso2_codes: list[str]) -> bytes | None:
        raise AssertionError("read_countries_union must not be called for create flow")

    async def read_countries_geojson_union(self, iso2_codes: list[str]) -> dict[str, object] | None:
        raise AssertionError("read_countries_geojson_union must not be called for create flow")


def _qa_resolved_area() -> ResolvedArea:
    qa_poly = MultiPolygon(
        [Polygon([(50.7, 24.4), (51.7, 24.4), (51.7, 26.2), (50.7, 26.2), (50.7, 24.4)])]
    )
    return ResolvedArea(wkb=qa_poly.wkb, country="QA")


def _override_resolver_with_qa_division() -> _FakeAreaReader:
    reader = _FakeAreaReader({"qa-country": _qa_resolved_area()})
    fake_reader: OvertureDivisionsReader = reader

    def override_resolver() -> CrisisPolygonResolver:
        return CrisisPolygonResolver(reader=fake_reader)

    app.dependency_overrides[get_crisis_polygon_resolver] = override_resolver
    return reader


def test_post_crisis_with_division_id_uses_resolver_and_writes_geometry() -> None:
    name = f"Test crisis division {uuid.uuid4().hex[:8]}"

    reader = _override_resolver_with_qa_division()
    try:
        with TestClient(app) as client:
            response = client.post(
                "/admin/crises",
                json={"name": name, "type": "flood", "geometry": {"division_id": "qa-country"}},
            )
    finally:
        app.dependency_overrides.pop(get_crisis_polygon_resolver, None)

    assert response.status_code == 201, response.text
    assert reader.read_calls == ["qa-country"]
    crisis_id = uuid.UUID(response.json()["id"])

    try:
        has_geom, area, gtype = _read_crisis_geometry(crisis_id)
        assert has_geom is True
        assert area is not None and area > 0
        assert gtype == "ST_MultiPolygon"
    finally:
        _delete_seed([crisis_id])


def test_post_crisis_with_parts_unions_and_seeds_countries() -> None:
    """Multi-place union: a `division_id` part + an inline `polygon` part
    resolve to one stored MultiPolygon, and `countries` auto-seeds from the
    division part only (the inline polygon carries no country)."""
    name = f"Test crisis parts {uuid.uuid4().hex[:8]}"

    reader = _override_resolver_with_qa_division()
    # A small polygon disjoint from QA so the union keeps two members.
    inline = Polygon([(0.0, 0.0), (1.0, 0.0), (1.0, 1.0), (0.0, 1.0), (0.0, 0.0)])
    try:
        with TestClient(app) as client:
            response = client.post(
                "/admin/crises",
                json={
                    "name": name,
                    "type": "flood",
                    "geometry": {
                        "parts": [
                            {"division_id": "qa-country"},
                            {"polygon": dict(mapping(inline))},
                        ]
                    },
                },
            )
    finally:
        app.dependency_overrides.pop(get_crisis_polygon_resolver, None)

    assert response.status_code == 201, response.text
    body = response.json()
    # Only the division part hits the reader; the inline polygon does not.
    assert reader.read_calls == ["qa-country"]
    assert body["countries"] == ["QA"]
    crisis_id = uuid.UUID(body["id"])

    try:
        has_geom, area, gtype = _read_crisis_geometry(crisis_id)
        assert has_geom is True
        assert area is not None and area > 0
        assert gtype == "ST_MultiPolygon"
    finally:
        _delete_seed([crisis_id])


def test_post_crisis_with_unknown_division_id_returns_400() -> None:
    """Unknown GERS id surfaces as 400, not 500 — the route maps
    `AreaNotFoundError` to a clean client error."""
    name = f"Bad division {uuid.uuid4().hex[:8]}"

    reader = _FakeAreaReader({})  # No areas → every lookup is a miss.
    fake_reader: OvertureDivisionsReader = reader

    def override_resolver() -> CrisisPolygonResolver:
        return CrisisPolygonResolver(reader=fake_reader)

    app.dependency_overrides[get_crisis_polygon_resolver] = override_resolver
    try:
        with TestClient(app) as client:
            response = client.post(
                "/admin/crises",
                json={"name": name, "type": "flood", "geometry": {"division_id": "nope"}},
            )
    finally:
        app.dependency_overrides.pop(get_crisis_polygon_resolver, None)

    assert response.status_code == 400, response.text


def test_post_crisis_countries_only_resolves_polygon_union() -> None:
    """Priority-2: no `geometry`, but `countries` set -> resolver builds the
    polygon-union via the reader, the route writes the WKB to
    `crises.geometry`. This is the regression guard for the polygon-vs-bbox
    decision: a multi-country area is a polygon union, never a bbox union."""
    name = f"Countries-only union {uuid.uuid4().hex[:8]}"

    union = MultiPolygon(
        [
            Polygon([(0, 0), (3, 0), (3, 3), (0, 3), (0, 0)]),
            Polygon([(7, 7), (10, 7), (10, 10), (7, 10), (7, 7)]),
        ]
    )

    class _UnionReader:
        def __init__(self) -> None:
            self.union_calls: list[list[str]] = []

        async def search(self, q: str, country: str | None, limit: int) -> list[Area]:
            raise AssertionError("unused")

        async def list_countries(self) -> list[Country]:
            raise AssertionError("unused")

        async def read_area_geometry(self, area_id: str) -> ResolvedArea:
            raise AssertionError("unused")

        async def read_area_geojson(self, area_id: str) -> dict[str, object]:
            raise AssertionError("unused")

        async def read_countries_union(self, iso2_codes: list[str]) -> bytes | None:
            self.union_calls.append(list(iso2_codes))
            return union.wkb

        async def read_countries_geojson_union(
            self, iso2_codes: list[str]
        ) -> dict[str, object] | None:
            raise AssertionError("unused")

    reader = _UnionReader()
    fake: OvertureDivisionsReader = reader

    def override_resolver() -> CrisisPolygonResolver:
        return CrisisPolygonResolver(reader=fake)

    app.dependency_overrides[get_crisis_polygon_resolver] = override_resolver
    try:
        with TestClient(app) as client:
            response = client.post(
                "/admin/crises",
                json={"name": name, "type": "flood", "countries": ["IQ", "SY"]},
            )
    finally:
        app.dependency_overrides.pop(get_crisis_polygon_resolver, None)

    assert response.status_code == 201, response.text
    assert reader.union_calls == [["IQ", "SY"]]
    body = response.json()
    assert body["has_geometry"] is True
    assert body["countries"] == ["IQ", "SY"]
    crisis_id = uuid.UUID(body["id"])

    try:
        has_geom, area, gtype = _read_crisis_geometry(crisis_id)
        assert has_geom is True
        assert area is not None and area > 0
        assert gtype == "ST_MultiPolygon"
    finally:
        _delete_seed([crisis_id])


def test_post_crisis_with_invalid_geometry_block_returns_400() -> None:
    """Exactly-one rule is enforced: bbox + division_id at once is rejected."""
    with TestClient(app) as client:
        response = client.post(
            "/admin/crises",
            json={
                "name": f"Bad crisis {uuid.uuid4().hex[:8]}",
                "type": "flood",
                "geometry": {
                    "bbox": [0.0, 0.0, 1.0, 1.0],
                    "division_id": "qa-country",
                },
            },
        )

    assert response.status_code == 422, response.text


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


def test_post_crisis_with_division_id_auto_seeds_countries() -> None:
    """Picking an area auto-seeds `crises.countries` from the area's
    `country` field when the request omits an explicit `countries`."""
    name = f"Auto seed {uuid.uuid4().hex[:8]}"
    _override_resolver_with_qa_division()
    try:
        with TestClient(app) as client:
            response = client.post(
                "/admin/crises",
                json={
                    "name": name,
                    "type": "flood",
                    "geometry": {"division_id": "qa-country"},
                },
            )
    finally:
        app.dependency_overrides.pop(get_crisis_polygon_resolver, None)

    assert response.status_code == 201, response.text
    body = response.json()
    assert body["countries"] == ["QA"]
    crisis_id = uuid.UUID(body["id"])
    try:
        _, countries, _, _ = _read_metadata(crisis_id)
        assert countries == ["QA"]
    finally:
        _delete_seed([crisis_id])


def test_post_crisis_explicit_countries_wins_over_auto_seed() -> None:
    name = f"Explicit countries {uuid.uuid4().hex[:8]}"
    _override_resolver_with_qa_division()
    try:
        with TestClient(app) as client:
            response = client.post(
                "/admin/crises",
                json={
                    "name": name,
                    "type": "flood",
                    "countries": ["QA", "BH"],
                    "geometry": {"division_id": "qa-country"},
                },
            )
    finally:
        app.dependency_overrides.pop(get_crisis_polygon_resolver, None)

    assert response.status_code == 201, response.text
    body = response.json()
    assert body["countries"] == ["QA", "BH"]
    crisis_id = uuid.UUID(body["id"])
    try:
        _, countries, _, _ = _read_metadata(crisis_id)
        assert countries == ["QA", "BH"]
    finally:
        _delete_seed([crisis_id])


def test_post_crisis_round_trips_event_time_window() -> None:
    name = f"Time window {uuid.uuid4().hex[:8]}"
    started = "2026-04-01T00:00:00+00:00"
    ended = "2026-04-15T00:00:00+00:00"

    with TestClient(app) as client:
        response = client.post(
            "/admin/crises",
            json={
                "name": name,
                "type": "flood",
                "started_at": started,
                "ended_at": ended,
            },
        )
    assert response.status_code == 201, response.text
    body = response.json()
    crisis_id = uuid.UUID(body["id"])
    try:
        assert body["started_at"].startswith("2026-04-01")
        assert body["ended_at"].startswith("2026-04-15")
        _, _, db_started, db_ended = _read_metadata(crisis_id)
        assert db_started is not None
        assert db_ended is not None
    finally:
        _delete_seed([crisis_id])


def test_post_crisis_with_inverted_time_window_fails() -> None:
    """`ended_at < started_at` is rejected by the DB CHECK; the route surfaces
    it as a 500 (IntegrityError) since handler-level validation is not added."""
    name = f"Bad time {uuid.uuid4().hex[:8]}"

    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.post(
            "/admin/crises",
            json={
                "name": name,
                "type": "flood",
                "started_at": "2026-04-15T00:00:00+00:00",
                "ended_at": "2026-04-01T00:00:00+00:00",
            },
        )
    assert response.status_code == 500


def test_post_crisis_with_invalid_iso2_country_fails() -> None:
    """Free-text country names violate `crises_countries_iso2_chk`."""
    name = f"Bad country {uuid.uuid4().hex[:8]}"

    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.post(
            "/admin/crises",
            json={"name": name, "type": "flood", "countries": ["Qatar"]},
        )
    assert response.status_code == 500
