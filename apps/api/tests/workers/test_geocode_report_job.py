"""Integration tests for the `geocode_report` Arq job.

Runs against local Supabase wrapped in a rolled-back outer transaction, like
the enrichment-job tests. The LLM extractor is monkeypatched (the prompt is
not under test here); the Nominatim client is a *real* `OSMNominatimClient`
backed by an `httpx.MockTransport`, so the job exercises the real
`geocode_search` + projection path against pinned JSON.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import AsyncGenerator, AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncConnection,
    AsyncEngine,
    async_sessionmaker,
    create_async_engine,
)

from api.ai.client import AIClients
from api.ai.llm import LLMOutputError
from api.ai.types import ToponymMention
from api.areas.nominatim_client import OSMNominatimClient
from api.core.config import get_settings
from api.workers import geocoding

pytestmark = pytest.mark.integration


# AOI: lon 36.10..36.25, lat 36.15..36.25. A small neighborhood polygon nested
# inside it that Nominatim "returns" for any query.
_AOI_WKT = "SRID=4326;MULTIPOLYGON(((36.10 36.15,36.25 36.15,36.25 36.25,36.10 36.25,36.10 36.15)))"


def _neighborhood_row() -> dict[str, Any]:
    return {
        "osm_type": "relation",
        "osm_id": 1,
        "display_name": "Yeni Cami Mahallesi, Antakya",
        "addresstype": "neighbourhood",
        "address": {"country_code": "tr"},
        "boundingbox": ["36.198", "36.200", "36.160", "36.162"],
        "geojson": {
            "type": "Polygon",
            "coordinates": [
                [
                    [36.160, 36.198],
                    [36.162, 36.198],
                    [36.162, 36.200],
                    [36.160, 36.200],
                    [36.160, 36.198],
                ]
            ],
        },
    }


def _mock_nominatim(payload: list[dict[str, Any]]) -> OSMNominatimClient:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=json.dumps(payload).encode())

    transport = httpx.MockTransport(handler)  # pyright: ignore[reportArgumentType]
    return OSMNominatimClient(http_client=httpx.AsyncClient(transport=transport))


@dataclass
class _Harness:
    sessionmaker: async_sessionmaker[Any]
    conn: AsyncConnection
    crisis_id: uuid.UUID

    def make_ctx(self, nominatim: OSMNominatimClient) -> dict[str, object]:
        return {
            "db_sessionmaker": self.sessionmaker,
            "ai_clients": AIClients(text=None, embedding=None),
            "nominatim_client": nominatim,
            "job_try": 1,
            "max_tries": 1,
        }


@pytest_asyncio.fixture
async def engine() -> AsyncIterator[AsyncEngine]:
    eng = create_async_engine(get_settings().database_url)
    try:
        yield eng
    finally:
        await eng.dispose()


@asynccontextmanager
async def _seeded_crisis(conn: AsyncConnection) -> AsyncGenerator[uuid.UUID]:
    crisis_id = uuid.uuid4()
    await conn.execute(
        text(
            "insert into public.crises (id, name, status, created_at, geometry, countries) "
            "values (:id, :n, 'active', :t, cast(:geom as geography), :countries)"
        ),
        {
            "id": str(crisis_id),
            "n": f"Geocode test crisis {uuid.uuid4().hex[:8]}",
            "t": datetime.now(UTC) - timedelta(minutes=1),
            "geom": _AOI_WKT,
            "countries": ["TR"],
        },
    )
    yield crisis_id


async def _seed_report(
    conn: AsyncConnection, crisis_id: uuid.UUID, *, route_description: str | None
) -> uuid.UUID:
    report_id = uuid.uuid4()
    await conn.execute(
        text(
            "insert into public.reports "
            "  (id, crisis_id, damage_class, route_description, photo_path) "
            "values (:id, :cid, 'minimal', :route, :path)"
        ),
        {
            "id": str(report_id),
            "cid": str(crisis_id),
            "route": route_description,
            "path": "aa/bb/aabb.jpg",
        },
    )
    await conn.execute(
        text("insert into public.report_geocodes (report_id) values (:id)"),
        {"id": str(report_id)},
    )
    return report_id


@pytest_asyncio.fixture
async def harness(engine: AsyncEngine) -> AsyncIterator[_Harness]:
    async with engine.connect() as conn:
        outer = await conn.begin()
        try:
            sessionmaker = async_sessionmaker(
                bind=conn,
                expire_on_commit=False,
                join_transaction_mode="create_savepoint",
            )
            async with _seeded_crisis(conn) as crisis_id:
                yield _Harness(sessionmaker=sessionmaker, conn=conn, crisis_id=crisis_id)
        finally:
            await outer.rollback()


async def _geocode_row(harness: _Harness, report_id: uuid.UUID) -> Any:
    return (
        await harness.conn.execute(
            text(
                "select status, lat, lon, granularity_tier, area_only, "
                "       confidence, source, toponyms "
                "from public.report_geocodes where report_id = :id"
            ),
            {"id": str(report_id)},
        )
    ).one()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


async def test_geocode_report_resolves_to_a_point(
    harness: _Harness, monkeypatch: pytest.MonkeyPatch
) -> None:
    report_id = await _seed_report(
        harness.conn, harness.crisis_id, route_description="behind the Yeni Cami"
    )

    async def fake_extract(_text: str, *, clients: Any) -> list[ToponymMention]:
        _ = clients
        return [ToponymMention(surface_form="Yeni Cami", type_hint="landmark")]

    monkeypatch.setattr(geocoding, "extract_toponyms", fake_extract)
    nominatim = _mock_nominatim([_neighborhood_row()])

    try:
        await geocoding.geocode_report(harness.make_ctx(nominatim), str(report_id))
    finally:
        await nominatim.aclose()

    row = await _geocode_row(harness, report_id)
    assert row.status == "ready"
    assert row.lat is not None and row.lon is not None
    # Resolved centroid lands inside the neighborhood polygon.
    assert 36.198 <= float(row.lat) <= 36.200
    assert 36.160 <= float(row.lon) <= 36.162
    assert row.granularity_tier == "neighborhood"
    assert row.source == "osm"
    audit = row.toponyms
    assert isinstance(audit, list) and audit[0]["surface_form"] == "Yeni Cami"


async def test_geocode_report_no_place_named_writes_ready_without_coords(
    harness: _Harness, monkeypatch: pytest.MonkeyPatch
) -> None:
    report_id = await _seed_report(
        harness.conn, harness.crisis_id, route_description="please send help, walls cracking"
    )

    async def fake_extract(_text: str, *, clients: Any) -> list[ToponymMention]:
        _ = clients
        return []

    monkeypatch.setattr(geocoding, "extract_toponyms", fake_extract)
    nominatim = _mock_nominatim([])

    try:
        await geocoding.geocode_report(harness.make_ctx(nominatim), str(report_id))
    finally:
        await nominatim.aclose()

    row = await _geocode_row(harness, report_id)
    assert row.status == "ready"
    assert row.lat is None
    assert row.lon is None


async def test_geocode_report_out_of_aoi_hit_is_dropped(
    harness: _Harness, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A geocode that lands outside the crisis polygon resolves to nothing —
    the centroid-collapse guard never gets a bogus point."""
    report_id = await _seed_report(
        harness.conn, harness.crisis_id, route_description="some far-away place"
    )

    async def fake_extract(_text: str, *, clients: Any) -> list[ToponymMention]:
        _ = clients
        return [ToponymMention(surface_form="Far Away", type_hint="area")]

    far_row = _neighborhood_row()
    far_row["boundingbox"] = ["10.0", "10.1", "10.0", "10.1"]
    far_row["geojson"] = {
        "type": "Polygon",
        "coordinates": [[[10.0, 10.0], [10.1, 10.0], [10.1, 10.1], [10.0, 10.0]]],
    }

    monkeypatch.setattr(geocoding, "extract_toponyms", fake_extract)
    nominatim = _mock_nominatim([far_row])

    try:
        await geocoding.geocode_report(harness.make_ctx(nominatim), str(report_id))
    finally:
        await nominatim.aclose()

    row = await _geocode_row(harness, report_id)
    assert row.status == "ready"
    assert row.lat is None


