"""End-to-end coverage for the `photo_metadata` wire field.

The PWA extracts EXIF before its canvas re-encode strips it from the
uploaded JPEG and includes the result in the `data` JSON on POST /reports.

Mirrors the live-stack pattern used by `test_reports_route_description.py`:
seed a crisis directly, drive the route via `TestClient`, assert the row
landed correctly, and verify the coordinator admin detail re-emits the
metadata in camelCase. Cleanup is in `finally`.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from api.admin.report_routes import get_photo_url_signer
from api.core.config import get_settings
from api.main import app
from api.schemas.reports import (
    PhotoCamera,
    PhotoGps,
    PhotoMetadata,
    ReportSubmitPayload,
)

FIXTURE = Path(__file__).parent.parent / "fixtures" / "tiny.jpg"

_FLOAT_TOL = 1e-6


def _close(a: float, b: float) -> bool:
    return abs(a - b) < _FLOAT_TOL


pytestmark = pytest.mark.integration


def _seed_crisis() -> uuid.UUID:
    settings = get_settings()
    crisis_id = uuid.uuid4()
    suffix = uuid.uuid4().hex[:8]

    async def _run() -> None:
        engine = create_async_engine(settings.database_url)
        try:
            async with engine.begin() as conn:
                await conn.execute(
                    text(
                        "insert into public.crises (id, name, status, created_at) "
                        "values (:id, :n, 'active', :t)"
                    ),
                    {
                        "id": str(crisis_id),
                        "n": f"Photo-meta crisis {suffix}",
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


def _read_row(report_id: uuid.UUID) -> dict[str, Any]:
    settings = get_settings()

    async def _run() -> dict[str, Any]:
        engine = create_async_engine(settings.database_url)
        try:
            async with engine.connect() as conn:
                row = (
                    await conn.execute(
                        text(
                            """
                            select
                                photo_captured_at,
                                photo_exif_extracted_at,
                                photo_exif_meta,
                                st_y(photo_exif_gps::geometry) as exif_lat,
                                st_x(photo_exif_gps::geometry) as exif_lng
                            from public.reports
                            where id = :id
                            """
                        ),
                        {"id": str(report_id)},
                    )
                ).one()
                return {
                    "photo_captured_at": row.photo_captured_at,
                    "photo_exif_extracted_at": row.photo_exif_extracted_at,
                    "photo_exif_meta": row.photo_exif_meta,
                    "exif_lat": row.exif_lat,
                    "exif_lng": row.exif_lng,
                }
        finally:
            await engine.dispose()

    return asyncio.run(_run())


def _post(
    client: TestClient,
    crisis_id: uuid.UUID,
    *,
    payload_extra: dict[str, Any],
) -> httpx.Response:
    payload: dict[str, Any] = {
        "crisis_id": str(crisis_id),
        "damage_class": "minimal",
        # Satisfies the minimum-content gate (location OR route). Note: the
        # photo's EXIF GPS in `photo_metadata` is a separate signal and does
        # NOT count toward the gate.
        "location": {"lat": 25.2854, "lng": 51.5310},
        **payload_extra,
    }
    files = {
        "photo": ("tiny.jpg", FIXTURE.read_bytes(), "image/jpeg"),
        "data": (None, json.dumps(payload), "application/json"),
    }
    return client.post("/reports", files=files)


class _FakeSigner:
    async def sign_photo_url(self, photo_path: str, ttl_seconds: int) -> str:
        return f"https://signed.test/{photo_path}?ttl={ttl_seconds}"


# --- Pydantic validation (no DB) ----------------------------------------


def test_photo_metadata_optional_on_payload() -> None:
    payload = ReportSubmitPayload.model_validate(
        {
            "crisis_id": str(uuid.uuid4()),
            "damage_class": "minimal",
        }
    )
    assert payload.photo_metadata is None


def test_photo_metadata_accepts_camelcase_keys() -> None:
    payload = ReportSubmitPayload.model_validate(
        {
            "crisis_id": str(uuid.uuid4()),
            "damage_class": "minimal",
            "photo_metadata": {
                "gps": {"latitude": 25.28, "longitude": 51.53, "accuracy": 12},
                "capturedAt": "2026-05-14T10:00:00Z",
                "orientation": 6,
                "width": 4032,
                "height": 3024,
                "camera": {"make": "Apple", "model": "iPhone 15", "software": "iOS 17.4"},
                "extractedAt": "2026-05-14T10:05:32Z",
            },
        }
    )
    assert payload.photo_metadata is not None
    assert payload.photo_metadata.captured_at is not None
    assert payload.photo_metadata.extracted_at is not None
    assert payload.photo_metadata.gps == PhotoGps(latitude=25.28, longitude=51.53, accuracy=12)
    assert payload.photo_metadata.camera == PhotoCamera(
        make="Apple", model="iPhone 15", software="iOS 17.4"
    )


def test_photo_metadata_requires_extracted_at() -> None:
    with pytest.raises(ValueError):
        PhotoMetadata.model_validate(
            {
                "gps": {"latitude": 25.28, "longitude": 51.53},
                # `extractedAt` omitted: the PWA always sends it, so it is required.
            }
        )


def test_photo_metadata_rejects_out_of_range_latitude() -> None:
    with pytest.raises(ValueError):
        PhotoMetadata.model_validate(
            {
                "gps": {"latitude": 95.0, "longitude": 0.0},
                "extractedAt": "2026-05-14T10:05:32Z",
            }
        )


# --- Live-stack round-trip ----------------------------------------------


def test_post_reports_persists_photo_metadata() -> None:
    crisis_id = _seed_crisis()
    try:
        with TestClient(app) as client:
            response = _post(
                client,
                crisis_id,
                payload_extra={
                    "photo_metadata": {
                        "gps": {"latitude": 25.28, "longitude": 51.53, "accuracy": 12},
                        "capturedAt": "2026-05-14T10:00:00Z",
                        "orientation": 6,
                        "width": 4032,
                        "height": 3024,
                        "camera": {
                            "make": "Apple",
                            "model": "iPhone 15",
                            "software": "iOS 17.4",
                        },
                        "extractedAt": "2026-05-14T10:05:32Z",
                    },
                },
            )
        assert response.status_code == 200, response.text
        report_id = uuid.UUID(response.json()["id"])

        row = _read_row(report_id)
        assert row["photo_captured_at"] is not None
        assert row["photo_exif_extracted_at"] is not None
        assert _close(row["exif_lat"], 25.28)
        assert _close(row["exif_lng"], 51.53)
        meta = row["photo_exif_meta"]
        assert isinstance(meta, dict)
        assert meta["orientation"] == 6
        assert meta["width"] == 4032
        assert meta["height"] == 3024
        assert meta["camera"] == {
            "make": "Apple",
            "model": "iPhone 15",
            "software": "iOS 17.4",
        }
        assert meta["gps_accuracy"] == 12
    finally:
        _cleanup_crisis(crisis_id)


def test_admin_detail_surfaces_photo_metadata_camelcase() -> None:
    crisis_id = _seed_crisis()
    app.dependency_overrides[get_photo_url_signer] = lambda: _FakeSigner()
    try:
        with TestClient(app) as client:
            submit = _post(
                client,
                crisis_id,
                payload_extra={
                    "photo_metadata": {
                        "gps": {"latitude": 25.28, "longitude": 51.53},
                        "capturedAt": "2026-05-14T10:00:00Z",
                        "orientation": 1,
                        "width": 1024,
                        "height": 768,
                        "camera": {"make": "Apple"},
                        "extractedAt": "2026-05-14T10:05:32Z",
                    },
                },
            )
            assert submit.status_code == 200, submit.text
            report_id = uuid.UUID(submit.json()["id"])

            detail = client.get(f"/admin/reports/{report_id}")
        assert detail.status_code == 200, detail.text
        body = detail.json()
        meta = body["photo_metadata"]
        # camelCase serialisation, matches PWA `PhotoMetadata`.
        assert "capturedAt" in meta
        assert "extractedAt" in meta
        assert meta["orientation"] == 1
        assert meta["width"] == 1024
        assert meta["height"] == 768
        assert meta["camera"] == {"make": "Apple", "model": None, "software": None}
        assert _close(meta["gps"]["latitude"], 25.28)
        assert _close(meta["gps"]["longitude"], 51.53)
        assert meta["gps"]["accuracy"] is None
    finally:
        app.dependency_overrides.pop(get_photo_url_signer, None)
        _cleanup_crisis(crisis_id)


def test_admin_detail_photo_metadata_null_when_absent() -> None:
    crisis_id = _seed_crisis()
    app.dependency_overrides[get_photo_url_signer] = lambda: _FakeSigner()
    try:
        with TestClient(app) as client:
            submit = _post(client, crisis_id, payload_extra={})
            assert submit.status_code == 200, submit.text
            report_id = uuid.UUID(submit.json()["id"])

            detail = client.get(f"/admin/reports/{report_id}")
        assert detail.status_code == 200, detail.text
        assert detail.json()["photo_metadata"] is None
    finally:
        app.dependency_overrides.pop(get_photo_url_signer, None)
        _cleanup_crisis(crisis_id)
