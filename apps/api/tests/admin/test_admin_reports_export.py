"""Integration tests for the GeoJSON and CSV export endpoints.

`GET /admin/crises/{crisis_id}/export.geojson` streams a FeatureCollection
assembled in Postgres; `…/export.csv` is its flat sibling. These tests cover
the geometry precedence (`submitted pin -> building centroid -> AI geocode ->
null`), the enrichment sidecars riding along (`description_en`, `ai_caption`,
`ai_relevance`, `ai_geocode`), the `damage_class` / `bbox` / date-window
filters, the `metadata` block, the streaming headers, and the 404 for an
unknown crisis. The CSV tests additionally check the column contract, the
JSON-text encoding of nested fields, the lon/lat flattening, and the BOM.

Requires `supabase start` for the Postgres side; no storage backend is touched.
"""

from __future__ import annotations

import asyncio
import csv
import io
import json
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from api.core.config import get_settings
from api.main import app

pytestmark = pytest.mark.integration


# --- Seed helpers --------------------------------------------------------


async def _seed_crisis(engine_url: str, *, name: str) -> uuid.UUID:
    crisis_id = uuid.uuid4()
    engine = create_async_engine(engine_url)
    try:
        async with engine.begin() as conn:
            await conn.execute(
                text(
                    "insert into public.crises (id, name, status, type, countries) "
                    "values (:id, :n, 'active', 'flood', ARRAY['QA']::text[])"
                ),
                {"id": str(crisis_id), "n": name},
            )
    finally:
        await engine.dispose()
    return crisis_id


async def _seed_report(
    engine_url: str,
    *,
    crisis_id: uuid.UUID,
    damage_class: str = "partial",
    location: tuple[float, float] | None = (25.30, 51.50),  # (lat, lng)
    building_id: uuid.UUID | None = None,
    created_at: datetime | None = None,
    photo_path: str | None = "test/photo.jpg",
    description: str | None = None,
    route_description: str | None = "test directions",
    crisis_type_detailed: str | None = None,
    infra_type: list[str] | None = None,
    infra_name: str | None = None,
    debris: str | None = None,
    generic_answers: dict[str, object] | None = None,
    photo_gps: tuple[float, float] | None = None,  # (lat, lng)
) -> uuid.UUID:
    report_id = uuid.uuid4()
    location_sql = (
        "st_setsrid(st_makepoint(:lng, :lat), 4326)::geography" if location is not None else "null"
    )
    created_at_sql = "cast(:created_at as timestamptz)" if created_at is not None else "now()"
    gps_sql = (
        "st_setsrid(st_makepoint(:glng, :glat), 4326)::geography"
        if photo_gps is not None
        else "null"
    )
    params: dict[str, object | None] = {
        "id": str(report_id),
        "crisis_id": str(crisis_id),
        "dc": damage_class,
        "photo": photo_path,
        "building_id": str(building_id) if building_id is not None else None,
        "description": description,
        "route_description": route_description,
        "ctd": crisis_type_detailed,
        "infra_type": infra_type,
        "infra_name": infra_name,
        "debris": debris,
        "ga": json.dumps(generic_answers) if generic_answers is not None else "{}",
    }
    if location is not None:
        params["lat"], params["lng"] = location
    if created_at is not None:
        params["created_at"] = created_at
    if photo_gps is not None:
        params["glat"], params["glng"] = photo_gps

    engine = create_async_engine(engine_url)
    try:
        async with engine.begin() as conn:
            await conn.execute(
                text(
                    "insert into public.reports "
                    "  (id, crisis_id, damage_class, photo_path, location, building_id, "
                    "   created_at, route_description, description, crisis_type_detailed, "
                    "   infra_type, infra_name, debris, generic_answers, photo_exif_gps) "
                    f"values (:id, :crisis_id, :dc, :photo, {location_sql}, :building_id, "
                    f"        {created_at_sql}, :route_description, :description, :ctd, "
                    f"        cast(:infra_type as text[]), :infra_name, :debris, "
                    f"        cast(:ga as jsonb), {gps_sql})"
                ),
                params,
            )
    finally:
        await engine.dispose()
    return report_id


