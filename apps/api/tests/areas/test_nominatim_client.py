"""Unit tests for `OSMNominatimClient` against a mocked httpx transport.

We do not hit the public Nominatim service from CI; every test injects an
`httpx.AsyncClient` backed by `httpx.MockTransport` so the recorded JSON
shapes are pinned and the politeness spacing is testable deterministically.
"""

from __future__ import annotations

import asyncio
import json
import time

import httpx
import pytest

from api.areas.nominatim_client import (
    NOMINATIM_MIN_SPACING_SECONDS,
    OSMNominatimClient,
)

pytestmark = pytest.mark.asyncio


def _make_response(payload: object, status_code: int = 200) -> httpx.Response:
    return httpx.Response(status_code, content=json.dumps(payload).encode())


def _fixture_relation_polygon() -> dict[str, object]:
    """A trimmed Nominatim row for a small Iraqi locality."""
    return {
        "osm_type": "relation",
        "osm_id": 12345,
        "display_name": "Tikrit, Saladin Governorate, Iraq",
        "addresstype": "city",
        "address": {"country_code": "iq"},
        "boundingbox": ["34.5", "34.7", "43.6", "43.8"],
        "geojson": {
            "type": "Polygon",
            "coordinates": [[[43.6, 34.5], [43.8, 34.5], [43.8, 34.7], [43.6, 34.5]]],
        },
    }


def _fixture_node_no_polygon() -> dict[str, object]:
    """A node-type Nominatim row — no usable polygon, must be skipped."""
    return {
        "osm_type": "node",
        "osm_id": 99,
        "display_name": "Anywhere, Earth",
        "addresstype": "village",
        "address": {"country_code": "iq"},
        "boundingbox": ["1.0", "1.1", "1.0", "1.1"],
        "geojson": {"type": "Point", "coordinates": [1.0, 1.0]},
    }


def _client_with_handler(handler: object) -> OSMNominatimClient:
    transport = httpx.MockTransport(handler)  # pyright: ignore[reportArgumentType]
    http = httpx.AsyncClient(transport=transport)
    return OSMNominatimClient(http_client=http)


async def test_search_maps_relation_row_to_osm_area() -> None:
    captured: dict[str, httpx.Request] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["req"] = request
        return _make_response([_fixture_relation_polygon()])

    client = _client_with_handler(handler)
    try:
        results = await client.search("tikrit", country="IQ", limit=5)
    finally:
        await client.aclose()

    assert len(results) == 1
    hit = results[0]
    assert hit.osm_id == "osm:relation/12345"
    assert hit.name == "Tikrit"
    assert hit.subtype == "osm:city"
    assert hit.country == "IQ"
    assert hit.parents == ["Saladin Governorate", "Iraq"]
    # bbox is normalised from Nominatim's [ymin, ymax, xmin, xmax] to
    # our (xmin, ymin, xmax, ymax) ordering.
    assert hit.bbox == (43.6, 34.5, 43.8, 34.7)
    assert hit.geometry["type"] == "Polygon"

    req = captured["req"]
    assert req.url.params["q"] == "tikrit"
    assert req.url.params["countrycodes"] == "iq"
    assert req.url.params["polygon_geojson"] == "1"
    assert req.url.params["limit"] == "5"
    assert "undp-crisis-mapping" in req.headers["user-agent"]


async def test_search_skips_node_type_results() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return _make_response([_fixture_node_no_polygon(), _fixture_relation_polygon()])

    client = _client_with_handler(handler)
    try:
        results = await client.search("tikrit", country=None, limit=5)
    finally:
        await client.aclose()

    assert [r.osm_id for r in results] == ["osm:relation/12345"]


async def test_search_network_failure_returns_empty_list() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("simulated network failure")

    client = _client_with_handler(handler)
    try:
        results = await client.search("tikrit", country=None, limit=5)
    finally:
        await client.aclose()
    assert results == []


async def test_search_json_decode_error_returns_empty_list() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"not json")

    client = _client_with_handler(handler)
    try:
        results = await client.search("tikrit", country=None, limit=5)
    finally:
        await client.aclose()
    assert results == []


async def test_search_http_5xx_returns_empty_list() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return _make_response([], status_code=503)

    client = _client_with_handler(handler)
    try:
        results = await client.search("tikrit", country=None, limit=5)
    finally:
        await client.aclose()
    assert results == []


async def test_search_enforces_minimum_spacing_between_calls() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return _make_response([])

    client = _client_with_handler(handler)
    try:
        first_start = time.monotonic()
        await client.search("a", country=None, limit=1)
        second_start = time.monotonic()
        await client.search("b", country=None, limit=1)
        second_end = time.monotonic()
    finally:
        await client.aclose()

    # Second call must wait at least NOMINATIM_MIN_SPACING_SECONDS after
    # the first one completed.
    assert (second_end - first_start) >= NOMINATIM_MIN_SPACING_SECONDS - 0.05
    # And the spacing happens inside the second `search` call (not before).
    assert (second_end - second_start) >= NOMINATIM_MIN_SPACING_SECONDS - 0.05


async def test_search_lock_serialises_concurrent_calls() -> None:
    in_flight = 0
    max_in_flight = 0

    async def handler(_: httpx.Request) -> httpx.Response:
        nonlocal in_flight, max_in_flight
        in_flight += 1
        max_in_flight = max(max_in_flight, in_flight)
        await asyncio.sleep(0.01)
        in_flight -= 1
        return _make_response([])

    transport = httpx.MockTransport(handler)  # pyright: ignore[reportArgumentType]
    http = httpx.AsyncClient(transport=transport)
    client = OSMNominatimClient(http_client=http)
    try:
        await asyncio.gather(
            client.search("a", country=None, limit=1),
            client.search("b", country=None, limit=1),
            client.search("c", country=None, limit=1),
        )
    finally:
        await client.aclose()

    assert max_in_flight == 1
