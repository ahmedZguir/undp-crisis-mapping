"""DB-backed tests for the server-side map surface.

Covers `map_queries.fetch_map_clusters` / `fetch_map_points` and the
`POST /admin/crises/{id}/map` route:

- clusters: exact totals + per-class counts, stored-3857 grid snap, the
  distinct-coordinate (stacked-building) signal, structured filters applied;
- points: the exact viewport set, and the dense-viewport fallback to clusters;
- zoom regime selection through the route;
- semantic clusters: end-to-end SQL over seeded embeddings, the 50K-cap flag,
  and the anomalies-only predicate.

Requires `supabase start` locally for the Postgres side.
"""

from __future__ import annotations

import asyncio
import math
import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from api.admin import map_queries
from api.admin.map_queries import (
    cluster_cell_size_3857,
    fetch_map_clusters,
    fetch_map_points,
)
from api.ai.embeddings import EMBEDDING_VERSION, QWEN3_EMBEDDING_DIM
from api.core.config import get_settings
from api.main import app
from api.schemas.admin_search import SearchRequest

pytestmark = pytest.mark.integration

# Remote bbox so seeded points don't collide with other DB tests' fixtures.
_LAT = 10.5
_LNG = 20.5
_BBOX = (20.0, 10.0, 21.0, 11.0)  # (w, s, e, n) — comfortably contains _LAT/_LNG


# --- Seed helpers --------------------------------------------------------


async def _seed_crisis(url: str) -> uuid.UUID:
    crisis_id = uuid.uuid4()
    suffix = uuid.uuid4().hex[:8]
    engine = create_async_engine(url)
    try:
        async with engine.begin() as conn:
            await conn.execute(
                text(
                    "insert into public.crises (id, name, status, type, countries) "
                    "values (:id, :n, 'active', 'flood', ARRAY['QA']::text[])"
                ),
                {"id": str(crisis_id), "n": f"Map test {suffix}"},
            )
    finally:
        await engine.dispose()
    return crisis_id


async def _seed_building(url: str, *, centroid: tuple[float, float]) -> uuid.UUID:
    building_id = uuid.uuid4()
    lat, lng = centroid
    d = 0.00001
    wkt = (
        "MULTIPOLYGON((("
        f"{lng - d} {lat - d},{lng + d} {lat - d},{lng + d} {lat + d},"
        f"{lng - d} {lat + d},{lng - d} {lat - d})))"
    )
    engine = create_async_engine(url)
    try:
        async with engine.begin() as conn:
            await conn.execute(
                text(
                    "insert into public.buildings (id, source, source_id, footprint) "
                    "values (:id, 'test', :src, st_geogfromtext(:wkt))"
                ),
                {"id": str(building_id), "src": uuid.uuid4().hex, "wkt": wkt},
            )
    finally:
        await engine.dispose()
    return building_id


async def _seed_report(
    url: str,
    *,
    crisis_id: uuid.UUID,
    damage_class: str = "partial",
    infra: list[str] | None = None,
    building_id: uuid.UUID | None = None,
    location: tuple[float, float] | None = None,
    embedding: list[float] | None = None,
) -> uuid.UUID:
    report_id = uuid.uuid4()
    location_sql = (
        "st_setsrid(st_makepoint(:lng, :lat), 4326)::geography" if location is not None else "null"
    )
    params: dict[str, object | None] = {
        "id": str(report_id),
        "crisis_id": str(crisis_id),
        "dc": damage_class,
        "photo": f"test/{report_id}.jpg",
        "infra": infra,
        "building_id": str(building_id) if building_id is not None else None,
    }
    if location is not None:
        params["lat"], params["lng"] = location[0], location[1]
    engine = create_async_engine(url)
    try:
        async with engine.begin() as conn:
            await conn.execute(
                text(
                    "insert into public.reports "
                    "  (id, crisis_id, damage_class, photo_path, infra_type, building_id, "
                    "   location, route_description) "
                    "values (:id, :crisis_id, :dc, :photo, cast(:infra as text[]), "
                    f"        :building_id, {location_sql}, 'test directions')"
                ),
                params,
            )
            if embedding is not None:
                await conn.execute(
                    text(
                        "insert into public.report_embeddings "
                        "  (report_id, embedding, model, dimension, embedding_version) "
                        "values (:rid, cast(:emb as halfvec), 'test', :dim, :ver)"
                    ),
                    {
                        "rid": str(report_id),
                        "emb": "[" + ",".join(repr(x) for x in embedding) + "]",
                        "dim": QWEN3_EMBEDDING_DIM,
                        "ver": EMBEDDING_VERSION,
                    },
                )
    finally:
        await engine.dispose()
    return report_id