async def _seed_building(engine_url: str, *, centroid: tuple[float, float]) -> uuid.UUID:
    building_id = uuid.uuid4()
    source_id = uuid.uuid4().hex
    lat, lng = centroid
    delta = 0.00001
    wkt = (
        "MULTIPOLYGON((("
        f"{lng - delta} {lat - delta},"
        f"{lng + delta} {lat - delta},"
        f"{lng + delta} {lat + delta},"
        f"{lng - delta} {lat + delta},"
        f"{lng - delta} {lat - delta}"
        ")))"
    )
    engine = create_async_engine(engine_url)
    try:
        async with engine.begin() as conn:
            await conn.execute(
                text(
                    "insert into public.buildings (id, source, source_id, footprint) "
                    "values (:id, 'test', :src, st_geogfromtext(:wkt))"
                ),
                {"id": str(building_id), "src": source_id, "wkt": wkt},
            )
    finally:
        await engine.dispose()
    return building_id


async def _seed_translation(
    engine_url: str,
    *,
    report_id: uuid.UUID,
    description_lang: str,
    description_en: str,
) -> None:
    engine = create_async_engine(engine_url)
    try:
        async with engine.begin() as conn:
            await conn.execute(
                text(
                    "insert into public.report_translations "
                    "  (report_id, description_lang, description_en, description_status) "
                    "values (:rid, :lang, :en, 'ready')"
                ),
                {"rid": str(report_id), "lang": description_lang, "en": description_en},
            )
    finally:
        await engine.dispose()


async def _seed_caption(
    engine_url: str,
    *,
    report_id: uuid.UUID,
    caption: str,
    relevance_label: str,
    relevance_score: float,
) -> None:
    engine = create_async_engine(engine_url)
    try:
        async with engine.begin() as conn:
            await conn.execute(
                text(
                    "insert into public.image_captions "
                    "  (report_id, caption, relevance_label, relevance_score, status) "
                    "values (:rid, :cap, :label, :score, 'ready')"
                ),
                {
                    "rid": str(report_id),
                    "cap": caption,
                    "label": relevance_label,
                    "score": relevance_score,
                },
            )
    finally:
        await engine.dispose()


async def _seed_geocode(
    engine_url: str,
    *,
    report_id: uuid.UUID,
    lat: float,
    lon: float,
    confidence: float = 0.8,
    granularity_tier: str = "street",
) -> None:
    engine = create_async_engine(engine_url)
    try:
        async with engine.begin() as conn:
            await conn.execute(
                text(
                    "insert into public.report_geocodes "
                    "  (report_id, lat, lon, confidence, granularity_tier, "
                    "   radius_m, area_only, source, status) "
                    "values (:rid, :lat, :lon, :conf, :tier, 50, false, 'osm', 'ready')"
                ),
                {
                    "rid": str(report_id),
                    "lat": lat,
                    "lon": lon,
                    "conf": confidence,
                    "tier": granularity_tier,
                },
            )
    finally:
        await engine.dispose()


async def _cleanup(
    engine_url: str,
    *,
    crisis_ids: list[uuid.UUID],
    building_ids: list[uuid.UUID] | None = None,
) -> None:
    # Deleting the reports cascades to report_translations / image_captions /
    # report_geocodes (all ON DELETE CASCADE on report_id).
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
            if building_ids:
                await conn.execute(
                    text("delete from public.buildings where id = any(:ids)"),
                    {"ids": [str(i) for i in building_ids]},
                )
    finally:
        await engine.dispose()


def _feature_by_id(body: Any, report_id: uuid.UUID) -> Any:
    return next(f for f in body["features"] if f["id"] == str(report_id))


# --- Tests ---------------------------------------------------------------


