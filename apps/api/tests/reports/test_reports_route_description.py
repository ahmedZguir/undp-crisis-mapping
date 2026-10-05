"""End-to-end coverage for `reports.route_description`.

Drives the full submit path (FastAPI → Pydantic → Supabase Storage → Postgres) and
checks the read-side schemas (`POST /reports` echo, citizen history,
admin detail). Mirrors the live-stack pattern used in
`test_reports_integration.py` and `test_reports_history_integration.py`:
seed crises directly, drive the route via `TestClient`, clean up in
`finally`.

Privacy posture: coordinator-only by default; the field is included on
owner-scoped + coordinator responses, omitted from the admin-list shape,
and never surfaced on the public-browse layer.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from api.admin.report_routes import get_photo_url_signer
from api.core.config import get_settings
from api.main import app
from api.schemas.reports import ReportSubmitPayload

FIXTURE = Path(__file__).parent.parent / "fixtures" / "tiny.jpg"


pytestmark = pytest.mark.integration


# --- helpers -------------------------------------------------------------


def _seed_crisis(status: str = "active") -> uuid.UUID:
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
                        "values (:id, :n, :s, :t)"
                    ),
                    {
                        "id": str(crisis_id),
                        "n": f"Route-desc crisis {suffix}",
                        "s": status,
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


def _read_route_description(report_id: uuid.UUID) -> str | None:
    settings = get_settings()

    async def _run() -> str | None:
        engine = create_async_engine(settings.database_url)
        try:
            async with engine.connect() as conn:
                row = (
                    await conn.execute(
                        text("select route_description from public.reports where id = :id"),
                        {"id": str(report_id)},
                    )
                ).one()
                return row.route_description
        finally:
            await engine.dispose()

    return asyncio.run(_run())


def _post(
    client: TestClient,
    crisis_id: uuid.UUID,
    *,
    client_id: uuid.UUID | None = None,
    route_description: str | None = None,
    client_submission_id: uuid.UUID | None = None,
) -> httpx.Response:
    payload: dict[str, object] = {
        "crisis_id": str(crisis_id),
        "damage_class": "minimal",
        # A GPS fix satisfies the minimum-content gate so these tests can
        # exercise route_description omission/empty without tripping the
        # location-or-route pair.
        "location": {"lat": 25.2854, "lng": 51.5310},
    }
    if client_id is not None:
        payload["client_id"] = str(client_id)
    if route_description is not None:
        payload["route_description"] = route_description
    if client_submission_id is not None:
        payload["client_submission_id"] = str(client_submission_id)
    files = {
        "photo": ("tiny.jpg", FIXTURE.read_bytes(), "image/jpeg"),
        "data": (None, json.dumps(payload), "application/json"),
    }
    return client.post("/reports", files=files)


class _FakeSigner:
    """Stub `PhotoUrlSigner` — admin-detail tests don't need a real URL."""

    async def sign_photo_url(self, photo_path: str, ttl_seconds: int) -> str:
        return f"https://signed.test/{photo_path}?ttl={ttl_seconds}"


# --- Pydantic-layer validator (no DB needed) -----------------------------


def test_payload_trims_leading_and_trailing_whitespace() -> None:
    payload = ReportSubmitPayload.model_validate(
        {
            "crisis_id": str(uuid.uuid4()),
            "damage_class": "minimal",
            "route_description": "  third house past the blue mosque  ",
        }
    )
    assert payload.route_description == "third house past the blue mosque"


def test_payload_normalizes_empty_string_to_none() -> None:
    payload = ReportSubmitPayload.model_validate(
        {
            "crisis_id": str(uuid.uuid4()),
            "damage_class": "minimal",
            "route_description": "",
        }
    )
    assert payload.route_description is None


def test_payload_normalizes_whitespace_only_to_none() -> None:
    payload = ReportSubmitPayload.model_validate(
        {
            "crisis_id": str(uuid.uuid4()),
            "damage_class": "minimal",
            "route_description": "   \n\t  ",
        }
    )
    assert payload.route_description is None


def test_payload_rejects_over_1000_chars() -> None:
    with pytest.raises(ValueError):
        ReportSubmitPayload.model_validate(
            {
                "crisis_id": str(uuid.uuid4()),
                "damage_class": "minimal",
                "route_description": "x" * 1001,
            }
        )


def test_payload_accepts_999_chars() -> None:
    payload = ReportSubmitPayload.model_validate(
        {
            "crisis_id": str(uuid.uuid4()),
            "damage_class": "minimal",
            "route_description": "x" * 999,
        }
    )
    assert payload.route_description == "x" * 999


def test_payload_is_optional() -> None:
    payload = ReportSubmitPayload.model_validate(
        {"crisis_id": str(uuid.uuid4()), "damage_class": "minimal"}
    )
    assert payload.route_description is None


# --- POST /reports persistence -------------------------------------------


def test_post_persists_route_description() -> None:
    crisis_id = _seed_crisis()
    try:
        with TestClient(app) as client:
            response = _post(
                client,
                crisis_id,
                route_description="third house past the blue mosque",
            )
            assert response.status_code == 200, response.text
            body = response.json()
            assert body["route_description"] == "third house past the blue mosque"

            stored = _read_route_description(uuid.UUID(body["id"]))
            assert stored == "third house past the blue mosque"
    finally:
        _cleanup_crisis(crisis_id)


