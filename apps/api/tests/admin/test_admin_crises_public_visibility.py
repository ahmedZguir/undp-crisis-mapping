"""Integration tests for the `public_visibility` and `heatmap_k_anonymity`
fields on the admin crisis endpoints (`PATCH` + `GET /admin/crises`).

The API accepts all four `public_visibility` values unconditionally; the admin
UI gates the riskier ones behind warnings + a modal — that gate is
out of scope for these tests.
"""

from __future__ import annotations

import asyncio
import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from api.core.config import get_settings
from api.main import app

pytestmark = pytest.mark.integration


def _seed_crisis(name: str) -> uuid.UUID:
    settings = get_settings()
    crisis_id = uuid.uuid4()

    async def _run() -> None:
        engine = create_async_engine(settings.database_url)
        try:
            async with engine.begin() as conn:
                await conn.execute(
                    text(
                        "insert into public.crises (id, name, status) values (:id, :n, 'inactive')"
                    ),
                    {"id": str(crisis_id), "n": name},
                )
        finally:
            await engine.dispose()

    asyncio.run(_run())
    return crisis_id


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


def _read_visibility(crisis_id: uuid.UUID) -> tuple[str, int]:
    settings = get_settings()

    async def _run() -> tuple[str, int]:
        engine = create_async_engine(settings.database_url)
        try:
            async with engine.connect() as conn:
                row = (
                    await conn.execute(
                        text(
                            "select public_visibility, heatmap_k_anonymity "
                            "from public.crises where id = :id"
                        ),
                        {"id": str(crisis_id)},
                    )
                ).first()
        finally:
            await engine.dispose()
        assert row is not None
        return row.public_visibility, int(row.heatmap_k_anonymity)

    return asyncio.run(_run())


@pytest.mark.parametrize("value", ["none", "aggregate_view", "buildings", "full"])
def test_patch_accepts_every_public_visibility_value(value: str) -> None:
    """All four enum values round-trip through the API. The admin UI's
    modal-confirm flow for `full` is a frontend concern; the API accepts
    every value unconditionally."""
    crisis_id = _seed_crisis(f"Visibility {value} {uuid.uuid4().hex[:6]}")
    try:
        with TestClient(app) as client:
            response = client.patch(
                f"/admin/crises/{crisis_id}",
                json={"public_visibility": value},
            )
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["public_visibility"] == value
        db_value, _ = _read_visibility(crisis_id)
        assert db_value == value
    finally:
        _delete_seed([crisis_id])


def test_patch_rejects_unknown_public_visibility_value() -> None:
    """An unknown enum value fails Pydantic validation with 422 before the
    DB CHECK ever sees it."""
    crisis_id = _seed_crisis(f"Visibility bogus {uuid.uuid4().hex[:6]}")
    try:
        with TestClient(app) as client:
            response = client.patch(
                f"/admin/crises/{crisis_id}",
                json={"public_visibility": "everyone_sees_everything"},
            )
        assert response.status_code == 422, response.text
    finally:
        _delete_seed([crisis_id])


def test_patch_heatmap_k_anonymity_round_trips() -> None:
    """The k floor is editable via the same PATCH; the admin UI surfaces it
    as 'K-anonymity floor' but the API field stays `heatmap_k_anonymity`."""
    crisis_id = _seed_crisis(f"k floor {uuid.uuid4().hex[:6]}")
    try:
        with TestClient(app) as client:
            response = client.patch(
                f"/admin/crises/{crisis_id}",
                json={"heatmap_k_anonymity": 5},
            )
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["heatmap_k_anonymity"] == 5
        _, k = _read_visibility(crisis_id)
        assert k == 5
    finally:
        _delete_seed([crisis_id])


def test_admin_list_surfaces_public_visibility_field() -> None:
    """`GET /admin/crises` returns the new field for every row."""
    crisis_id = _seed_crisis(f"List visibility {uuid.uuid4().hex[:6]}")
    try:
        # Flip to a non-default so the assertion is meaningful.
        with TestClient(app) as client:
            patch = client.patch(
                f"/admin/crises/{crisis_id}",
                json={"public_visibility": "none"},
            )
            assert patch.status_code == 200, patch.text

            listing = client.get("/admin/crises")
            assert listing.status_code == 200, listing.text
            items = listing.json()
            row = next((r for r in items if r["id"] == str(crisis_id)), None)
            assert row is not None
            assert row["public_visibility"] == "none"
            # The default k value is surfaced unchanged.
            assert row["heatmap_k_anonymity"] == 1
    finally:
        _delete_seed([crisis_id])