def test_export_returns_geojson_envelope_and_headers() -> None:
    settings = get_settings()
    name = f"Export test {uuid.uuid4().hex[:8]}"
    crisis_id = asyncio.run(_seed_crisis(settings.database_url, name=name))
    asyncio.run(_seed_report(settings.database_url, crisis_id=crisis_id))

    try:
        with TestClient(app) as client:
            response = client.get(f"/admin/crises/{crisis_id}/export.geojson")
        assert response.status_code == 200, response.text
        assert response.headers["content-type"].startswith("application/geo+json")
        disposition = response.headers["content-disposition"]
        assert "attachment" in disposition
        # Named after the crisis, not its UUID: ASCII slug + RFC 5987 full name.
        slug = name.lower().replace(" ", "-")
        assert f'filename="{slug}.geojson"' in disposition
        assert "filename*=UTF-8''" in disposition

        body = response.json()
        assert body["type"] == "FeatureCollection"
        assert isinstance(body["features"], list)
        meta = body["metadata"]
        assert meta["crisis"] == {"id": str(crisis_id), "name": name}
        assert meta["report_count"] == 1
        assert meta["schema_version"] == 1
        assert "CRS84" in meta["crs"]
        assert meta["filters"] == {
            "damage_class": None,
            "bbox": None,
            "date_from": None,
            "date_to": None,
        }
        assert meta["generated_at"].endswith("+00:00")
    finally:
        asyncio.run(_cleanup(settings.database_url, crisis_ids=[crisis_id]))


def test_export_feature_carries_full_properties() -> None:
    settings = get_settings()
    name = f"Rich {uuid.uuid4().hex[:8]}"
    crisis_id = asyncio.run(_seed_crisis(settings.database_url, name=name))
    report_id = asyncio.run(
        _seed_report(
            settings.database_url,
            crisis_id=crisis_id,
            damage_class="complete",
            location=(25.30, 51.50),
            description="نص عربي",
            crisis_type_detailed="building_collapse",
            infra_type=["school", "hospital"],
            infra_name="Al Noor School",
            debris="yes",
            generic_answers={"people_trapped": "yes"},
            photo_gps=(25.31, 51.51),
        )
    )
    asyncio.run(
        _seed_translation(
            settings.database_url,
            report_id=report_id,
            description_lang="ar",
            description_en="Arabic text",
        )
    )
    asyncio.run(
        _seed_caption(
            settings.database_url,
            report_id=report_id,
            caption="A collapsed building",
            relevance_label="relevant",
            relevance_score=0.91,
        )
    )
    asyncio.run(_seed_geocode(settings.database_url, report_id=report_id, lat=25.32, lon=51.52))

    try:
        with TestClient(app) as client:
            body = client.get(f"/admin/crises/{crisis_id}/export.geojson").json()
        feature = _feature_by_id(body, report_id)

        assert feature["type"] == "Feature"
        # Geometry is the submitted pin, in [lon, lat] order.
        geom = feature["geometry"]
        assert geom["type"] == "Point"
        assert abs(geom["coordinates"][0] - 51.50) < 1e-6
        assert abs(geom["coordinates"][1] - 25.30) < 1e-6

        props = feature["properties"]
        assert props["crisis_id"] == str(crisis_id)
        assert props["damage_class"] == "complete"
        assert props["description"] == "نص عربي"
        assert props["description_lang"] == "ar"
        assert props["description_en"] == "Arabic text"
        assert props["crisis_type"] == "building_collapse"
        assert props["infra_type"] == ["school", "hospital"]
        assert props["infra_name"] == "Al Noor School"
        assert props["debris"] == "yes"
        assert props["location_source"] == "submitted_pin"
        assert props["has_photo"] is True
        # photo_path (internal storage path) is deliberately not exported.
        assert "photo_path" not in props
        assert props["survey"] == {"people_trapped": "yes"}
        assert props["created_at"].endswith("Z")

        # Photo EXIF GPS is a standalone [lon, lat] property, not the geometry.
        assert abs(props["photo_gps"][0] - 51.51) < 1e-6
        assert abs(props["photo_gps"][1] - 25.31) < 1e-6

        # Machine-derived enrichment rides along.
        assert props["ai_caption"] == "A collapsed building"
        assert props["ai_relevance"] == "relevant"
        geocode = props["ai_geocode"]
        assert abs(geocode["coordinates"][0] - 51.52) < 1e-6
        assert abs(geocode["coordinates"][1] - 25.32) < 1e-6
        assert geocode["granularity_tier"] == "street"
        assert geocode["source"] == "osm"
    finally:
        asyncio.run(_cleanup(settings.database_url, crisis_ids=[crisis_id]))


