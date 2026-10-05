"""Tests for the `reports_gps_inside_crisis_trg` Postgres trigger.

See migration `20260514140000_reports_gps_polygon_check.sql`.

The trigger fires BEFORE INSERT OR UPDATE OF location, crisis_id on
public.reports. It raises sqlstate 23514 (check_violation) when the
report's location is non-NULL and sits outside the crisis polygon.
NULL location or NULL crisis geometry skips the check.

These tests insert reports directly via SQL (bypassing the API) to
exercise the trigger in isolation. The route-level handling — mapping
the trigger's IntegrityError to a 400 — is integration-tested
separately in `test_reports_integration.py` (the existing happy-path
tests already submit GPS inside the seeded polygon).
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import create_async_engine

from api.core.config import get_settings

pytestmark = pytest.mark.integration


# A small polygon centred near (33.5, 36.3). Lat range [33.495, 33.505],
# Lng range [36.295, 36.305].
_CRISIS_POLYGON_WKT = (
    "SRID=4326;MULTIPOLYGON((("
    "36.295 33.495,"
    "36.305 33.495,"
    "36.305 33.505,"
    "36.295 33.505,"
    "36.295 33.495"
    ")))"
)


def _seed_crisis_with_polygon() -> uuid.UUID:
    settings = get_settings()
    crisis_id = uuid.uuid4()
    suffix = uuid.uuid4().hex[:8]

    async def _run() -> None:
        engine = create_async_engine(settings.database_url)
        try:
            async with engine.begin() as conn:
                await conn.execute(
                    text(
                        "insert into public.crises "
                        "(id, name, status, created_at, geometry) "
                        "values (:id, :n, 'active', :t, st_geogfromtext(:wkt))"
                    ),
                    {
                        "id": str(crisis_id),
                        "n": f"polygon test crisis {suffix}",
                        "t": datetime.now(UTC) - timedelta(minutes=1),
                        "wkt": _CRISIS_POLYGON_WKT,
                    },
                )
        finally:
            await engine.dispose()

    asyncio.run(_run())
    return crisis_id


def _seed_crisis_no_geometry() -> uuid.UUID:
    """Crisis with NULL geometry — mirrors the reserved 'Other / Unspecified'
    row. The trigger must pass-through any report bound to it."""
    settings = get_settings()
    crisis_id = uuid.uuid4()
    suffix = uuid.uuid4().hex[:8]

    async def _run() -> None:
        engine = create_async_engine(settings.database_url)
        try:
            async with engine.begin() as conn:
                await conn.execute(
                    text(
                        "insert into public.crises "
                        "(id, name, status, created_at) "
                        "values (:id, :n, 'active', :t)"
                    ),
                    {
                        "id": str(crisis_id),
                        "n": f"no-geom test crisis {suffix}",
                        "t": datetime.now(UTC) - timedelta(minutes=1),
                    },
                )
        finally:
            await engine.dispose()

    asyncio.run(_run())
    return crisis_id


def _cleanup_crisis(crisis_id: uuid.UUID) -> None:
    settings = get_settings()

    async def _run() -> None:
        engine = create_async_engine(settings.database_url)
        try:
            async with engine.begin() as conn:
                await conn.execute(
                    text("delete from public.reports where crisis_id = :id"),
                    {"id": str(crisis_id)},
                )
                await conn.execute(
                    text("delete from public.crises where id = :id"),
                    {"id": str(crisis_id)},
                )
        finally:
            await engine.dispose()

    asyncio.run(_run())


def _insert_report(
    crisis_id: uuid.UUID,
    *,
    location_wkt: str | None,
) -> uuid.UUID:
    """Direct SQL insert, no API. Returns the new report id."""
    settings = get_settings()
    report_id: uuid.UUID = uuid.uuid4()

    async def _run() -> None:
        engine = create_async_engine(settings.database_url)
        try:
            async with engine.begin() as conn:
                await conn.execute(
                    text(
                        "insert into public.reports "
                        "(id, crisis_id, damage_class, photo_path, location, "
                        " route_description) "
                        "values (:id, :cid, 'minimal', :p, "
                        "        cast(:loc as geography), :route)"
                    ),
                    {
                        "id": str(report_id),
                        "cid": str(crisis_id),
                        "p": f"test/{report_id.hex}.jpg",
                        "loc": location_wkt,
                        # Satisfies `reports_location_or_route` for the
                        # null-location case; the GPS trigger is unaffected.
                        "route": "test directions",
                    },
                )
        finally:
            await engine.dispose()

    asyncio.run(_run())
    return report_id


def test_insert_inside_polygon_succeeds() -> None:
    crisis_id = _seed_crisis_with_polygon()
    try:
        # Centre of the polygon.
        rid = _insert_report(crisis_id, location_wkt="SRID=4326;POINT(36.300 33.500)")
        assert rid is not None
    finally:
        _cleanup_crisis(crisis_id)


def test_insert_outside_polygon_raises_integrity_error() -> None:
    crisis_id = _seed_crisis_with_polygon()
    try:
        # Far outside the (33.495, 36.295)-(33.505, 36.305) box.
        with pytest.raises(IntegrityError) as exc_info:
            _insert_report(crisis_id, location_wkt="SRID=4326;POINT(0 0)")
        sqlstate = getattr(exc_info.value.orig, "sqlstate", None)
        assert sqlstate == "23514", f"expected check_violation 23514; got {sqlstate}"
        # Message must mention the polygon to distinguish from other 23514s.
        assert "outside the crisis area" in str(exc_info.value.orig).lower()
    finally:
        _cleanup_crisis(crisis_id)


def test_insert_with_null_location_passes() -> None:
    """Landmark/infra-only reports submit with location=NULL. The trigger
    must pass them through regardless of the crisis polygon."""
    crisis_id = _seed_crisis_with_polygon()
    try:
        rid = _insert_report(crisis_id, location_wkt=None)
        assert rid is not None
    finally:
        _cleanup_crisis(crisis_id)


def test_insert_against_crisis_with_null_geometry_passes() -> None:
    """The reserved 'Other / Unspecified' crisis has NULL geometry; the
    trigger must pass reports bound to it through, even with GPS that
    would otherwise be 'outside' some other polygon."""
    crisis_id = _seed_crisis_no_geometry()
    try:
        rid = _insert_report(crisis_id, location_wkt="SRID=4326;POINT(120.0 -45.0)")
        assert rid is not None
    finally:
        _cleanup_crisis(crisis_id)


def test_update_moving_location_outside_polygon_raises() -> None:
    """The trigger covers UPDATE OF location too — a row with a valid
    in-polygon location cannot be moved outside after the fact."""
    crisis_id = _seed_crisis_with_polygon()
    settings = get_settings()
    try:
        rid = _insert_report(crisis_id, location_wkt="SRID=4326;POINT(36.300 33.500)")

        async def _try_update() -> None:
            engine = create_async_engine(settings.database_url)
            try:
                async with engine.begin() as conn:
                    await conn.execute(
                        text(
                            "update public.reports "
                            "set location = cast(:loc as geography) "
                            "where id = :id"
                        ),
                        {
                            "id": str(rid),
                            "loc": "SRID=4326;POINT(0 0)",
                        },
                    )
            finally:
                await engine.dispose()

        with pytest.raises(IntegrityError) as exc_info:
            asyncio.run(_try_update())
        assert getattr(exc_info.value.orig, "sqlstate", None) == "23514"
    finally:
        _cleanup_crisis(crisis_id)
