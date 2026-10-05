"""Smoke test: insert a row into `public.buildings` and read it back.

Pins that the `centroid` column is populated by the database (generated
stored column) even though the insert never sets it.
"""

from __future__ import annotations

import asyncio
import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from api.core.config import get_settings

pytestmark = pytest.mark.integration


def test_building_row_roundtrip_populates_centroid() -> None:
    """Insert a single MultiPolygon footprint into `public.buildings` and read
    it back, asserting the generated `centroid` column is auto-populated.
    """
    settings = get_settings()
    source_id = f"test-{uuid.uuid4().hex}"

    # A 0.001-degree square near (10,10).
    wkt = "MULTIPOLYGON(((10.0 10.0,10.001 10.0,10.001 10.001,10.0 10.001,10.0 10.0)))"

    async def _run() -> tuple[str, float, float, str]:
        engine = create_async_engine(settings.database_url)
        try:
            async with engine.begin() as conn:
                await conn.execute(
                    text(
                        "insert into public.buildings "
                        "(source, source_id, footprint, properties) "
                        "values (:source, :source_id, "
                        "  st_geogfromtext(:wkt), "
                        "  cast(:props as jsonb))"
                    ),
                    {
                        "source": "test",
                        "source_id": source_id,
                        "wkt": f"SRID=4326;{wkt}",
                        "props": '{"k": "v"}',
                    },
                )
                row = (
                    await conn.execute(
                        text(
                            "select source_id, "
                            "st_x(centroid::geometry) as cx, "
                            "st_y(centroid::geometry) as cy, "
                            "properties::text as props "
                            "from public.buildings "
                            "where source = :s and source_id = :sid"
                        ),
                        {"s": "test", "sid": source_id},
                    )
                ).first()
                await conn.execute(
                    text("delete from public.buildings where source = :s and source_id = :sid"),
                    {"s": "test", "sid": source_id},
                )
        finally:
            await engine.dispose()
        assert row is not None
        return str(row.source_id), float(row.cx), float(row.cy), str(row.props)

    sid, cx, cy, props = asyncio.run(_run())
    assert sid == source_id
    # Centroid of the unit square is (10.0005, 10.0005).
    assert abs(cx - 10.0005) < 1e-6
    assert abs(cy - 10.0005) < 1e-6
    assert props == '{"k": "v"}'