def test_export_geometry_precedence_falls_through() -> None:
    """pin > building centroid > AI geocode > null, with location_source set."""
    settings = get_settings()
    crisis_id = asyncio.run(
        _seed_crisis(settings.database_url, name=f"Precedence {uuid.uuid4().hex[:8]}")
    )
    building_id = asyncio.run(_seed_building(settings.database_url, centroid=(25.30, 51.50)))

    # (a) no pin, but a building -> building_centroid
    centroid_report = asyncio.run(
        _seed_report(
            settings.database_url, crisis_id=crisis_id, location=None, building_id=building_id
        )
    )
    # (b) no pin, no building, but a geocode -> ai_geocode
    geocode_report = asyncio.run(
        _seed_report(settings.database_url, crisis_id=crisis_id, location=None)
    )
    asyncio.run(_seed_geocode(settings.database_url, report_id=geocode_report, lat=10.0, lon=20.0))
    # (c) nothing locatable -> null geometry
    null_report = asyncio.run(
        _seed_report(settings.database_url, crisis_id=crisis_id, location=None)
    )

    try:
        with TestClient(app) as client:
            body = client.get(f"/admin/crises/{crisis_id}/export.geojson").json()

        centroid = _feature_by_id(body, centroid_report)
        assert centroid["properties"]["location_source"] == "building_centroid"
        assert centroid["geometry"]["type"] == "Point"
        assert abs(centroid["geometry"]["coordinates"][0] - 51.50) < 1e-3

        geocode = _feature_by_id(body, geocode_report)
        assert geocode["properties"]["location_source"] == "ai_geocode"
        assert geocode["geometry"]["coordinates"] == [20.0, 10.0]

        unlocated = _feature_by_id(body, null_report)
        assert unlocated["geometry"] is None
        assert unlocated["properties"]["location_source"] is None
    finally:
        asyncio.run(
            _cleanup(settings.database_url, crisis_ids=[crisis_id], building_ids=[building_id])
        )


def test_export_respects_damage_class_filter() -> None:
    settings = get_settings()
    crisis_id = asyncio.run(
        _seed_crisis(settings.database_url, name=f"Filter {uuid.uuid4().hex[:8]}")
    )
    keep = asyncio.run(
        _seed_report(settings.database_url, crisis_id=crisis_id, damage_class="complete")
    )
    drop = asyncio.run(
        _seed_report(settings.database_url, crisis_id=crisis_id, damage_class="minimal")
    )

    try:
        with TestClient(app) as client:
            body = client.get(
                f"/admin/crises/{crisis_id}/export.geojson?damage_class=complete"
            ).json()
        ids = {f["id"] for f in body["features"]}
        assert str(keep) in ids
        assert str(drop) not in ids
        assert body["metadata"]["report_count"] == 1
        assert body["metadata"]["filters"]["damage_class"] == "complete"
    finally:
        asyncio.run(_cleanup(settings.database_url, crisis_ids=[crisis_id]))


