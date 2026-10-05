"""Public `full`-mode report detail — gating + retroactivity + signed URL.

Covers `GET /public/reports/{id}` against a real Postgres + a stubbed
`PhotoUrlSigner`:
  * happy path: 200 with the public-safe field set + a 15-minute
    signed URL.
  * hidden fields are absent from the JSON payload.
  * gating: 404 for `aggregate_view`, `buildings`, `none` crises;
    404 for `inactive`/`archived` crises; 404 for unknown report.
  * retroactivity: insert a row, flip `full → none` in the same
    transaction, assert the detail call 404s atomically.
  * retroactivity on `reports.public_visible`: flip `true → false`,
    assert 404.
  * `Cache-Control` header on 200.

Skipped if Supabase is not reachable, matching the integration-test
pattern used across the suite.
"""

from __future__ import annotations

import asyncio
import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from api.core.config import get_settings
from api.crises.public import get_photo_url_signer
from api.main import app

pytestmark = pytest.mark.integration


# --- Seed + cleanup helpers ---------------------------------------------


async def _seed_crisis(
    engine_url: str,
    *,
    public_visibility: str = "full",
    status: str = "active",
) -> uuid.UUID:
    crisis_id = uuid.uuid4()
    engine = create_async_engine(engine_url)
    try:
        async with engine.begin() as conn:
            await conn.execute(
                text(
                    "insert into public.crises "
                    "  (id, name, status, public_visibility, heatmap_k_anonymity) "
                    "values (:id, :n, :s, :pv, 1)"
                ),
                {
                    "id": str(crisis_id),
                    "n": f"detail-test {public_visibility}/{status} {uuid.uuid4().hex[:6]}",
                    "s": status,
                    "pv": public_visibility,
                },
            )
    finally:
        await engine.dispose()
    return crisis_id


async def _seed_report(
    engine_url: str,
    *,
    crisis_id: uuid.UUID,
    public_visible: bool = True,
    description: str | None = None,
    route_description: str | None = None,
    client_id: uuid.UUID | None = None,
) -> uuid.UUID:
    report_id = uuid.uuid4()
    engine = create_async_engine(engine_url)
    try:
        async with engine.begin() as conn:
            await conn.execute(
                text(
                    "insert into public.reports "
                    "  (id, crisis_id, damage_class, photo_path, "
                    "   description, route_description, "
                    "   client_id, "
                    "   location, public_visible) "
                    "values (:id, :cid, 'partial', :p, :desc, "
                    "        :route_desc, :clid, "
                    "        st_setsrid(st_makepoint(:lng, :lat), 4326)::geography, :pv)"
                ),
                {
                    "id": str(report_id),
                    "cid": str(crisis_id),
                    "p": f"reports/{report_id}.jpg",
                    "desc": description,
                    "route_desc": route_description,
                    "clid": str(client_id) if client_id is not None else None,
                    "lat": 25.30,
                    "lng": 51.50,
                    "pv": public_visible,
                },
            )
    finally:
        await engine.dispose()
    return report_id


async def _set_crisis_visibility(engine_url: str, crisis_id: uuid.UUID, value: str) -> None:
    engine = create_async_engine(engine_url)
    try:
        async with engine.begin() as conn:
            await conn.execute(
                text("update public.crises set public_visibility = :pv where id = :id"),
                {"id": str(crisis_id), "pv": value},
            )
    finally:
        await engine.dispose()


async def _set_report_visible(engine_url: str, report_id: uuid.UUID, value: bool) -> None:
    engine = create_async_engine(engine_url)
    try:
        async with engine.begin() as conn:
            await conn.execute(
                text("update public.reports set public_visible = :v where id = :id"),
                {"id": str(report_id), "v": value},
            )
    finally:
        await engine.dispose()


async def _cleanup(engine_url: str, *, crisis_ids: list[uuid.UUID]) -> None:
    engine = create_async_engine(engine_url)
    try:
        async with engine.begin() as conn:
            await conn.execute(
                text("delete from public.reports where crisis_id = any(:ids)"),
                {"ids": [str(i) for i in crisis_ids]},
            )
            await conn.execute(
                text("delete from public.crises where id = any(:ids)"),
                {"ids": [str(i) for i in crisis_ids]},
            )
    finally:
        await engine.dispose()


# --- Fake signer --------------------------------------------------------


