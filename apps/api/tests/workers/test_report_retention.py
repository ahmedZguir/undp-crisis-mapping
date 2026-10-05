"""Integration tests for the data-retention purge.

Covers the daily `tick_report_retention` cron and the shared
`delete_by_crisis` disposal primitive:

  * Only crises archived past the window are purged; not-due and active
    crises are untouched, and the crisis row itself survives (empty shell).
  * Disposal decrements/removes `heat_cells`, attempts photo cleanup, and
    writes one PII-free `data_disposal_log` row (`reason='retention'`).
  * The cron is idempotent (a second run purges zero).
  * The window is configurable.
  * The citizen-delete path writes a `reason='manual'` disposal row.

Gated on a reachable local Supabase
stack, same posture as the citizen-delete tests.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from api.core.config import get_settings
from api.core.db import make_engine, make_sessionmaker
from api.core.rate_limit import limiter
from api.main import app
from api.workers.crises import tick_report_retention

FIXTURE = Path(__file__).parent.parent / "fixtures" / "tiny.jpg"

# ctx keys the worker reads off Arq's ctx — mirror
# `api.workers.settings._CTX_SESSIONMAKER` / `_CTX_STORAGE`.
_CTX_SESSIONMAKER = "db_sessionmaker"
_CTX_STORAGE = "storage_client"


pytestmark = pytest.mark.integration


# --- DB helpers ----------------------------------------------------------


def _run_sql(sql: str, params: dict[str, Any]) -> None:
    settings = get_settings()

    async def _run() -> None:
        engine = create_async_engine(settings.database_url)
        try:
            async with engine.begin() as conn:
                await conn.execute(text(sql), params)
        finally:
            await engine.dispose()

    asyncio.run(_run())


def _scalar(sql: str, params: dict[str, Any]) -> int:
    settings = get_settings()

    async def _run() -> int:
        engine = create_async_engine(settings.database_url)
        try:
            async with engine.connect() as conn:
                value = (await conn.execute(text(sql), params)).scalar_one()
                return int(value) if value is not None else 0
        finally:
            await engine.dispose()

    return asyncio.run(_run())


def _seed_active_crisis() -> uuid.UUID:
    crisis_id = uuid.uuid4()
    suffix = uuid.uuid4().hex[:8]
    _run_sql(
        "insert into public.crises (id, name, status, created_at) "
        "values (:id, :n, 'active', now() - interval '1 minute')",
        {"id": str(crisis_id), "n": f"retention test {suffix}"},
    )
    return crisis_id


def _archive_crisis(crisis_id: uuid.UUID, *, days_ago: int) -> None:
    _run_sql(
        "update public.crises set status = 'archived', "
        "    archived_at = now() - make_interval(days => :d) "
        " where id = :id",
        {"d": days_ago, "id": str(crisis_id)},
    )


def _count_reports(crisis_id: uuid.UUID) -> int:
    return _scalar(
        "select count(*) from public.reports where crisis_id = :id",
        {"id": str(crisis_id)},
    )


def _count_heat_cells(crisis_id: uuid.UUID) -> int:
    return _scalar(
        "select count(*) from public.heat_cells where crisis_id = :id",
        {"id": str(crisis_id)},
    )


def _crisis_exists(crisis_id: uuid.UUID) -> bool:
    return (
        _scalar(
            "select count(*) from public.crises where id = :id",
            {"id": str(crisis_id)},
        )
        == 1
    )


def _disposal_rows(crisis_id: uuid.UUID) -> list[dict[str, Any]]:
    settings = get_settings()

    async def _run() -> list[dict[str, Any]]:
        engine = create_async_engine(settings.database_url)
        try:
            async with engine.connect() as conn:
                rows = (
                    await conn.execute(
                        text(
                            "select reason, reports_count, photos_count "
                            "from public.data_disposal_log where crisis_id = :id"
                        ),
                        {"id": str(crisis_id)},
                    )
                ).all()
                return [
                    {
                        "reason": r.reason,
                        "reports_count": int(r.reports_count),
                        "photos_count": int(r.photos_count),
                    }
                    for r in rows
                ]
        finally:
            await engine.dispose()

    return asyncio.run(_run())


def _cleanup(crisis_id: uuid.UUID) -> None:
    _run_sql("delete from public.reports where crisis_id = :id", {"id": str(crisis_id)})
    _run_sql("delete from public.heat_cells where crisis_id = :id", {"id": str(crisis_id)})
    _run_sql("delete from public.data_disposal_log where crisis_id = :id", {"id": str(crisis_id)})
    _run_sql("delete from public.crises where id = :id", {"id": str(crisis_id)})


def _submit_report(
    client: TestClient,
    crisis_id: uuid.UUID,
    client_id: uuid.UUID,
    *,
    lat: float,
    lng: float,
    damage_class: str,
) -> None:
    payload = {
        "crisis_id": str(crisis_id),
        "damage_class": damage_class,
        "location": {"lat": lat, "lng": lng},
        "client_id": str(client_id),
    }
    files = {
        "photo": ("tiny.jpg", FIXTURE.read_bytes(), "image/jpeg"),
        "data": (None, json.dumps(payload), "application/json"),
    }
    response = client.post("/reports", files=files)
    assert response.status_code == 200, response.text


class _StubDeleter:
    """Records best-effort photo-delete attempts instead of hitting Storage."""

    def __init__(self) -> None:
        self.deleted: list[str] = []

    async def delete_photo(self, photo_path: str) -> None:
        self.deleted.append(photo_path)


def _run_tick(deleter: _StubDeleter) -> int:
    async def _run() -> int:
        engine = make_engine(get_settings().database_url)
        try:
            ctx: dict[str, object] = {
                _CTX_SESSIONMAKER: make_sessionmaker(engine),
                _CTX_STORAGE: deleter,
            }
            return await tick_report_retention(ctx)
        finally:
            await engine.dispose()

    return asyncio.run(_run())


# --- Tests ---------------------------------------------------------------


def test_tick_purges_due_crisis_only() -> None:
    due = _seed_active_crisis()
    recent = _seed_active_crisis()
    active = _seed_active_crisis()
    client_id = uuid.uuid4()
    try:
        with TestClient(app) as client:
            # Two reports at the same cell under the due crisis (one heat_cell,
            # report_count 2), plus one each under the other two crises.
            _submit_report(client, due, client_id, lat=25.30, lng=51.50, damage_class="partial")
            _submit_report(client, due, client_id, lat=25.30, lng=51.50, damage_class="minimal")
            _submit_report(client, recent, client_id, lat=25.31, lng=51.51, damage_class="complete")
            _submit_report(client, active, client_id, lat=25.32, lng=51.52, damage_class="minimal")

        _archive_crisis(due, days_ago=200)  # past the 90-day default → due
        _archive_crisis(recent, days_ago=10)  # within the window → not due
        # `active` stays active → never a purge candidate.

        assert _count_reports(due) == 2
        assert _count_heat_cells(due) == 1

        deleter = _StubDeleter()
        purged = _run_tick(deleter)

        assert purged == 2
        # Due crisis fully purged; its heat_cells removed; photos attempted.
        assert _count_reports(due) == 0
        assert _count_heat_cells(due) == 0
        assert len(deleter.deleted) == 2
        # The crisis row itself survives as an empty shell.
        assert _crisis_exists(due)
        # Not-due and active crises are untouched.
        assert _count_reports(recent) == 1
        assert _count_reports(active) == 1
        # One PII-free disposal record for the due crisis.
        rows = _disposal_rows(due)
        assert rows == [{"reason": "retention", "reports_count": 2, "photos_count": 2}]
    finally:
        _cleanup(due)
        _cleanup(recent)
        _cleanup(active)


def test_tick_is_idempotent() -> None:
    due = _seed_active_crisis()
    client_id = uuid.uuid4()
    try:
        with TestClient(app) as client:
            _submit_report(client, due, client_id, lat=25.30, lng=51.50, damage_class="minimal")
        _archive_crisis(due, days_ago=200)

        assert _run_tick(_StubDeleter()) == 1
        # Reports already gone → second run is a no-op, writes no new log row.
        assert _run_tick(_StubDeleter()) == 0
        assert len(_disposal_rows(due)) == 1
    finally:
        _cleanup(due)


def test_tick_respects_configurable_window(monkeypatch: pytest.MonkeyPatch) -> None:
    due = _seed_active_crisis()
    client_id = uuid.uuid4()
    try:
        with TestClient(app) as client:
            _submit_report(client, due, client_id, lat=25.30, lng=51.50, damage_class="minimal")
        _archive_crisis(due, days_ago=7)  # NOT due under the 90-day default

        assert _run_tick(_StubDeleter()) == 0
        assert _count_reports(due) == 1

        # Shrink the window so the 7-days-archived crisis becomes due.
        monkeypatch.setattr(get_settings(), "report_retention_days", 5)
        assert _run_tick(_StubDeleter()) == 1
        assert _count_reports(due) == 0
    finally:
        _cleanup(due)


def test_citizen_delete_writes_manual_disposal_row() -> None:
    crisis_id = _seed_active_crisis()
    client_id = uuid.uuid4()
    try:
        with TestClient(app) as client:
            _submit_report(
                client, crisis_id, client_id, lat=25.30, lng=51.50, damage_class="minimal"
            )
            limiter.reset()
            response = client.post("/reports/delete", json={"client_id": str(client_id)})
            assert response.status_code == 200, response.text

        rows = _disposal_rows(crisis_id)
        assert rows == [{"reason": "manual", "reports_count": 1, "photos_count": 1}]
    finally:
        _cleanup(crisis_id)