def test_export_respects_date_window() -> None:
    settings = get_settings()
    base = datetime.now(UTC) - timedelta(days=10)
    name = f"Dates {uuid.uuid4().hex[:8]}"
    crisis_id = asyncio.run(_seed_crisis(settings.database_url, name=name))
    old = asyncio.run(_seed_report(settings.database_url, crisis_id=crisis_id, created_at=base))
    recent = asyncio.run(
        _seed_report(
            settings.database_url, crisis_id=crisis_id, created_at=base + timedelta(days=5)
        )
    )

    cutoff = (base + timedelta(days=2)).isoformat()
    try:
        with TestClient(app) as client:
            # Pass via `params` so httpx percent-encodes the `+00:00` offset
            # (a bare `+` in a query string decodes to a space).
            response = client.get(
                f"/admin/crises/{crisis_id}/export.geojson", params={"date_from": cutoff}
            )
        assert response.status_code == 200, response.text
        body = response.json()
        ids = {f["id"] for f in body["features"]}
        assert str(recent) in ids
        assert str(old) not in ids
        assert body["metadata"]["filters"]["date_from"] is not None
    finally:
        asyncio.run(_cleanup(settings.database_url, crisis_ids=[crisis_id]))


def test_export_bbox_excludes_outside_points() -> None:
    settings = get_settings()
    name = f"Bbox {uuid.uuid4().hex[:8]}"
    crisis_id = asyncio.run(_seed_crisis(settings.database_url, name=name))
    inside = asyncio.run(
        _seed_report(settings.database_url, crisis_id=crisis_id, location=(25.30, 51.50))
    )
    outside = asyncio.run(
        _seed_report(settings.database_url, crisis_id=crisis_id, location=(40.00, 10.00))
    )

    try:
        with TestClient(app) as client:
            body = client.get(
                f"/admin/crises/{crisis_id}/export.geojson?bbox=51.0,25.0,52.0,26.0"
            ).json()
        ids = {f["id"] for f in body["features"]}
        assert str(inside) in ids
        assert str(outside) not in ids
        assert body["metadata"]["filters"]["bbox"] == [51.0, 25.0, 52.0, 26.0]
    finally:
        asyncio.run(_cleanup(settings.database_url, crisis_ids=[crisis_id]))


def test_export_empty_crisis_yields_valid_empty_collection() -> None:
    settings = get_settings()
    name = f"Empty {uuid.uuid4().hex[:8]}"
    crisis_id = asyncio.run(_seed_crisis(settings.database_url, name=name))
    try:
        with TestClient(app) as client:
            body = client.get(f"/admin/crises/{crisis_id}/export.geojson").json()
        assert body["type"] == "FeatureCollection"
        assert body["features"] == []
        assert body["metadata"]["report_count"] == 0
    finally:
        asyncio.run(_cleanup(settings.database_url, crisis_ids=[crisis_id]))


def test_export_returns_404_for_unknown_crisis() -> None:
    with TestClient(app) as client:
        response = client.get(f"/admin/crises/{uuid.uuid4()}/export.geojson")
    assert response.status_code == 404


def test_export_rejects_invalid_damage_class() -> None:
    settings = get_settings()
    crisis_id = asyncio.run(_seed_crisis(settings.database_url, name=f"Bad {uuid.uuid4().hex[:8]}"))
    try:
        with TestClient(app) as client:
            response = client.get(f"/admin/crises/{crisis_id}/export.geojson?damage_class=severe")
        assert response.status_code == 400
    finally:
        asyncio.run(_cleanup(settings.database_url, crisis_ids=[crisis_id]))


# --- CSV export ----------------------------------------------------------

_CSV_COLUMNS = [
    "report_id",
    "crisis_id",
    "crisis_name",
    "building_id",
    "client_id",
    "created_at",
    "photo_captured_at",
    "damage_class",
    "infra_type",
    "infra_name",
    "crisis_type",
    "debris",
    "description",
    "description_lang",
    "description_en",
    "route_description",
    "route_description_en",
    "location_source",
    "longitude",
    "latitude",
    "photo_gps",
    "has_photo",
    "survey",
    "ai_geocode",
    "ai_caption",
    "ai_relevance",
]