async def _cleanup(url: str, *, crisis_ids: list[uuid.UUID], building_ids: list[uuid.UUID]) -> None:
    engine = create_async_engine(url)
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
            if building_ids:
                await conn.execute(
                    text("delete from public.buildings where id = any(:ids)"),
                    {"ids": [str(i) for i in building_ids]},
                )
    finally:
        await engine.dispose()


def _query_vec() -> list[float]:
    """Unit query vector e0 = [1, 0, 0, ...]."""
    v = [0.0] * QWEN3_EMBEDDING_DIM
    v[0] = 1.0
    return v


def _emb_for_similarity(s: float) -> list[float]:
    """A unit vector whose cosine similarity with `_query_vec()` is `s`:
    [s, sqrt(1-s^2), 0, ...]."""
    v = [0.0] * QWEN3_EMBEDDING_DIM
    v[0] = s
    v[1] = math.sqrt(max(0.0, 1.0 - s * s))
    return v


async def _clusters(
    url: str,
    crisis_id: uuid.UUID,
    *,
    zoom: float,
    request: SearchRequest | None = None,
    query_vector: list[float] | None = None,
    anomalies_only: bool = False,
) -> tuple[list[map_queries.MapClusterCell], int, int | None, bool]:
    engine = create_async_engine(url)
    try:
        async with AsyncSession(engine) as session:
            return await fetch_map_clusters(
                session,
                crisis_id=crisis_id,
                request=request or SearchRequest(),
                bbox=_BBOX,
                zoom=zoom,
                query_vector=query_vector,
                anomalies_only=anomalies_only,
            )
    finally:
        await engine.dispose()


async def _points(
    url: str,
    crisis_id: uuid.UUID,
    *,
    request: SearchRequest | None = None,
    query_vector: list[float] | None = None,
    anomalies_only: bool = False,
) -> tuple[list[map_queries.MapPoint], bool]:
    engine = create_async_engine(url)
    try:
        async with AsyncSession(engine) as session:
            return await fetch_map_points(
                session,
                crisis_id=crisis_id,
                request=request or SearchRequest(),
                bbox=_BBOX,
                query_vector=query_vector,
                anomalies_only=anomalies_only,
            )
    finally:
        await engine.dispose()


# --- Unit: cell size -----------------------------------------------------


def test_cell_size_shrinks_with_zoom() -> None:
    """Grid cell halves per zoom level (MapLibre clusterRadius:40 in 3857)."""
    assert cluster_cell_size_3857(2) > cluster_cell_size_3857(10) > cluster_cell_size_3857(14)
    # Halving relation between adjacent zooms.
    assert cluster_cell_size_3857(10) == pytest.approx(cluster_cell_size_3857(11) * 2)


# --- Clusters ------------------------------------------------------------


def test_clusters_exact_counts_and_filter() -> None:
    url = get_settings().database_url
    crisis_id = asyncio.run(_seed_crisis(url))

    async def seed() -> None:
        await _seed_report(url, crisis_id=crisis_id, damage_class="complete", location=(_LAT, _LNG))
        await _seed_report(url, crisis_id=crisis_id, damage_class="complete", location=(_LAT, _LNG))
        await _seed_report(url, crisis_id=crisis_id, damage_class="partial", location=(_LAT, _LNG))
        await _seed_report(url, crisis_id=crisis_id, damage_class="minimal", location=(_LAT, _LNG))

    asyncio.run(seed())
    try:
        cells, total, tmc, capped = asyncio.run(_clusters(url, crisis_id, zoom=6))
        assert total == 4
        assert tmc is None
        assert capped is False
        # All four share one coordinate → one cell at any zoom.
        assert len(cells) == 1
        cell = cells[0]
        assert cell.total == 4
        assert cell.complete == 2
        assert cell.partial == 1
        assert cell.minimal == 1
        # The cell centroid round-trips back to the seeded coordinate.
        assert cell.lat == pytest.approx(_LAT, abs=1e-4)
        assert cell.lng == pytest.approx(_LNG, abs=1e-4)

        # damage_class filter narrows the aggregate.
        cells_c, total_c, _, _ = asyncio.run(
            _clusters(url, crisis_id, zoom=6, request=SearchRequest(damage_class="complete"))
        )
        assert total_c == 2
        assert sum(c.complete for c in cells_c) == 2
        assert sum(c.partial + c.minimal for c in cells_c) == 0
    finally:
        asyncio.run(_cleanup(url, crisis_ids=[crisis_id], building_ids=[]))


