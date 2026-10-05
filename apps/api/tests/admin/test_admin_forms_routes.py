"""Integration tests for `GET` and `POST /admin/crises/{id}/form`."""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from api.auth.dependency import require_coordinator
from api.auth.models import Coordinator
from api.core.config import get_settings
from api.crises.default_form import DEFAULT_FORM_SCHEMA, default_form_schema_copy
from api.main import app

pytestmark = pytest.mark.integration


def _delete_seed(ids: list[uuid.UUID]) -> None:
    settings = get_settings()

    async def _run() -> None:
        engine = create_async_engine(settings.database_url)
        try:
            async with engine.begin() as conn:
                await conn.execute(
                    text("delete from public.crises where id = any(:ids)"),
                    {"ids": [str(i) for i in ids]},
                )
        finally:
            await engine.dispose()

    asyncio.run(_run())


def _create_crisis(client: TestClient) -> uuid.UUID:
    resp = client.post(
        "/admin/crises", json={"name": f"Forms test {uuid.uuid4().hex[:8]}", "type": "flood"}
    )
    assert resp.status_code == 201, resp.text
    return uuid.UUID(resp.json()["id"])


@pytest.fixture
def coordinator_principal() -> Iterator[None]:
    """Override the bypass to install a non-admin coordinator. Used to prove
    a plain coordinator can publish a form (editing the form is operational
    crisis work, not admin-only account management)."""

    def _override() -> Coordinator:
        return Coordinator(
            id=uuid.UUID("00000000-0000-4000-8000-000000000099"),
            email="coord@example.com",
            role="coordinator",
        )

    prev = app.dependency_overrides.get(require_coordinator)
    app.dependency_overrides[require_coordinator] = _override
    try:
        yield
    finally:
        if prev is not None:
            app.dependency_overrides[require_coordinator] = prev
        else:
            app.dependency_overrides.pop(require_coordinator, None)


def test_get_form_returns_default_schema_at_version_one() -> None:
    with TestClient(app) as client:
        cid = _create_crisis(client)
        try:
            resp = client.get(f"/admin/crises/{cid}/form")
            assert resp.status_code == 200, resp.text
            body = resp.json()
            assert body["version"] == 1
            assert body["schema"] == DEFAULT_FORM_SCHEMA
        finally:
            _delete_seed([cid])


def test_post_form_bumps_version_and_writes_history_row() -> None:
    with TestClient(app) as client:
        cid = _create_crisis(client)
        try:
            new_schema = default_form_schema_copy()
            # Toggle a non-locked page; still a valid schema.
            for p in new_schema["pages"]:
                if p["kind"] == "debris":
                    p["enabled"] = False

            resp = client.post(
                f"/admin/crises/{cid}/form",
                json={"based_on_version": 1, "schema": new_schema},
            )
            assert resp.status_code == 200, resp.text
            body = resp.json()
            assert body["version"] == 2

            # GET reflects the new state.
            again = client.get(f"/admin/crises/{cid}/form").json()
            assert again["version"] == 2
            disabled = next(p for p in again["schema"]["pages"] if p["kind"] == "debris")
            assert disabled["enabled"] is False

            # History row exists for version 2.
            settings = get_settings()

            async def _count() -> int:
                engine = create_async_engine(settings.database_url)
                try:
                    async with engine.connect() as conn:
                        row = (
                            await conn.execute(
                                text(
                                    "select count(*) as n from public.crisis_form_versions "
                                    "where crisis_id = :id and version = 2"
                                ),
                                {"id": str(cid)},
                            )
                        ).first()
                finally:
                    await engine.dispose()
                assert row is not None
                return row.n

            assert asyncio.run(_count()) == 1
        finally:
            _delete_seed([cid])


def test_post_form_returns_409_when_based_on_version_is_stale() -> None:
    with TestClient(app) as client:
        cid = _create_crisis(client)
        try:
            # First publish bumps to 2.
            ok = client.post(
                f"/admin/crises/{cid}/form",
                json={"based_on_version": 1, "schema": default_form_schema_copy()},
            )
            assert ok.status_code == 200, ok.text

            # Second publish still claims based_on_version=1 -> 409.
            conflict = client.post(
                f"/admin/crises/{cid}/form",
                json={"based_on_version": 1, "schema": default_form_schema_copy()},
            )
            assert conflict.status_code == 409, conflict.text
            body = conflict.json()
            assert body["current_version"] == 2
            assert "schema" in body
        finally:
            _delete_seed([cid])


def test_post_form_rejects_invalid_schema_with_400() -> None:
    with TestClient(app) as client:
        cid = _create_crisis(client)
        try:
            bad = default_form_schema_copy()
            # Swap photo_and_damage with another kind to break the locked
            # invariant at position 0.
            bad["pages"][0] = {"kind": "description", "enabled": True, "locked": True}
            resp = client.post(
                f"/admin/crises/{cid}/form",
                json={"based_on_version": 1, "schema": bad},
            )
            assert resp.status_code == 400, resp.text
            errors = resp.json()["detail"]["errors"]
            paths = [e["path"] for e in errors]
            assert any(p.startswith("$.pages[0]") for p in paths)
        finally:
            _delete_seed([cid])


def test_post_form_allows_non_admin_coordinator(coordinator_principal: None) -> None:
    _ = coordinator_principal
    with TestClient(app) as client:
        cid = _create_crisis(client)
        try:
            resp = client.post(
                f"/admin/crises/{cid}/form",
                json={"based_on_version": 1, "schema": default_form_schema_copy()},
            )
            assert resp.status_code == 200, resp.text
            assert resp.json()["version"] == 2
        finally:
            _delete_seed([cid])


def test_post_form_evicts_public_crisis_detail_cache() -> None:
    """After a POST, `GET /crises/{id}` reflects the new schema immediately."""
    with TestClient(app) as client:
        cid = _create_crisis(client)
        try:
            # Activate so /crises/{id} returns 200 for the citizen path.
            settings = get_settings()

            async def _activate() -> None:
                engine = create_async_engine(settings.database_url)
                try:
                    async with engine.begin() as conn:
                        await conn.execute(
                            text("update public.crises set status='active' where id=:id"),
                            {"id": str(cid)},
                        )
                finally:
                    await engine.dispose()

            asyncio.run(_activate())

            # Warm the public-detail cache at version 1.
            first = client.get(f"/crises/{cid}").json()
            assert first["form_version"] == 1

            new_schema = default_form_schema_copy()
            for p in new_schema["pages"]:
                if p["kind"] == "debris":
                    p["enabled"] = False
            assert (
                client.post(
                    f"/admin/crises/{cid}/form",
                    json={"based_on_version": 1, "schema": new_schema},
                ).status_code
                == 200
            )

            second = client.get(f"/crises/{cid}").json()
            assert second["form_version"] == 2
            d = next(p for p in second["form_schema"]["pages"] if p["kind"] == "debris")
            assert d["enabled"] is False
        finally:
            _delete_seed([cid])
