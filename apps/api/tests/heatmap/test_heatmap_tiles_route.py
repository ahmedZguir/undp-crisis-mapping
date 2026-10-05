"""Integration tests for `GET /crises/{id}/tiles/heat/{z}/{x}/{y}.pbf`.

Seeds via the writer's same upsert SQL (we drive `heat_cells` directly so
the test does not depend on the photo-upload happy path) and decodes the
MVT response with `mapbox_vector_tile.decode` to assert on shape.

Scoped cleanup only — `DELETE` confined to the test's `crisis_id`. No
TRUNCATE, no DROP.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any, cast

import mapbox_vector_tile as _mvt_typed  # type: ignore[reportMissingTypeStubs]
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from api.core.config import get_settings
from api.core.h3 import cell_for_location, cell_parent
from api.main import app

_mvt: Any = _mvt_typed


pytestmark = pytest.mark.integration


# Doha — a tile at z=15 that comfortably covers (25.30, 51.50).
_DOHA_TILE = (15, 21071, 14002)


async def _seed_crisis(
    engine_url: str,
    *,
    status: str = "active",
    k: int = 1,
) -> uuid.UUID:
    crisis_id = uuid.uuid4()
    engine = create_async_engine(engine_url)
    try:
        async with engine.begin() as conn:
            await conn.execute(
                text(
                    "insert into public.crises "
                    "  (id, name, status, created_at, heatmap_k_anonymity) "
                    "values (:id, :n, :s, :t, :k)"
                ),
                {
                    "id": str(crisis_id),
                    "n": f"Heatmap tile test {uuid.uuid4().hex[:8]}",
                    "s": status,
                    "t": datetime.now(UTC) - timedelta(minutes=1),
                    "k": k,
                },
            )
    finally:
        await engine.dispose()
    return crisis_id


async def _seed_cell(
    engine_url: str,
    *,
    crisis_id: uuid.UUID,
    cell: int,
    minimal: int = 0,
    partial: int = 0,
    complete: int = 0,
    latest_at: datetime | None = None,
) -> None:
    engine = create_async_engine(engine_url)
    try:
        async with engine.begin() as conn:
            await conn.execute(
                text(
                    "insert into public.heat_cells "
                    "  (crisis_id, h3_cell, report_count, "
                    "   minimal_count, partial_count, complete_count, latest_at) "
                    "values (:cid, :cell, :rc, :mn, :pa, :co, :ts)"
                ),
                {
                    "cid": str(crisis_id),
                    "cell": cell,
                    "rc": minimal + partial + complete,
                    "mn": minimal,
                    "pa": partial,
                    "co": complete,
                    "ts": latest_at or datetime.now(UTC),
                },
            )
    finally:
        await engine.dispose()


async def _cleanup(engine_url: str, *, crisis_ids: list[uuid.UUID]) -> None:
    engine = create_async_engine(engine_url)
    try:
        async with engine.begin() as conn:
            await conn.execute(
                text("delete from public.heat_cells where crisis_id = any(:ids)"),
                {"ids": [str(i) for i in crisis_ids]},
            )
            await conn.execute(
                text("delete from public.reports where crisis_id = any(:ids)"),
                {"ids": [str(i) for i in crisis_ids]},
            )
            await conn.execute(
                text("delete from public.crises where id = any(:ids)"),
                {"ids": [str(i) for i in crisis_ids]},
            )
    finally:
        await engine.dispose()


def _decode_heat_layer(body: bytes) -> dict[str, Any]:
    decoded = cast(dict[str, Any], _mvt.decode(body))
    return cast(dict[str, Any], decoded.get("heat", {"features": []}))


# --- Tests ---------------------------------------------------------------


def test_returns_valid_mvt_with_features() -> None:
    settings = get_settings()
    crisis_id = asyncio.run(_seed_crisis(settings.database_url))
    cell = cell_for_location(25.30, 51.50)
    asyncio.run(
        _seed_cell(settings.database_url, crisis_id=crisis_id, cell=cell, partial=2, complete=1)
    )

    try:
        with TestClient(app) as client:
            z, x, y = _DOHA_TILE
            response = client.get(f"/crises/{crisis_id}/tiles/heat/{z}/{x}/{y}.pbf")
        assert response.status_code == 200, response.text
        assert response.headers["content-type"] == "application/x-protobuf"
        layer = _decode_heat_layer(response.content)
        features = layer["features"]
        assert len(features) == 1
        props = features[0]["properties"]
        assert props["report_count"] == 3
        assert props["partial_count"] == 2
        assert props["complete_count"] == 1
    finally:
        asyncio.run(_cleanup(settings.database_url, crisis_ids=[crisis_id]))


def test_inactive_crisis_returns_404() -> None:
    settings = get_settings()
    crisis_id = asyncio.run(_seed_crisis(settings.database_url, status="inactive"))
    try:
        with TestClient(app) as client:
            z, x, y = _DOHA_TILE
            response = client.get(f"/crises/{crisis_id}/tiles/heat/{z}/{x}/{y}.pbf")
        assert response.status_code == 404
    finally:
        asyncio.run(_cleanup(settings.database_url, crisis_ids=[crisis_id]))


def test_archived_crisis_returns_404() -> None:
    settings = get_settings()
    crisis_id = asyncio.run(_seed_crisis(settings.database_url, status="archived"))
    try:
        with TestClient(app) as client:
            z, x, y = _DOHA_TILE
            response = client.get(f"/crises/{crisis_id}/tiles/heat/{z}/{x}/{y}.pbf")
        assert response.status_code == 404
    finally:
        asyncio.run(_cleanup(settings.database_url, crisis_ids=[crisis_id]))


def test_k_filter_drops_low_count_cells() -> None:
    settings = get_settings()
    crisis_id = asyncio.run(_seed_crisis(settings.database_url, k=3))
    # Two distinct res-9 cells both inside the z=15 Doha tile bbox.
    cell_a = cell_for_location(25.30, 51.500)
    cell_b = cell_for_location(25.30, 51.501)
    assert cell_a != cell_b
    asyncio.run(_seed_cell(settings.database_url, crisis_id=crisis_id, cell=cell_a, minimal=2))
    asyncio.run(_seed_cell(settings.database_url, crisis_id=crisis_id, cell=cell_b, complete=4))

    try:
        with TestClient(app) as client:
            z, x, y = _DOHA_TILE
            response = client.get(f"/crises/{crisis_id}/tiles/heat/{z}/{x}/{y}.pbf")
        assert response.status_code == 200
        layer = _decode_heat_layer(response.content)
        # Only cell_b clears k=3.
        assert len(layer["features"]) == 1
        assert layer["features"][0]["properties"]["report_count"] == 4
    finally:
        asyncio.run(_cleanup(settings.database_url, crisis_ids=[crisis_id]))


def test_zoom_rollup_combines_sibling_cells() -> None:
    settings = get_settings()
    crisis_id = asyncio.run(_seed_crisis(settings.database_url))
    cell_a = cell_for_location(25.300, 51.500)
    cell_b = cell_for_location(25.300, 51.501)
    assert cell_a != cell_b
    # Both fall under the same res-6 parent (verified by the unit test).
    parent_6 = cell_parent(cell_a, 6)
    assert parent_6 == cell_parent(cell_b, 6)

    asyncio.run(_seed_cell(settings.database_url, crisis_id=crisis_id, cell=cell_a, minimal=1))
    asyncio.run(_seed_cell(settings.database_url, crisis_id=crisis_id, cell=cell_b, complete=2))

    try:
        with TestClient(app) as client:
            # z=5 maps to res 6. Tile (5, 20, 13) covers Doha.
            response = client.get(f"/crises/{crisis_id}/tiles/heat/5/20/13.pbf")
        assert response.status_code == 200
        layer = _decode_heat_layer(response.content)
        # Both stored cells fold into one parent feature.
        assert len(layer["features"]) == 1
        props = layer["features"][0]["properties"]
        assert props["report_count"] == 3
        assert props["minimal_count"] == 1
        assert props["complete_count"] == 2
    finally:
        asyncio.run(_cleanup(settings.database_url, crisis_ids=[crisis_id]))


def test_etag_round_trip_returns_304() -> None:
    """A second GET with the prior ETag in `If-None-Match` short-circuits to
    304 with no body."""
    settings = get_settings()
    crisis_id = asyncio.run(_seed_crisis(settings.database_url))
    cell = cell_for_location(25.30, 51.50)
    asyncio.run(_seed_cell(settings.database_url, crisis_id=crisis_id, cell=cell, partial=1))
    try:
        with TestClient(app) as client:
            z, x, y = _DOHA_TILE
            first = client.get(f"/crises/{crisis_id}/tiles/heat/{z}/{x}/{y}.pbf")
            assert first.status_code == 200
            etag = first.headers["etag"]
            assert etag.startswith('W/"')
            assert first.headers["cache-control"] == "public, max-age=15"

            second = client.get(
                f"/crises/{crisis_id}/tiles/heat/{z}/{x}/{y}.pbf",
                headers={"If-None-Match": etag},
            )
        assert second.status_code == 304
        assert second.content == b""
        assert second.headers["etag"] == etag
    finally:
        asyncio.run(_cleanup(settings.database_url, crisis_ids=[crisis_id]))


def test_etag_changes_after_new_report() -> None:
    """When `latest_at` advances, the ETag changes and the stale token no
    longer 304s."""
    settings = get_settings()
    crisis_id = asyncio.run(_seed_crisis(settings.database_url))
    cell = cell_for_location(25.30, 51.50)
    asyncio.run(
        _seed_cell(
            settings.database_url,
            crisis_id=crisis_id,
            cell=cell,
            partial=1,
            latest_at=datetime(2026, 5, 12, 10, 0, 0, tzinfo=UTC),
        )
    )
    try:
        with TestClient(app) as client:
            z, x, y = _DOHA_TILE
            first = client.get(f"/crises/{crisis_id}/tiles/heat/{z}/{x}/{y}.pbf")
            old_etag = first.headers["etag"]

        # Bump latest_at by upserting a newer timestamp into the same cell.
        async def _bump() -> None:
            engine = create_async_engine(settings.database_url)
            try:
                async with engine.begin() as conn:
                    await conn.execute(
                        text(
                            "update public.heat_cells set "
                            "  report_count = report_count + 1, "
                            "  complete_count = complete_count + 1, "
                            "  latest_at = :ts "
                            "where crisis_id = :cid and h3_cell = :cell"
                        ),
                        {
                            "ts": datetime(2026, 5, 12, 14, 0, 0, tzinfo=UTC),
                            "cid": str(crisis_id),
                            "cell": cell,
                        },
                    )
            finally:
                await engine.dispose()

        asyncio.run(_bump())

        with TestClient(app) as client:
            second = client.get(
                f"/crises/{crisis_id}/tiles/heat/{z}/{x}/{y}.pbf",
                headers={"If-None-Match": old_etag},
            )
        assert second.status_code == 200
        assert second.headers["etag"] != old_etag
    finally:
        asyncio.run(_cleanup(settings.database_url, crisis_ids=[crisis_id]))


def test_latest_at_rounded_to_hour() -> None:
    settings = get_settings()
    crisis_id = asyncio.run(_seed_crisis(settings.database_url))
    cell = cell_for_location(25.30, 51.50)
    asyncio.run(
        _seed_cell(
            settings.database_url,
            crisis_id=crisis_id,
            cell=cell,
            partial=1,
            latest_at=datetime(2026, 5, 12, 14, 37, 19, tzinfo=UTC),
        )
    )

    try:
        with TestClient(app) as client:
            z, x, y = _DOHA_TILE
            response = client.get(f"/crises/{crisis_id}/tiles/heat/{z}/{x}/{y}.pbf")
        layer = _decode_heat_layer(response.content)
        latest = layer["features"][0]["properties"]["latest_at"]
        assert latest.startswith("2026-05-12T14:00:00")
    finally:
        asyncio.run(_cleanup(settings.database_url, crisis_ids=[crisis_id]))