def _parse_csv(content: bytes) -> tuple[list[str], list[dict[str, str]]]:
    """Decode a CSV response body (utf-8-sig drops the BOM) into header + rows."""
    reader = csv.DictReader(io.StringIO(content.decode("utf-8-sig")))
    rows = list(reader)
    return list(reader.fieldnames or []), rows


def _csv_row_by_id(rows: list[dict[str, str]], report_id: uuid.UUID) -> dict[str, str]:
    return next(r for r in rows if r["report_id"] == str(report_id))


def test_csv_export_headers_columns_and_bom() -> None:
    settings = get_settings()
    name = f"CSV head {uuid.uuid4().hex[:8]}"
    crisis_id = asyncio.run(_seed_crisis(settings.database_url, name=name))
    asyncio.run(_seed_report(settings.database_url, crisis_id=crisis_id))

    try:
        with TestClient(app) as client:
            response = client.get(f"/admin/crises/{crisis_id}/export.csv")
        assert response.status_code == 200, response.text
        assert response.headers["content-type"].startswith("text/csv")
        disposition = response.headers["content-disposition"]
        assert "attachment" in disposition
        # Named after the crisis, not its UUID: ASCII slug + RFC 5987 full name.
        slug = name.lower().replace(" ", "-")
        assert f'filename="{slug}.csv"' in disposition
        assert "filename*=UTF-8''" in disposition
        # Leads with a UTF-8 BOM for Excel.
        assert response.content.startswith(b"\xef\xbb\xbf")

        header, rows = _parse_csv(response.content)
        assert header == _CSV_COLUMNS
        assert len(rows) == 1
    finally:
        asyncio.run(_cleanup(settings.database_url, crisis_ids=[crisis_id]))


def test_csv_export_full_row() -> None:
    settings = get_settings()
    name = f"CSV rich {uuid.uuid4().hex[:8]}"
    crisis_id = asyncio.run(_seed_crisis(settings.database_url, name=name))
    report_id = asyncio.run(
        _seed_report(
            settings.database_url,
            crisis_id=crisis_id,
            damage_class="complete",
            location=(25.30, 51.50),
            description="نص عربي",
            crisis_type_detailed="building_collapse",
            infra_type=["school", "hospital"],
            infra_name="Al Noor School",
            debris="yes",
            generic_answers={"people_trapped": "yes"},
            photo_gps=(25.31, 51.51),
        )
    )
    asyncio.run(
        _seed_translation(
            settings.database_url,
            report_id=report_id,
            description_lang="ar",
            description_en="Arabic text",
        )
    )
    asyncio.run(_seed_geocode(settings.database_url, report_id=report_id, lat=25.32, lon=51.52))

    try:
        with TestClient(app) as client:
            response = client.get(f"/admin/crises/{crisis_id}/export.csv")
        _, rows = _parse_csv(response.content)
        row = _csv_row_by_id(rows, report_id)

        assert row["crisis_id"] == str(crisis_id)
        assert row["crisis_name"] == name
        assert row["damage_class"] == "complete"
        assert row["description"] == "نص عربي"
        assert row["description_lang"] == "ar"
        assert row["description_en"] == "Arabic text"
        assert row["crisis_type"] == "building_collapse"
        assert row["infra_name"] == "Al Noor School"
        assert row["debris"] == "yes"
        assert row["location_source"] == "submitted_pin"
        assert row["has_photo"] == "True"
        # photo_path (internal storage path) is deliberately not exported.
        assert "photo_path" not in row
        assert row["created_at"].endswith("Z")

        # Geometry flattened to lon/lat columns.
        assert abs(float(row["longitude"]) - 51.50) < 1e-6
        assert abs(float(row["latitude"]) - 25.30) < 1e-6

        # Nested fields are JSON text in a single cell.
        assert json.loads(row["infra_type"]) == ["school", "hospital"]
        assert json.loads(row["survey"]) == {"people_trapped": "yes"}
        photo_gps = json.loads(row["photo_gps"])
        assert abs(photo_gps[0] - 51.51) < 1e-6
        assert abs(photo_gps[1] - 25.31) < 1e-6
        geocode = json.loads(row["ai_geocode"])
        assert abs(geocode["coordinates"][0] - 51.52) < 1e-6
        assert abs(geocode["coordinates"][1] - 25.32) < 1e-6
        assert geocode["source"] == "osm"
    finally:
        asyncio.run(_cleanup(settings.database_url, crisis_ids=[crisis_id]))


