"""Live stack: back-to-back bot submissions all land, with no ``POST /reports`` rate limit."""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from api.channels.sessions import Session
from api.channels.submitter import ServiceReportSubmitter
from api.core.app_state import AppState
from api.core.config import get_settings
from api.main import app

pytestmark = pytest.mark.integration


def _exec(sql: str, params: dict[str, object]) -> list[Any]:
    async def _run() -> list[Any]:
        engine = create_async_engine(get_settings().database_url)
        try:
            async with engine.begin() as conn:
                result = await conn.execute(text(sql), params)
                return list(result.all()) if result.returns_rows else []
        finally:
            await engine.dispose()

    return asyncio.run(_run())


def _session(crisis_id: uuid.UUID, n: int) -> Session:
    s = Session(phone_e164=f"+9744000000{n}")
    s.crisis_id = crisis_id
    s.damage_class = "partial"
    s.infra_description = f"wall cracked #{n}"
    s.route_description = "behind the school"
    return s


def test_channel_reports_are_written_in_process_without_a_shared_rate_limit() -> None:
    crisis_id = uuid.uuid4()
    _exec(
        "insert into public.crises (id, name, status, created_at) values (:id, :n, 'active', :t)",
        {
            "id": str(crisis_id),
            "n": f"Bot submit crisis {crisis_id.hex[:8]}",
            "t": datetime.now(UTC) - timedelta(minutes=1),
        },
    )
    try:
        with TestClient(app) as client:
            state = app.state.container
            assert isinstance(state, AppState)
            submitter = ServiceReportSubmitter(
                state.report_service,
                twilio_account_sid="",
                twilio_auth_token="",
                always_send_description=True,
            )
            ids = [
                client.portal.call(submitter.submit, _session(crisis_id, n))  # pyright: ignore[reportOptionalMemberAccess]
                for n in range(7)
            ]
        rows = _exec(
            "select id, description from public.reports where crisis_id = :id",
            {"id": str(crisis_id)},
        )
        assert {r.id for r in rows} == set(ids)
        assert len(ids) == 7
    finally:
        _exec("delete from public.reports where crisis_id = :id", {"id": str(crisis_id)})
        _exec("delete from public.crises where id = :id", {"id": str(crisis_id)})
