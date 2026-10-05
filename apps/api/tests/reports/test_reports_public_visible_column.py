"""`public.reports.public_visible` column smoke tests.

Public visibility mode-switching scaffold; migration
`20260517130000_reports_public_visible.sql`. Covers:

- Default `true` for inserts that omit the column.
- An update to `false` is accepted.
- The column is queryable via `SELECT`.

No admin endpoint ships for this column yet. A
coordinator with a v1 emergency flips a row via SQL — exactly what this
test exercises.
"""

from __future__ import annotations

import asyncio
import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from api.core.config import get_settings

pytestmark = pytest.mark.integration


async def _seed_crisis(engine_url: str) -> uuid.UUID:
    crisis_id = uuid.uuid4()
    engine = create_async_engine(engine_url)
    try:
        async with engine.begin() as conn:
            await conn.execute(
                text(
                    "insert into public.crises "
                    "  (id, name, status, public_visibility, heatmap_k_anonymity) "
                    "values (:id, :n, 'active', 'aggregate_view', 1)"
                ),
                {
                    "id": str(crisis_id),
                    "n": f"public_visible col {uuid.uuid4().hex[:6]}",
                },
            )
    finally:
        await engine.dispose()
    return crisis_id


async def _insert_minimal_report(engine_url: str, *, crisis_id: uuid.UUID) -> uuid.UUID:
    """Insert a report row WITHOUT touching `public_visible`. We bypass the
    HTTP layer here because the route schema doesn't accept the column (the
    admin mutation surface is deferred) — but the
    point of this test is the DB-level default + column shape, not the
    submit flow.
    """
    report_id = uuid.uuid4()
    engine = create_async_engine(engine_url)
    try:
        async with engine.begin() as conn:
            await conn.execute(
                text(
                    "insert into public.reports "
                    "  (id, crisis_id, damage_class, photo_path, route_description) "
                    "values (:id, :cid, 'minimal', :path, 'test directions')"
                ),
                {
                    "id": str(report_id),
                    "cid": str(crisis_id),
                    "path": f"reports/{report_id}.jpg",
                },
            )
    finally:
        await engine.dispose()
    return report_id


async def _read_public_visible(engine_url: str, *, report_id: uuid.UUID) -> bool:
    engine = create_async_engine(engine_url)
    try:
        async with engine.connect() as conn:
            row = (
                await conn.execute(
                    text("select public_visible from public.reports where id = :id"),
                    {"id": str(report_id)},
                )
            ).first()
    finally:
        await engine.dispose()
    assert row is not None
    return bool(row.public_visible)


async def _update_public_visible(engine_url: str, *, report_id: uuid.UUID, value: bool) -> None:
    engine = create_async_engine(engine_url)
    try:
        async with engine.begin() as conn:
            await conn.execute(
                text("update public.reports set public_visible = :v where id = :id"),
                {"v": value, "id": str(report_id)},
            )
    finally:
        await engine.dispose()


async def _cleanup(engine_url: str, *, crisis_id: uuid.UUID) -> None:
    engine = create_async_engine(engine_url)
    try:
        async with engine.begin() as conn:
            await conn.execute(
                text("delete from public.reports where crisis_id = :cid"),
                {"cid": str(crisis_id)},
            )
            await conn.execute(
                text("delete from public.crises where id = :id"),
                {"id": str(crisis_id)},
            )
    finally:
        await engine.dispose()


def test_insert_without_column_defaults_to_true() -> None:
    """An INSERT that omits `public_visible` lands `true` via the column
    default — every existing row stays publicly visible after the migration."""
    settings = get_settings()
    crisis_id = asyncio.run(_seed_crisis(settings.database_url))
    try:
        report_id = asyncio.run(_insert_minimal_report(settings.database_url, crisis_id=crisis_id))
        assert asyncio.run(_read_public_visible(settings.database_url, report_id=report_id)) is True
    finally:
        asyncio.run(_cleanup(settings.database_url, crisis_id=crisis_id))


def test_update_to_false_is_accepted_and_queryable() -> None:
    """A coordinator-emergency `UPDATE ... set public_visible = false`
    round-trips. This is the v1 escape hatch until the admin mutation
    surface ships."""
    settings = get_settings()
    crisis_id = asyncio.run(_seed_crisis(settings.database_url))
    try:
        report_id = asyncio.run(_insert_minimal_report(settings.database_url, crisis_id=crisis_id))
        asyncio.run(_update_public_visible(settings.database_url, report_id=report_id, value=False))
        assert (
            asyncio.run(_read_public_visible(settings.database_url, report_id=report_id)) is False
        )
        # Flip back to sanity-check the round-trip both directions.
        asyncio.run(_update_public_visible(settings.database_url, report_id=report_id, value=True))
        assert asyncio.run(_read_public_visible(settings.database_url, report_id=report_id)) is True
    finally:
        asyncio.run(_cleanup(settings.database_url, crisis_id=crisis_id))
