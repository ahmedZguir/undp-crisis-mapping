"""Integration tests for the per-crisis form schema wiring on
`POST /admin/crises`.

A new crisis must persist the canonical default schema explicitly — not
just rely on the column default — and write a matching row to
`crisis_form_versions` so the version history starts at v1 from day one.
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


def _read_form(crisis_id: uuid.UUID) -> tuple[int, dict[str, object]]:
    settings = get_settings()

    async def _run() -> tuple[int, dict[str, object]]:
        engine = create_async_engine(settings.database_url)
        try:
            async with engine.connect() as conn:
                row = (
                    await conn.execute(
                        text("select form_version, form_schema from public.crises where id = :id"),
                        {"id": str(crisis_id)},
                    )
                ).first()
        finally:
            await engine.dispose()
        assert row is not None
        return row.form_version, row.form_schema

    return asyncio.run(_run())


def _count_history(crisis_id: uuid.UUID, version: int) -> int:
    settings = get_settings()

    async def _run() -> int:
        engine = create_async_engine(settings.database_url)
        try:
            async with engine.connect() as conn:
                row = (
                    await conn.execute(
                        text(
                            "select count(*) as n from public.crisis_form_versions "
                            "where crisis_id = :id and version = :v"
                        ),
                        {"id": str(crisis_id), "v": version},
                    )
                ).first()
        finally:
            await engine.dispose()
        assert row is not None
        return row.n

    return asyncio.run(_run())


def test_post_crisis_persists_default_form_schema_at_version_one() -> None:
    from api.crises.default_form import DEFAULT_FORM_SCHEMA, DEFAULT_FORM_VERSION

    name = f"Form schema seed {uuid.uuid4().hex[:8]}"
    with TestClient(app) as client:
        response = client.post("/admin/crises", json={"name": name, "type": "flood"})

    assert response.status_code == 201, response.text
    crisis_id = uuid.UUID(response.json()["id"])

    try:
        version, schema = _read_form(crisis_id)
        assert version == DEFAULT_FORM_VERSION
        assert schema == DEFAULT_FORM_SCHEMA
    finally:
        _delete_seed([crisis_id])


def test_post_crisis_writes_matching_crisis_form_versions_row() -> None:
    name = f"Form schema history {uuid.uuid4().hex[:8]}"
    with TestClient(app) as client:
        response = client.post("/admin/crises", json={"name": name, "type": "flood"})

    assert response.status_code == 201, response.text
    crisis_id = uuid.UUID(response.json()["id"])

    try:
        assert _count_history(crisis_id, version=1) == 1
    finally:
        _delete_seed([crisis_id])
