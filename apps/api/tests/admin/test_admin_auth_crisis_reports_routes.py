"""Integration + permission tests for the crisis-analysis-report admin routes.

  * `POST /admin/crises/{id}/analysis_reports`
  * `GET  /admin/crises/{id}/analysis_reports`
  * `GET  /admin/crises/{id}/analysis_reports/{report_id}`

Filename prefix `test_admin_auth_` opts out of the autouse `_bypass_admin_auth`
fixture, so the real router-level `require_coordinator` gate runs. We stub
the JWT verifier (via `get_app_state` override) to forge coordinator/admin
tokens, a fake enqueuer so no Redis is needed, and a fake PDF store so the
detail endpoint can mint a signed URL without a live Supabase.

The DB writes (the pre-created 'running' row, the history list) hit local
Supabase, so the module skips when the stack is unreachable.
"""

from __future__ import annotations

import asyncio
import dataclasses
import uuid
from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from api.admin.report_generation_routes import (
    get_report_generation_enqueuer,
    get_report_pdf_store,
)
from api.core.app_state import AppState, get_app_state
from api.core.config import get_settings
from api.main import app

pytestmark = [pytest.mark.integration, pytest.mark.real_auth]


# --- stubs -----------------------------------------------------------------


class _StubVerifier:
    def __init__(self, claims: dict[str, Any] | None) -> None:
        self._claims = claims

    def verify(self, token: str) -> dict[str, Any]:
        if self._claims is None:
            import jwt as _jwt

            raise _jwt.InvalidSignatureError("stub: invalid")
        return self._claims


def _claims_for(role: str) -> dict[str, Any]:
    return {
        "sub": "11111111-1111-4111-8111-111111111111",
        "aud": "authenticated",
        "iss": "test-issuer",
        "exp": 9_999_999_999,
        "iat": 1,
        "email": "admin@example.com",
        "app_metadata": {"role": role},
    }


class _FakeEnqueuer:
    def __init__(self) -> None:
        self.calls: list[uuid.UUID] = []

    async def enqueue(self, report_id: uuid.UUID) -> str:
        self.calls.append(report_id)
        return "job-report-123"


class _FakeStore:
    async def upload_report_pdf(self, content: bytes, key: str) -> str:  # pragma: no cover
        return key

    async def sign_report_url(self, key: str, ttl_seconds: int) -> str:
        return f"https://signed.example/{key}?ttl={ttl_seconds}"


# --- db helpers ------------------------------------------------------------


def _seed_crisis() -> uuid.UUID:
    settings = get_settings()
    crisis_id = uuid.uuid4()

    async def _run() -> None:
        engine = create_async_engine(settings.database_url)
        try:
            async with engine.begin() as conn:
                await conn.execute(
                    text(
                        "insert into public.crises (id, name, status, type) "
                        "values (:id, :n, 'inactive', 'flood')"
                    ),
                    {"id": str(crisis_id), "n": f"Report test {crisis_id}"},
                )
        finally:
            await engine.dispose()

    asyncio.run(_run())
    return crisis_id


def _delete_crisis(crisis_id: uuid.UUID) -> None:
    settings = get_settings()

    async def _run() -> None:
        engine = create_async_engine(settings.database_url)
        try:
            async with engine.begin() as conn:
                # crisis_reports cascade-deletes with the crisis.
                await conn.execute(
                    text("delete from public.crises where id = :id"),
                    {"id": str(crisis_id)},
                )
        finally:
            await engine.dispose()

    asyncio.run(_run())


def _mark_report_succeeded(report_id: uuid.UUID, storage_key: str) -> None:
    settings = get_settings()

    async def _run() -> None:
        engine = create_async_engine(settings.database_url)
        try:
            async with engine.begin() as conn:
                await conn.execute(
                    text(
                        "update public.crisis_reports set status = 'succeeded', "
                        "storage_key = :k, report_count = 7, device_count = 3, "
                        "building_count = 100, coverage_pct = 25 where id = :id"
                    ),
                    {"id": str(report_id), "k": storage_key},
                )
        finally:
            await engine.dispose()

    asyncio.run(_run())


def _read_report_status(report_id: uuid.UUID) -> str | None:
    settings = get_settings()

    async def _run() -> str | None:
        engine = create_async_engine(settings.database_url)
        try:
            async with engine.connect() as conn:
                row = (
                    await conn.execute(
                        text("select status from public.crisis_reports where id = :id"),
                        {"id": str(report_id)},
                    )
                ).first()
        finally:
            await engine.dispose()
        return row.status if row is not None else None

    return asyncio.run(_run())


# --- fixtures --------------------------------------------------------------


@pytest.fixture
def client_with_role() -> Iterator[tuple[TestClient, dict[str, Any], _FakeEnqueuer]]:
    """Yields (client, mutable verifier_state, fake_enqueuer). Set
    `verifier_state["claims"]` to control the forged role per test."""
    verifier_state: dict[str, Any] = {"claims": None}
    fake_enqueuer = _FakeEnqueuer()

    with TestClient(app) as client:
        real_state = app.state.container

        def _stub_get_state() -> AppState:
            return dataclasses.replace(
                real_state, jwt_verifier=_StubVerifier(verifier_state["claims"])
            )

        app.dependency_overrides[get_app_state] = _stub_get_state
        app.dependency_overrides[get_report_generation_enqueuer] = lambda: fake_enqueuer
        app.dependency_overrides[get_report_pdf_store] = _FakeStore
        try:
            yield client, verifier_state, fake_enqueuer
        finally:
            app.dependency_overrides.pop(get_app_state, None)
            app.dependency_overrides.pop(get_report_generation_enqueuer, None)
            app.dependency_overrides.pop(get_report_pdf_store, None)


