"""Integration tests for crisis-name uniqueness on the admin endpoints.

The `crises.name` column carries a unique index (`crises_name_uniq`,
seeded in the spine migration). The admin routes translate a collision
against it into a clean `409` rather than letting the raw
`IntegrityError` bubble up as a 500. Uniqueness is exact-match and
case-sensitive — see the `crisis-name-unique` work.
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


def _delete_by_name(names: list[str]) -> None:
    settings = get_settings()

    async def _run() -> None:
        engine = create_async_engine(settings.database_url)
        try:
            async with engine.begin() as conn:
                await conn.execute(
                    text("delete from public.crises where name = any(:names)"),
                    {"names": names},
                )
        finally:
            await engine.dispose()

    asyncio.run(_run())


def test_create_duplicate_name_returns_409() -> None:
    """A second create with an already-used name fails cleanly with 409,
    not a 500 from the raw unique-violation."""
    name = f"Dup create {uuid.uuid4().hex[:8]}"
    try:
        with TestClient(app) as client:
            first = client.post(
                "/admin/crises", json={"name": name, "status": "inactive", "type": "flood"}
            )
            assert first.status_code == 201, first.text

            second = client.post(
                "/admin/crises", json={"name": name, "status": "inactive", "type": "flood"}
            )
            assert second.status_code == 409, second.text
            assert name in second.json()["detail"]
    finally:
        _delete_by_name([name])


def test_create_distinct_name_succeeds() -> None:
    """A differently-cased name does NOT collide — uniqueness is exact."""
    base = uuid.uuid4().hex[:8]
    lower = f"casecheck {base}"
    upper = f"Casecheck {base}"
    try:
        with TestClient(app) as client:
            first = client.post(
                "/admin/crises", json={"name": lower, "status": "inactive", "type": "flood"}
            )
            assert first.status_code == 201, first.text

            second = client.post(
                "/admin/crises", json={"name": upper, "status": "inactive", "type": "flood"}
            )
            assert second.status_code == 201, second.text
    finally:
        _delete_by_name([lower, upper])


def test_patch_rename_to_existing_name_returns_409() -> None:
    """Renaming one crisis onto another's name collides with a clean 409."""
    taken = f"Taken {uuid.uuid4().hex[:8]}"
    other = f"Other {uuid.uuid4().hex[:8]}"
    _seed_crisis(taken)
    mover = _seed_crisis(other)
    try:
        with TestClient(app) as client:
            response = client.patch(f"/admin/crises/{mover}", json={"name": taken})
            assert response.status_code == 409, response.text
            assert taken in response.json()["detail"]
    finally:
        _delete_by_name([taken, other])