def test_post_omitting_field_stores_null() -> None:
    crisis_id = _seed_crisis()
    try:
        with TestClient(app) as client:
            response = _post(client, crisis_id)
            assert response.status_code == 200, response.text
            body = response.json()
            assert body["route_description"] is None
            assert _read_route_description(uuid.UUID(body["id"])) is None
    finally:
        _cleanup_crisis(crisis_id)


def test_post_strips_whitespace_before_storing() -> None:
    crisis_id = _seed_crisis()
    try:
        with TestClient(app) as client:
            response = _post(client, crisis_id, route_description="  hello  ")
            assert response.status_code == 200, response.text
            body = response.json()
            assert body["route_description"] == "hello"
            assert _read_route_description(uuid.UUID(body["id"])) == "hello"
    finally:
        _cleanup_crisis(crisis_id)


def test_post_empty_string_stores_null() -> None:
    crisis_id = _seed_crisis()
    try:
        with TestClient(app) as client:
            response = _post(client, crisis_id, route_description="")
            assert response.status_code == 200, response.text
            body = response.json()
            assert body["route_description"] is None
            assert _read_route_description(uuid.UUID(body["id"])) is None
    finally:
        _cleanup_crisis(crisis_id)


def test_post_rejects_over_1000_chars_via_http() -> None:
    crisis_id = _seed_crisis()
    try:
        with TestClient(app) as client:
            response = _post(client, crisis_id, route_description="x" * 1001)
            assert response.status_code == 400, response.text
            detail = response.json()["detail"]
            assert any("route_description" in str(err.get("loc", "")) for err in detail)
    finally:
        _cleanup_crisis(crisis_id)


def test_post_accepts_unicode() -> None:
    crisis_id = _seed_crisis()
    # Non-Latin scripts: Arabic + Tigrinya + Burmese. Byte-count > char-count,
    # so this is also a check that the 1000-char cap is character-based.
    value = "بيت يوسف خلف المسجد · ቤት ዮሴፍ ድሕሪት መስጊድ · ယူဆဖ၏ အိမ်"
    try:
        with TestClient(app) as client:
            response = _post(client, crisis_id, route_description=value)
            assert response.status_code == 200, response.text
            body = response.json()
            assert body["route_description"] == value
            assert _read_route_description(uuid.UUID(body["id"])) == value
    finally:
        _cleanup_crisis(crisis_id)


# --- Idempotency: original-wins on retry ---------------------------------


def test_post_idempotent_retry_preserves_first_route_description() -> None:
    """Same `client_submission_id`, different `route_description` on retry:
    the stored row keeps the first value (dedup unit is the submission, not
    per-field). Wire echo on the retry returns the original row id."""
    crisis_id = _seed_crisis()
    submission_id = uuid.uuid4()
    try:
        with TestClient(app) as client:
            r1 = _post(
                client,
                crisis_id,
                route_description="first version",
                client_submission_id=submission_id,
            )
            assert r1.status_code == 200, r1.text
            r2 = _post(
                client,
                crisis_id,
                route_description="second version",
                client_submission_id=submission_id,
            )
            assert r2.status_code == 200, r2.text

            # Same row, original value retained in storage.
            assert r1.json()["id"] == r2.json()["id"]
            assert _read_route_description(uuid.UUID(r1.json()["id"])) == "first version"
    finally:
        _cleanup_crisis(crisis_id)


# --- Read surfaces -------------------------------------------------------


def test_citizen_history_returns_route_description() -> None:
    crisis_id = _seed_crisis()
    client_id = uuid.uuid4()
    try:
        with TestClient(app) as client:
            posted = _post(
                client,
                crisis_id,
                client_id=client_id,
                route_description="around the corner from the school",
            )
            assert posted.status_code == 200, posted.text

            response = client.get("/reports", params={"client_id": str(client_id)})
            assert response.status_code == 200, response.text
            items = response.json()["items"]
            assert len(items) == 1
            assert items[0]["route_description"] == "around the corner from the school"
    finally:
        _cleanup_crisis(crisis_id)


def test_admin_detail_returns_route_description() -> None:
    crisis_id = _seed_crisis()
    app.dependency_overrides[get_photo_url_signer] = lambda: _FakeSigner()
    try:
        with TestClient(app) as client:
            posted = _post(client, crisis_id, route_description="behind the well")
            assert posted.status_code == 200, posted.text
            report_id = posted.json()["id"]

            detail = client.get(f"/admin/reports/{report_id}")
            assert detail.status_code == 200, detail.text
            assert detail.json()["route_description"] == "behind the well"
    finally:
        app.dependency_overrides.pop(get_photo_url_signer, None)
        _cleanup_crisis(crisis_id)


def test_admin_list_includes_route_description() -> None:
    """`route_description` is surfaced on the admin list/map as the
    "Location description" column (directions used when there is no GPS pin),
    so coordinators can scan it without opening detail. This supersedes the
    original detail-only rule; see the field comment on `AdminReportListItem`
    in `schemas/admin_reports.py`."""
    crisis_id = _seed_crisis()
    try:
        with TestClient(app) as client:
            posted = _post(client, crisis_id, route_description="behind the well")
            assert posted.status_code == 200, posted.text

            listing = client.get(f"/admin/crises/{crisis_id}/reports")
            assert listing.status_code == 200, listing.text
            items = listing.json()["items"]
            assert len(items) == 1
            assert items[0]["route_description"] == "behind the well"
    finally:
        _cleanup_crisis(crisis_id)
