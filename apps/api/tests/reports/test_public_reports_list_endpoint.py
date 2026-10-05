"""Public `full`-mode reports list — gating + schema + filtering.

Covers `GET /crises/{id}/public/reports` against a real Postgres:
  * mode-gating (`buildings`, `aggregate_view`, `none` → 404;
    `full` → 200).
  * status-gating (`inactive`, `archived` → 404).
  * unknown crisis → 404.
  * public-safe schema (hidden fields really absent in the payload).
  * `public_visible = false` filter.
  * bbox filter (in vs out).
  * cursor pagination (round-trip).
  * `Cache-Control` header on 200.
  * `bbox` + `cursor` mutual exclusion (400).

Skipped if Supabase is not reachable, matching the integration-test
pattern used across the suite.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from api.core.config import get_settings
from api.main import app

pytestmark = pytest.mark.integration


# --- Seed helpers --------------------------------------------------------


async def _seed_crisis_only(
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
                    "n": f"public-reports {public_visibility}/{status} {uuid.uuid4().hex[:6]}",
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
    damage_class: str = "partial",
    location: tuple[float, float] | None = (25.30, 51.50),
    created_at: datetime | None = None,
    public_visible: bool = True,
    description: str | None = None,
    route_description: str | None = None,
    client_id: uuid.UUID | None = None,
    client_submission_id: uuid.UUID | None = None,
) -> uuid.UUID:
    """Seed a report with optional coordinator-only fields set so we can
    assert they're absent from the public-safe response."""
    report_id = uuid.uuid4()
    location_sql = (
        "st_setsrid(st_makepoint(:lng, :lat), 4326)::geography" if location is not None else "null"
    )
    created_at_sql = "cast(:created_at as timestamptz)" if created_at is not None else "now()"
    params: dict[str, object | None] = {
        "id": str(report_id),
        "crisis_id": str(crisis_id),
        "dc": damage_class,
        "photo": f"test/{report_id}.jpg",
        "pv": public_visible,
        "desc": description,
        "route_desc": route_description,
        "cid": str(client_id) if client_id is not None else None,
        "csid": str(client_submission_id) if client_submission_id is not None else None,
    }
    if location is not None:
        params["lat"] = location[0]
        params["lng"] = location[1]
    if created_at is not None:
        params["created_at"] = created_at

    engine = create_async_engine(engine_url)
    try:
        async with engine.begin() as conn:
            await conn.execute(
                text(
                    "insert into public.reports "
                    "  (id, crisis_id, damage_class, photo_path, "
                    "   description, route_description, "
                    "   client_id, client_submission_id, "
                    "   location, public_visible, created_at) "
                    f"values (:id, :crisis_id, :dc, :photo, :desc, "
                    f"        :route_desc, :cid, :csid, "
                    f"        {location_sql}, :pv, {created_at_sql})"
                ),
                params,
            )
    finally:
        await engine.dispose()
    return report_id


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


# --- Tests: gating ------------------------------------------------------


@pytest.mark.parametrize("visibility", ["none", "aggregate_view", "buildings"])
def test_list_other_modes_return_404(visibility: str) -> None:
    """`full` is the only mode that surfaces report detail. Every other
    `public_visibility` value 404s — mirrors the buildings endpoint
    gating shape."""
    settings = get_settings()
    crisis_id = asyncio.run(_seed_crisis_only(settings.database_url, public_visibility=visibility))
    try:
        with TestClient(app) as client:
            response = client.get(f"/crises/{crisis_id}/public/reports")
        assert response.status_code == 404, response.text
    finally:
        asyncio.run(_cleanup(settings.database_url, crisis_ids=[crisis_id]))


@pytest.mark.parametrize("status", ["inactive", "archived"])
def test_list_non_active_status_returns_404(status: str) -> None:
    """An `inactive` or `archived` crisis 404s even when its mode is
    `full` — the public surface only serves `active` crises."""
    settings = get_settings()
    crisis_id = asyncio.run(
        _seed_crisis_only(settings.database_url, public_visibility="full", status=status)
    )
    try:
        with TestClient(app) as client:
            response = client.get(f"/crises/{crisis_id}/public/reports")
        assert response.status_code == 404, response.text
    finally:
        asyncio.run(_cleanup(settings.database_url, crisis_ids=[crisis_id]))