async def test_geocode_report_empty_route_marks_skipped(
    harness: _Harness, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Defensive: if the job runs on a report with no route text, it marks
    the sidecar skipped rather than calling the LLM."""
    report_id = await _seed_report(harness.conn, harness.crisis_id, route_description="   ")

    called = False

    async def fake_extract(_text: str, *, clients: Any) -> list[ToponymMention]:
        nonlocal called
        called = True
        return []

    monkeypatch.setattr(geocoding, "extract_toponyms", fake_extract)
    nominatim = _mock_nominatim([])

    try:
        await geocoding.geocode_report(harness.make_ctx(nominatim), str(report_id))
    finally:
        await nominatim.aclose()

    row = await _geocode_row(harness, report_id)
    assert row.status == "skipped"
    assert called is False


async def test_geocode_report_parse_error_writes_failed(
    harness: _Harness, monkeypatch: pytest.MonkeyPatch
) -> None:
    report_id = await _seed_report(
        harness.conn, harness.crisis_id, route_description="Atatürk Caddesi"
    )

    async def fake_extract(_text: str, *, clients: Any) -> list[ToponymMention]:
        _ = clients
        raise LLMOutputError("model output is not a JSON array")

    monkeypatch.setattr(geocoding, "extract_toponyms", fake_extract)
    nominatim = _mock_nominatim([])

    try:
        await geocoding.geocode_report(harness.make_ctx(nominatim), str(report_id))
    finally:
        await nominatim.aclose()

    row = (
        await harness.conn.execute(
            text("select status, error from public.report_geocodes where report_id = :id"),
            {"id": str(report_id)},
        )
    ).one()
    assert row.status == "failed"
    assert "parse_error" in (row.error or "")