# --- permission tests ------------------------------------------------------


def test_post_report_without_bearer_returns_401(
    client_with_role: tuple[TestClient, dict[str, Any], _FakeEnqueuer],
) -> None:
    client, _, _ = client_with_role
    res = client.post(f"/admin/crises/{uuid.uuid4()}/analysis_reports")
    assert res.status_code == 401, res.text


def test_post_report_as_coordinator_creates_running_row(
    client_with_role: tuple[TestClient, dict[str, Any], _FakeEnqueuer],
) -> None:
    # Generating a report is operational crisis work, open to any coordinator —
    # only staff account management stays admin-only.
    client, state, enqueuer = client_with_role
    state["claims"] = _claims_for("coordinator")
    crisis_id = _seed_crisis()
    try:
        res = client.post(
            f"/admin/crises/{crisis_id}/analysis_reports",
            headers={"Authorization": "Bearer good"},
        )
        assert res.status_code == 201, res.text
        body = res.json()
        assert body["status"] == "running"
        assert enqueuer.calls == [uuid.UUID(body["id"])]
    finally:
        _delete_crisis(crisis_id)


def test_list_reports_as_coordinator_is_allowed(
    client_with_role: tuple[TestClient, dict[str, Any], _FakeEnqueuer],
) -> None:
    client, state, _ = client_with_role
    state["claims"] = _claims_for("coordinator")
    res = client.get(
        f"/admin/crises/{uuid.uuid4()}/analysis_reports",
        headers={"Authorization": "Bearer good"},
    )
    assert res.status_code == 200, res.text
    assert res.json() == []


# --- functional tests (admin) ----------------------------------------------


def test_post_report_as_admin_creates_running_row_and_enqueues(
    client_with_role: tuple[TestClient, dict[str, Any], _FakeEnqueuer],
) -> None:
    client, state, enqueuer = client_with_role
    state["claims"] = _claims_for("admin")
    crisis_id = _seed_crisis()
    try:
        res = client.post(
            f"/admin/crises/{crisis_id}/analysis_reports",
            headers={"Authorization": "Bearer good"},
        )
        assert res.status_code == 201, res.text
        body = res.json()
        assert body["status"] == "running"
        report_id = uuid.UUID(body["id"])

        assert enqueuer.calls == [report_id]
        assert _read_report_status(report_id) == "running"

        # The generator's email is snapshotted onto the row at creation time
        # (from the admin principal's claims) and surfaced in the history list.
        listing = client.get(
            f"/admin/crises/{crisis_id}/analysis_reports",
            headers={"Authorization": "Bearer good"},
        ).json()
        assert listing[0]["created_by_email"] == "admin@example.com"
    finally:
        _delete_crisis(crisis_id)


def test_post_report_unknown_crisis_returns_404(
    client_with_role: tuple[TestClient, dict[str, Any], _FakeEnqueuer],
) -> None:
    client, state, enqueuer = client_with_role
    state["claims"] = _claims_for("admin")
    res = client.post(
        f"/admin/crises/{uuid.uuid4()}/analysis_reports",
        headers={"Authorization": "Bearer good"},
    )
    assert res.status_code == 404, res.text
    assert enqueuer.calls == []  # no enqueue when the crisis is absent


def test_get_report_item_signs_url_only_when_succeeded(
    client_with_role: tuple[TestClient, dict[str, Any], _FakeEnqueuer],
) -> None:
    client, state, _ = client_with_role
    state["claims"] = _claims_for("admin")
    crisis_id = _seed_crisis()
    try:
        created = client.post(
            f"/admin/crises/{crisis_id}/analysis_reports",
            headers={"Authorization": "Bearer good"},
        ).json()
        report_id = uuid.UUID(created["id"])

        # Still running → no download URL, and the list shows download_ready=false.
        detail = client.get(
            f"/admin/crises/{crisis_id}/analysis_reports/{report_id}",
            headers={"Authorization": "Bearer good"},
        ).json()
        assert detail["status"] == "running"
        assert detail["download_url"] is None
        assert detail["created_by_email"] == "admin@example.com"

        listing = client.get(
            f"/admin/crises/{crisis_id}/analysis_reports",
            headers={"Authorization": "Bearer good"},
        ).json()
        assert len(listing) == 1
        assert listing[0]["download_ready"] is False

        # Flip to succeeded (simulating the worker) → URL is minted.
        key = f"{crisis_id}/{report_id}.pdf"
        _mark_report_succeeded(report_id, key)

        detail2 = client.get(
            f"/admin/crises/{crisis_id}/analysis_reports/{report_id}",
            headers={"Authorization": "Bearer good"},
        ).json()
        assert detail2["status"] == "succeeded"
        assert detail2["download_url"] == f"https://signed.example/{key}?ttl=900"
        assert detail2["report_count"] == 7
        assert detail2["coverage_pct"] == 25.0

        listing2 = client.get(
            f"/admin/crises/{crisis_id}/analysis_reports",
            headers={"Authorization": "Bearer good"},
        ).json()
        assert listing2[0]["download_ready"] is True
    finally:
        _delete_crisis(crisis_id)