def test_singleton_cell_carries_report_passthrough() -> None:
    """A cell holding exactly one report carries its id + class + provenance so
    the client can draw it as a real pin (clickable) instead of a "1" bubble."""
    url = get_settings().database_url
    crisis_id = asyncio.run(_seed_crisis(url))
    report_id = asyncio.run(
        _seed_report(url, crisis_id=crisis_id, damage_class="complete", location=(_LAT, _LNG))
    )
    try:
        cells, total, _, _ = asyncio.run(_clusters(url, crisis_id, zoom=4))
        assert total == 1
        assert len(cells) == 1
        cell = cells[0]
        assert cell.total == 1
        assert cell.report_id == report_id
        assert cell.damage_class == "complete"
        assert cell.location_source == "submitted_pin"
    finally:
        asyncio.run(_cleanup(url, crisis_ids=[crisis_id], building_ids=[]))


def test_multi_report_cell_has_no_singleton_passthrough() -> None:
    """A real cluster (total > 1) leaves the singleton fields null."""
    url = get_settings().database_url
    crisis_id = asyncio.run(_seed_crisis(url))

    async def seed() -> None:
        await _seed_report(url, crisis_id=crisis_id, location=(_LAT, _LNG))
        await _seed_report(url, crisis_id=crisis_id, location=(_LAT, _LNG))

    asyncio.run(seed())
    try:
        cells, total, _, _ = asyncio.run(_clusters(url, crisis_id, zoom=4))
        assert total == 2
        assert len(cells) == 1
        assert cells[0].total == 2
        assert cells[0].report_id is None
        assert cells[0].damage_class is None
    finally:
        asyncio.run(_cleanup(url, crisis_ids=[crisis_id], building_ids=[]))


def test_clusters_distinct_point_count_for_stacked_building() -> None:
    """Reports stacked on one building share an identical map_point, so the
    cell reports distinct_point_count == 1 (the 'click → list' signal)."""
    url = get_settings().database_url
    crisis_id = asyncio.run(_seed_crisis(url))
    building_id = asyncio.run(_seed_building(url, centroid=(_LAT, _LNG)))

    async def seed() -> None:
        # No GPS → map_point falls back to the building centroid for all three.
        for dc in ("complete", "partial", "minimal"):
            await _seed_report(url, crisis_id=crisis_id, damage_class=dc, building_id=building_id)

    asyncio.run(seed())
    try:
        cells, total, _, _ = asyncio.run(_clusters(url, crisis_id, zoom=10))
        assert total == 3
        assert len(cells) == 1
        assert cells[0].total == 3
        assert cells[0].distinct_point_count == 1
    finally:
        asyncio.run(_cleanup(url, crisis_ids=[crisis_id], building_ids=[building_id]))


def test_clusters_split_into_more_cells_when_zoomed_in() -> None:
    """Two well-separated points share a cell when zoomed far out and split
    into two when zoomed in — the zoom-derived 3857 grid at work."""
    url = get_settings().database_url
    crisis_id = asyncio.run(_seed_crisis(url))

    async def seed() -> None:
        await _seed_report(url, crisis_id=crisis_id, location=(_LAT, _LNG))
        await _seed_report(url, crisis_id=crisis_id, location=(_LAT + 0.1, _LNG + 0.1))

    asyncio.run(seed())
    try:
        cells_out, total_out, _, _ = asyncio.run(_clusters(url, crisis_id, zoom=4))
        cells_in, total_in, _, _ = asyncio.run(_clusters(url, crisis_id, zoom=15))
        # Total is exact and preserved regardless of cell size.
        assert total_out == 2
        assert total_in == 2
        assert len(cells_in) >= len(cells_out)
        assert len(cells_in) == 2
    finally:
        asyncio.run(_cleanup(url, crisis_ids=[crisis_id], building_ids=[]))


# --- Points + fallback ---------------------------------------------------


def test_points_mode_returns_exact_viewport_set() -> None:
    url = get_settings().database_url
    crisis_id = asyncio.run(_seed_crisis(url))

    async def seed() -> None:
        await _seed_report(url, crisis_id=crisis_id, damage_class="complete", location=(_LAT, _LNG))
        await _seed_report(
            url, crisis_id=crisis_id, damage_class="minimal", location=(_LAT + 0.01, _LNG)
        )

    asyncio.run(seed())
    try:
        points, overflowed = asyncio.run(_points(url, crisis_id))
        assert overflowed is False
        assert len(points) == 2
        classes = {p.damage_class for p in points}
        assert classes == {"complete", "minimal"}
        assert all(p.map_point is not None for p in points)
    finally:
        asyncio.run(_cleanup(url, crisis_ids=[crisis_id], building_ids=[]))