def test_list_unknown_crisis_returns_404() -> None:
    bogus = uuid.uuid4()
    with TestClient(app) as client:
        response = client.get(f"/crises/{bogus}/public/reports")
    assert response.status_code == 404, response.text


def test_list_full_mode_active_returns_200() -> None:
    settings = get_settings()
    crisis_id = asyncio.run(_seed_crisis_only(settings.database_url, public_visibility="full"))
    try:
        with TestClient(app) as client:
            response = client.get(f"/crises/{crisis_id}/public/reports")
        assert response.status_code == 200, response.text
    finally:
        asyncio.run(_cleanup(settings.database_url, crisis_ids=[crisis_id]))


# --- Tests: public-safe schema ------------------------------------------


def test_list_response_omits_hidden_fields() -> None:
    """Response items NEVER carry the coordinator-only
    columns. Pydantic + SQL projection both enforce this — assert at
    the JSON-payload layer so a future leak (e.g. someone adds a new
    column to the schema) is caught."""
    settings = get_settings()
    crisis_id = asyncio.run(_seed_crisis_only(settings.database_url, public_visibility="full"))
    asyncio.run(
        _seed_report(
            settings.database_url,
            crisis_id=crisis_id,
            description="public-safe text",
            route_description="turn left at the mosque",
            client_id=uuid.uuid4(),
            client_submission_id=uuid.uuid4(),
        )
    )
    try:
        with TestClient(app) as client:
            response = client.get(f"/crises/{crisis_id}/public/reports")
        assert response.status_code == 200, response.text
        items = response.json()["items"]
        assert len(items) == 1
        item = items[0]
        # Allow-list of expected keys (no extras, no missing).
        assert set(item.keys()) == {
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
            "map_point",
            "created_at",
        }
        # And the hidden columns are really absent — belt-and-braces.
        for hidden in (
            "route_description",
            "client_id",
            "client_submission_id",
            "photo_captured_at",
            "photo_exif_extracted_at",
            "photo_exif_gps",
            "photo_exif_meta",
            "photo_url",
            "photo_path",
        ):
            assert hidden not in item, f"hidden field surfaced on list: {hidden}"
        # The visible description IS present and carries the data.
        assert item["description"] == "public-safe text"
    finally:
        asyncio.run(_cleanup(settings.database_url, crisis_ids=[crisis_id]))


def test_list_excludes_public_visible_false_rows() -> None:
    """A `public_visible = false` row is filtered at the SQL layer."""
    settings = get_settings()
    crisis_id = asyncio.run(_seed_crisis_only(settings.database_url, public_visibility="full"))
    visible_id = asyncio.run(
        _seed_report(settings.database_url, crisis_id=crisis_id, public_visible=True)
    )
    asyncio.run(_seed_report(settings.database_url, crisis_id=crisis_id, public_visible=False))
    try:
        with TestClient(app) as client:
            response = client.get(f"/crises/{crisis_id}/public/reports")
        assert response.status_code == 200, response.text
        items = response.json()["items"]
        assert len(items) == 1
        assert items[0]["id"] == str(visible_id)
    finally:
        asyncio.run(_cleanup(settings.database_url, crisis_ids=[crisis_id]))


# --- Tests: bbox + cursor -----------------------------------------------


def test_list_bbox_filters_by_envelope() -> None:
    """Three reports in the bbox, two outside. Bbox query returns three
    items with `total_in_bbox = 3` and `truncated = false`."""
    settings = get_settings()
    crisis_id = asyncio.run(_seed_crisis_only(settings.database_url, public_visibility="full"))
    # In-bbox triplet around (25.30, 51.50).
    in_ids = [
        asyncio.run(
            _seed_report(
                settings.database_url,
                crisis_id=crisis_id,
                location=(25.30 + i * 1e-4, 51.50 + i * 1e-4),
            )
        )
        for i in range(3)
    ]
    # Out-of-bbox doubles way over (50, 100).
    asyncio.run(_seed_report(settings.database_url, crisis_id=crisis_id, location=(50.0, 100.0)))
    asyncio.run(_seed_report(settings.database_url, crisis_id=crisis_id, location=(50.1, 100.1)))

    try:
        with TestClient(app) as client:
            response = client.get(
                f"/crises/{crisis_id}/public/reports",
                params={"bbox": "51.0,25.0,52.0,26.0", "limit": 10},
            )
        assert response.status_code == 200, response.text
        body = response.json()
        ids_in_response = {item["id"] for item in body["items"]}
        assert ids_in_response == {str(i) for i in in_ids}
        assert body["total_in_bbox"] == 3
        assert body["truncated"] is False
        assert body["next_cursor"] is None
    finally:
        asyncio.run(_cleanup(settings.database_url, crisis_ids=[crisis_id]))