class _FakeSigner:
    """Stub `PhotoUrlSigner` — keeps test runs decoupled from Supabase
    Storage. Records the (path, ttl) pair so we can assert the 15-minute
    TTL contract."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, int]] = []

    async def sign_photo_url(self, photo_path: str, ttl_seconds: int) -> str:
        self.calls.append((photo_path, ttl_seconds))
        return f"https://signed.test/{photo_path}?ttl={ttl_seconds}"


# --- Happy path ---------------------------------------------------------


def test_detail_returns_public_safe_payload_with_signed_url() -> None:
    settings = get_settings()
    crisis_id = asyncio.run(_seed_crisis(settings.database_url))
    report_id = asyncio.run(
        _seed_report(
            settings.database_url,
            crisis_id=crisis_id,
            description="visible to public",
            route_description="also hidden",
            client_id=uuid.uuid4(),
        )
    )

    fake = _FakeSigner()
    app.dependency_overrides[get_photo_url_signer] = lambda: fake
    try:
        with TestClient(app) as client:
            response = client.get(f"/public/reports/{report_id}")
        assert response.status_code == 200, response.text
        body = response.json()

        # The signed URL is produced and the TTL is the platform 15-min
        # default (matches admin).
        assert body["photo_url"].startswith("https://signed.test/")
        assert fake.calls and fake.calls[0][1] == 15 * 60

        # Public-safe field set: exact key parity with the spec.
        assert set(body.keys()) == {
            "id",
            "crisis_id",
            "damage_class",
            "description",
            "infra_type",
            "infra_name",
            "crisis_type",
            "crisis_type_detailed",
            "debris",
            "building_id",
            "location",
            "building_centroid",
            "photo_url",
            "created_at",
        }
        # And the hidden columns are really absent.
        for hidden in (
            "route_description",
            "client_id",
            "client_submission_id",
            "photo_captured_at",
            "photo_exif_extracted_at",
            "photo_exif_gps",
            "photo_exif_meta",
            "photo_path",
        ):
            assert hidden not in body, f"hidden field surfaced on detail: {hidden}"
        # The visible description IS present.
        assert body["description"] == "visible to public"
        # Location round-trips back as floats.
        assert body["location"] is not None
        assert abs(body["location"]["lat"] - 25.30) < 1e-3
        assert abs(body["location"]["lng"] - 51.50) < 1e-3
    finally:
        app.dependency_overrides.pop(get_photo_url_signer, None)
        asyncio.run(_cleanup(settings.database_url, crisis_ids=[crisis_id]))


def test_detail_cache_control_header() -> None:
    settings = get_settings()
    crisis_id = asyncio.run(_seed_crisis(settings.database_url))
    report_id = asyncio.run(_seed_report(settings.database_url, crisis_id=crisis_id))

    app.dependency_overrides[get_photo_url_signer] = lambda: _FakeSigner()
    try:
        with TestClient(app) as client:
            response = client.get(f"/public/reports/{report_id}")
        assert response.status_code == 200, response.text
        assert response.headers.get("Cache-Control") == "public, max-age=15"
    finally:
        app.dependency_overrides.pop(get_photo_url_signer, None)
        asyncio.run(_cleanup(settings.database_url, crisis_ids=[crisis_id]))


# --- Gating -------------------------------------------------------------


@pytest.mark.parametrize("visibility", ["none", "aggregate_view", "buildings"])
def test_detail_other_modes_return_404(visibility: str) -> None:
    settings = get_settings()
    crisis_id = asyncio.run(_seed_crisis(settings.database_url, public_visibility=visibility))
    report_id = asyncio.run(_seed_report(settings.database_url, crisis_id=crisis_id))

    app.dependency_overrides[get_photo_url_signer] = lambda: _FakeSigner()
    try:
        with TestClient(app) as client:
            response = client.get(f"/public/reports/{report_id}")
        assert response.status_code == 404, response.text
    finally:
        app.dependency_overrides.pop(get_photo_url_signer, None)
        asyncio.run(_cleanup(settings.database_url, crisis_ids=[crisis_id]))


@pytest.mark.parametrize("status", ["inactive", "archived"])
def test_detail_non_active_status_returns_404(status: str) -> None:
    settings = get_settings()
    crisis_id = asyncio.run(
        _seed_crisis(settings.database_url, public_visibility="full", status=status)
    )
    report_id = asyncio.run(_seed_report(settings.database_url, crisis_id=crisis_id))

    app.dependency_overrides[get_photo_url_signer] = lambda: _FakeSigner()
    try:
        with TestClient(app) as client:
            response = client.get(f"/public/reports/{report_id}")
        assert response.status_code == 404, response.text
    finally:
        app.dependency_overrides.pop(get_photo_url_signer, None)
        asyncio.run(_cleanup(settings.database_url, crisis_ids=[crisis_id]))


def test_detail_unknown_report_returns_404() -> None:
    app.dependency_overrides[get_photo_url_signer] = lambda: _FakeSigner()
    try:
        with TestClient(app) as client:
            response = client.get(f"/public/reports/{uuid.uuid4()}")
        assert response.status_code == 404, response.text
    finally:
        app.dependency_overrides.pop(get_photo_url_signer, None)


def test_detail_hidden_report_returns_404() -> None:
    """A report with `public_visible = false` 404s even when the crisis
    is in `full` mode. The retroactivity gate evaluates the flag on the
    materialised row."""
    settings = get_settings()
    crisis_id = asyncio.run(_seed_crisis(settings.database_url))
    report_id = asyncio.run(
        _seed_report(settings.database_url, crisis_id=crisis_id, public_visible=False)
    )

    app.dependency_overrides[get_photo_url_signer] = lambda: _FakeSigner()
    try:
        with TestClient(app) as client:
            response = client.get(f"/public/reports/{report_id}")
        assert response.status_code == 404, response.text
    finally:
        app.dependency_overrides.pop(get_photo_url_signer, None)
        asyncio.run(_cleanup(settings.database_url, crisis_ids=[crisis_id]))


# --- Retroactivity (the load-bearing one) -------------------------------


def test_detail_retroactive_crisis_mode_flip_returns_404() -> None:
    """Insert a row in a `full`-mode crisis; verify 200. Flip the crisis
    to `none`; verify the very next detail call returns 404 atomically.
    Flip back to `full`; verify 200 again. Contract: a coordinator who
    flips `full → none` mid-session must see the detail endpoint 404
    without an application restart and without any cache invalidation step."""
    settings = get_settings()
    crisis_id = asyncio.run(_seed_crisis(settings.database_url, public_visibility="full"))
    report_id = asyncio.run(_seed_report(settings.database_url, crisis_id=crisis_id))

    app.dependency_overrides[get_photo_url_signer] = lambda: _FakeSigner()
    try:
        with TestClient(app) as client:
            first = client.get(f"/public/reports/{report_id}")
            assert first.status_code == 200, first.text

            # Flip the crisis out of `full` — between two API calls.
            asyncio.run(_set_crisis_visibility(settings.database_url, crisis_id, "none"))

            second = client.get(f"/public/reports/{report_id}")
            assert second.status_code == 404, second.text

            # Flip back — the row comes back.
            asyncio.run(_set_crisis_visibility(settings.database_url, crisis_id, "full"))
            third = client.get(f"/public/reports/{report_id}")
            assert third.status_code == 200, third.text
    finally:
        app.dependency_overrides.pop(get_photo_url_signer, None)
        asyncio.run(_cleanup(settings.database_url, crisis_ids=[crisis_id]))


def test_detail_retroactive_public_visible_flip_returns_404() -> None:
    """Same shape as the mode flip, but on `reports.public_visible`. The
    single-query gate evaluates both flags on the same materialised row
    so flipping either takes effect on the next request."""
    settings = get_settings()
    crisis_id = asyncio.run(_seed_crisis(settings.database_url, public_visibility="full"))
    report_id = asyncio.run(_seed_report(settings.database_url, crisis_id=crisis_id))

    app.dependency_overrides[get_photo_url_signer] = lambda: _FakeSigner()
    try:
        with TestClient(app) as client:
            assert client.get(f"/public/reports/{report_id}").status_code == 200

            asyncio.run(_set_report_visible(settings.database_url, report_id, False))
            assert client.get(f"/public/reports/{report_id}").status_code == 404

            asyncio.run(_set_report_visible(settings.database_url, report_id, True))
            assert client.get(f"/public/reports/{report_id}").status_code == 200
    finally:
        app.dependency_overrides.pop(get_photo_url_signer, None)
        asyncio.run(_cleanup(settings.database_url, crisis_ids=[crisis_id]))


def test_detail_signed_url_carries_default_ttl() -> None:
    """The handler MUST pass `DEFAULT_SIGNED_URL_TTL_SECONDS` (15 * 60)
    to the signer — no public-specific shorter TTL."""
    settings = get_settings()
    crisis_id = asyncio.run(_seed_crisis(settings.database_url))
    report_id = asyncio.run(_seed_report(settings.database_url, crisis_id=crisis_id))

    fake = _FakeSigner()
    app.dependency_overrides[get_photo_url_signer] = lambda: fake
    try:
        with TestClient(app) as client:
            response = client.get(f"/public/reports/{report_id}")
        assert response.status_code == 200, response.text
        # One signer call, exact 15-minute TTL.
        assert len(fake.calls) == 1
        path, ttl = fake.calls[0]
        assert ttl == 15 * 60
        assert path == f"reports/{report_id}.jpg"
        # Returned URL carries the TTL we asked for — the stub echoes it.
        assert f"ttl={15 * 60}" in response.json()["photo_url"]
    finally:
        app.dependency_overrides.pop(get_photo_url_signer, None)
        asyncio.run(_cleanup(settings.database_url, crisis_ids=[crisis_id]))