def test_points_overflow_falls_back(monkeypatch: pytest.MonkeyPatch) -> None:
    """When the viewport has more than POINTS_CEILING points, points mode
    overflows so the route can switch to clusters."""
    url = get_settings().database_url
    crisis_id = asyncio.run(_seed_crisis(url))

    async def seed() -> None:
        for _ in range(4):
            await _seed_report(url, crisis_id=crisis_id, location=(_LAT, _LNG))

    asyncio.run(seed())
    # Tighten the ceiling so 4 seeded points overflow it.
    monkeypatch.setattr(map_queries, "POINTS_CEILING", 2)
    try:
        points, overflowed = asyncio.run(_points(url, crisis_id))
        assert overflowed is True
        assert points == []
    finally:
        asyncio.run(_cleanup(url, crisis_ids=[crisis_id], building_ids=[]))


# --- Route: zoom regime --------------------------------------------------


def test_route_selects_regime_by_zoom() -> None:
    url = get_settings().database_url
    crisis_id = asyncio.run(_seed_crisis(url))
    asyncio.run(
        _seed_report(url, crisis_id=crisis_id, damage_class="partial", location=(_LAT, _LNG))
    )
    try:
        with TestClient(app) as client:
            low = client.post(
                f"/admin/crises/{crisis_id}/map",
                json={"bbox": list(_BBOX), "zoom": 6},
            )
            high = client.post(
                f"/admin/crises/{crisis_id}/map",
                json={"bbox": list(_BBOX), "zoom": 16},
            )
        assert low.status_code == 200, low.text
        assert low.json()["mode"] == "clusters"
        assert low.json()["total"] == 1
        assert high.status_code == 200, high.text
        assert high.json()["mode"] == "points"
        assert len(high.json()["items"]) == 1
    finally:
        asyncio.run(_cleanup(url, crisis_ids=[crisis_id], building_ids=[]))


def test_route_unknown_crisis_404() -> None:
    with TestClient(app) as client:
        resp = client.post(
            f"/admin/crises/{uuid.uuid4()}/map",
            json={"bbox": list(_BBOX), "zoom": 6},
        )
    assert resp.status_code == 404


# --- Semantic clusters ---------------------------------------------------


def test_semantic_clusters_smoke() -> None:
    """The semantic cluster SQL runs end-to-end over seeded embeddings: it
    aggregates the matched set, and reports capped=False / total_match_count
    None when well under the 50K cap."""
    url = get_settings().database_url
    crisis_id = asyncio.run(_seed_crisis(url))

    async def seed() -> None:
        for _ in range(3):
            await _seed_report(
                url,
                crisis_id=crisis_id,
                location=(_LAT, _LNG),
                embedding=_emb_for_similarity(0.9),
            )

    asyncio.run(seed())
    try:
        cells, total, tmc, capped = asyncio.run(
            _clusters(
                url,
                crisis_id,
                zoom=6,
                request=SearchRequest(query="x", strictness="loose"),
                query_vector=_query_vec(),
            )
        )
        assert total == 3
        assert capped is False
        assert tmc is None
        assert sum(c.total for c in cells) == 3
    finally:
        asyncio.run(_cleanup(url, crisis_ids=[crisis_id], building_ids=[]))


def test_semantic_clusters_anomalies_only() -> None:
    """anomalies-only keeps the high-distance (low-similarity) outlier and
    drops the tight cluster of normal matches."""
    url = get_settings().database_url
    crisis_id = asyncio.run(_seed_crisis(url))

    async def seed() -> None:
        for _ in range(5):
            await _seed_report(
                url,
                crisis_id=crisis_id,
                location=(_LAT, _LNG),
                embedding=_emb_for_similarity(0.9),  # distance 0.1
            )
        await _seed_report(
            url,
            crisis_id=crisis_id,
            location=(_LAT, _LNG),
            embedding=_emb_for_similarity(0.3),  # distance 0.7 → the outlier
        )

    asyncio.run(seed())
    try:
        # Without the filter, all six match (loose floor 0.25 → max_distance 0.75).
        _, total_all, _, _ = asyncio.run(
            _clusters(
                url,
                crisis_id,
                zoom=6,
                request=SearchRequest(query="x", strictness="loose"),
                query_vector=_query_vec(),
            )
        )
        assert total_all == 6

        # anomalies-only keeps just the outlier.
        _, total_anom, _, _ = asyncio.run(
            _clusters(
                url,
                crisis_id,
                zoom=6,
                request=SearchRequest(query="x", strictness="loose"),
                query_vector=_query_vec(),
                anomalies_only=True,
            )
        )
        assert total_anom == 1
    finally:
        asyncio.run(_cleanup(url, crisis_ids=[crisis_id], building_ids=[]))