def test_csv_export_geometry_precedence_and_null() -> None:
    settings = get_settings()
    crisis_id = asyncio.run(
        _seed_crisis(settings.database_url, name=f"CSV geom {uuid.uuid4().hex[:8]}")
    )
    building_id = asyncio.run(_seed_building(settings.database_url, centroid=(25.30, 51.50)))
    centroid_report = asyncio.run(
        _seed_report(
            settings.database_url, crisis_id=crisis_id, location=None, building_id=building_id
        )
    )
    geocode_report = asyncio.run(
        _seed_report(settings.database_url, crisis_id=crisis_id, location=None)
    )
    asyncio.run(_seed_geocode(settings.database_url, report_id=geocode_report, lat=10.0, lon=20.0))
    null_report = asyncio.run(
        _seed_report(settings.database_url, crisis_id=crisis_id, location=None)
    )

    try:
        with TestClient(app) as client:
            response = client.get(f"/admin/crises/{crisis_id}/export.csv")
        _, rows = _parse_csv(response.content)

        centroid = _csv_row_by_id(rows, centroid_report)
        assert centroid["location_source"] == "building_centroid"
        assert abs(float(centroid["longitude"]) - 51.50) < 1e-3

        geocode = _csv_row_by_id(rows, geocode_report)
        assert geocode["location_source"] == "ai_geocode"
        assert abs(float(geocode["longitude"]) - 20.0) < 1e-6
        assert abs(float(geocode["latitude"]) - 10.0) < 1e-6

        # No locatable geometry -> empty lon/lat cells, empty location_source.
        unlocated = _csv_row_by_id(rows, null_report)
        assert unlocated["longitude"] == ""
        assert unlocated["latitude"] == ""
        assert unlocated["location_source"] == ""
    finally:
        asyncio.run(
            _cleanup(settings.database_url, crisis_ids=[crisis_id], building_ids=[building_id])
        )


def test_csv_export_respects_damage_class_filter() -> None:
    settings = get_settings()
    crisis_id = asyncio.run(
        _seed_crisis(settings.database_url, name=f"CSV filter {uuid.uuid4().hex[:8]}")
    )
    keep = asyncio.run(
        _seed_report(settings.database_url, crisis_id=crisis_id, damage_class="complete")
    )
    drop = asyncio.run(
        _seed_report(settings.database_url, crisis_id=crisis_id, damage_class="minimal")
    )

    try:
        with TestClient(app) as client:
            response = client.get(f"/admin/crises/{crisis_id}/export.csv?damage_class=complete")
        _, rows = _parse_csv(response.content)
        ids = {r["report_id"] for r in rows}
        assert str(keep) in ids
        assert str(drop) not in ids
        assert len(rows) == 1
    finally:
        asyncio.run(_cleanup(settings.database_url, crisis_ids=[crisis_id]))


def test_csv_export_empty_crisis_has_header_only() -> None:
    settings = get_settings()
    crisis_id = asyncio.run(
        _seed_crisis(settings.database_url, name=f"CSV empty {uuid.uuid4().hex[:8]}")
    )
    try:
        with TestClient(app) as client:
            response = client.get(f"/admin/crises/{crisis_id}/export.csv")
        header, rows = _parse_csv(response.content)
        assert header == _CSV_COLUMNS
        assert rows == []
    finally:
        asyncio.run(_cleanup(settings.database_url, crisis_ids=[crisis_id]))


def test_csv_export_returns_404_for_unknown_crisis() -> None:
    with TestClient(app) as client:
        response = client.get(f"/admin/crises/{uuid.uuid4()}/export.csv")
    assert response.status_code == 404