def test_list_bbox_truncated_when_total_exceeds_limit() -> None:
    settings = get_settings()
    crisis_id = asyncio.run(_seed_crisis_only(settings.database_url, public_visibility="full"))
    for i in range(5):
        asyncio.run(
            _seed_report(
                settings.database_url,
                crisis_id=crisis_id,
                location=(25.30 + i * 1e-4, 51.50 + i * 1e-4),
            )
        )

    try:
        with TestClient(app) as client:
            response = client.get(
                f"/crises/{crisis_id}/public/reports",
                params={"bbox": "51.0,25.0,52.0,26.0", "limit": 2},
            )
        assert response.status_code == 200, response.text
        body = response.json()
        assert len(body["items"]) == 2
        assert body["total_in_bbox"] == 5
        assert body["truncated"] is True
    finally:
        asyncio.run(_cleanup(settings.database_url, crisis_ids=[crisis_id]))


def test_list_cursor_round_trip() -> None:
    """Cursor mode: seed five reports, fetch `?limit=2` twice + once more
    for the tail. Assert pages are contiguous and the last page has
    `next_cursor = null`."""
    settings = get_settings()
    base_ts = datetime.now(UTC) - timedelta(hours=1)
    crisis_id = asyncio.run(_seed_crisis_only(settings.database_url, public_visibility="full"))
    seeded = [
        asyncio.run(
            _seed_report(
                settings.database_url,
                crisis_id=crisis_id,
                created_at=base_ts + timedelta(minutes=i),
            )
        )
        for i in range(5)
    ]
    # seeded[4] newest, seeded[0] oldest. Response order = newest first.
    expected_order = [str(i) for i in reversed(seeded)]

    try:
        with TestClient(app) as client:
            page1 = client.get(f"/crises/{crisis_id}/public/reports", params={"limit": 2}).json()
            assert [i["id"] for i in page1["items"]] == expected_order[:2]
            assert page1["next_cursor"] is not None
            assert page1["truncated"] is False
            assert page1["total_in_bbox"] is None

            page2 = client.get(
                f"/crises/{crisis_id}/public/reports",
                params={"cursor": page1["next_cursor"], "limit": 2},
            ).json()
            assert [i["id"] for i in page2["items"]] == expected_order[2:4]
            assert page2["next_cursor"] is not None

            page3 = client.get(
                f"/crises/{crisis_id}/public/reports",
                params={"cursor": page2["next_cursor"], "limit": 2},
            ).json()
            assert [i["id"] for i in page3["items"]] == expected_order[4:]
            # Last page — no next cursor (only one row left, no overflow).
            assert page3["next_cursor"] is None
    finally:
        asyncio.run(_cleanup(settings.database_url, crisis_ids=[crisis_id]))


def test_list_bbox_and_cursor_mutually_exclusive() -> None:
    settings = get_settings()
    crisis_id = asyncio.run(_seed_crisis_only(settings.database_url, public_visibility="full"))
    try:
        with TestClient(app) as client:
            response = client.get(
                f"/crises/{crisis_id}/public/reports",
                params={"bbox": "51.0,25.0,52.0,26.0", "cursor": "anything"},
            )
        assert response.status_code == 400, response.text
    finally:
        asyncio.run(_cleanup(settings.database_url, crisis_ids=[crisis_id]))


def test_list_cache_control_header() -> None:
    settings = get_settings()
    crisis_id = asyncio.run(_seed_crisis_only(settings.database_url, public_visibility="full"))
    try:
        with TestClient(app) as client:
            response = client.get(f"/crises/{crisis_id}/public/reports")
        assert response.status_code == 200, response.text
        assert response.headers.get("Cache-Control") == "public, max-age=15"
    finally:
        asyncio.run(_cleanup(settings.database_url, crisis_ids=[crisis_id]))
