"""End-to-end integration test for the building ingest job.

Runs the orchestration function directly (Arq jobs are plain async functions),
against a real DB, a fixture polygon (a small bbox), and a fixture GeoParquet
of buildings — no live S3. Pins:

- `public.buildings` rows are present after the run.
- `crises.overture_release_pinned` is set to the release the run used.
- `crises.buildings_ingested_at` is set.
- A second run does not duplicate rows (idempotent on `(source, source_id)`).
- The reserved `Other / Unspecified` crisis row (null geometry) skips cleanly:
  no rows inserted, no crash, the crisis fields stay null.
"""

from __future__ import annotations

import asyncio
import uuid
from pathlib import Path

import duckdb
import pytest
from shapely.geometry import MultiPolygon, Polygon
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from api.buildings.buildings_bulk_loader import BuildingsBulkLoader
from api.buildings.crisis_polygon_resolver import CrisisPolygonResolver
from api.buildings.ingest_job import (
    BuildingIngestJob,
    SkippedNoGeometryError,
)
from api.buildings.overture_building_reader import DuckDBBuildingsReader
from api.buildings.overture_release_locator import (
    OvertureReleaseLocator,
    OvertureTileReleaseLocator,
)
from api.buildings.pmtiles_extractor import PmtilesExtractor
from api.core.config import get_settings

pytestmark = pytest.mark.integration


def _write_buildings_fixture(tmp_path: Path) -> Path:
    """Five buildings inside (10..11, 10..11), five outside (20..21, 20..21)."""
    parquet_path = tmp_path / "fixture_buildings.parquet"

    inside = [
        Polygon(
            [
                (10.0 + 0.001 * i, 10.0 + 0.001 * i),
                (10.001 + 0.001 * i, 10.0 + 0.001 * i),
                (10.001 + 0.001 * i, 10.001 + 0.001 * i),
                (10.0 + 0.001 * i, 10.001 + 0.001 * i),
                (10.0 + 0.001 * i, 10.0 + 0.001 * i),
            ]
        )
        for i in range(5)
    ]
    outside = [
        Polygon(
            [
                (20.0 + 0.001 * i, 20.0 + 0.001 * i),
                (20.001 + 0.001 * i, 20.0 + 0.001 * i),
                (20.001 + 0.001 * i, 20.001 + 0.001 * i),
                (20.0 + 0.001 * i, 20.001 + 0.001 * i),
                (20.0 + 0.001 * i, 20.0 + 0.001 * i),
            ]
        )
        for i in range(5)
    ]
    con = duckdb.connect(":memory:")
    try:
        con.execute("install spatial")
        con.execute("load spatial")
        con.execute(
            "create table buildings ("
            "  id varchar,"
            "  geometry geometry,"
            '  names struct("primary" varchar),'
            "  class varchar,"
            "  height double,"
            "  num_floors integer"
            ")"
        )
        for i, poly in enumerate(inside):
            con.execute(
                'insert into buildings values (?, st_geomfromwkb(?), {"primary": ?}, ?, ?, ?)',
                [f"job-in-{i}", poly.wkb, f"Building {i}", "residential", 10.0 + i, 2],
            )
        for i, poly in enumerate(outside):
            con.execute(
                'insert into buildings values (?, st_geomfromwkb(?), {"primary": ?}, ?, ?, ?)',
                [f"job-out-{i}", poly.wkb, None, "commercial", None, None],
            )
        con.execute("copy buildings to ? (format parquet)", [str(parquet_path)])
    finally:
        con.close()
    return parquet_path


def _seed_crisis_with_geometry(name: str) -> uuid.UUID:
    settings = get_settings()
    crisis_id = uuid.uuid4()
    aoi = MultiPolygon([Polygon([(9.5, 9.5), (12.0, 9.5), (12.0, 12.0), (9.5, 12.0), (9.5, 9.5)])])

    async def _run() -> None:
        engine = create_async_engine(settings.database_url)
        try:
            async with engine.begin() as conn:
                await conn.execute(
                    text(
                        "insert into public.crises (id, name, status, geometry) "
                        "values (:id, :n, 'active', "
                        "  st_multi(st_geomfromwkb(:wkb, 4326))::geography)"
                    ),
                    {"id": str(crisis_id), "n": name, "wkb": aoi.wkb},
                )
        finally:
            await engine.dispose()

    asyncio.run(_run())
    return crisis_id


def _seed_crisis_without_geometry(name: str) -> uuid.UUID:
    settings = get_settings()
    crisis_id = uuid.uuid4()

    async def _run() -> None:
        engine = create_async_engine(settings.database_url)
        try:
            async with engine.begin() as conn:
                await conn.execute(
                    text("insert into public.crises (id, name, status) values (:id, :n, 'active')"),
                    {"id": str(crisis_id), "n": name},
                )
        finally:
            await engine.dispose()

    asyncio.run(_run())
    return crisis_id


def _seed_crisis_countries_only(name: str, countries: list[str]) -> uuid.UUID:
    settings = get_settings()
    crisis_id = uuid.uuid4()

    async def _run() -> None:
        engine = create_async_engine(settings.database_url)
        try:
            async with engine.begin() as conn:
                await conn.execute(
                    text(
                        "insert into public.crises (id, name, status, countries) "
                        "values (:id, :n, 'active', :c)"
                    ),
                    {"id": str(crisis_id), "n": name, "c": countries},
                )
        finally:
            await engine.dispose()

    asyncio.run(_run())
    return crisis_id


async def _read_crisis_geometry_present_async(crisis_id: uuid.UUID) -> bool:
    settings = get_settings()
    engine = create_async_engine(settings.database_url)
    try:
        async with engine.connect() as conn:
            row = (
                await conn.execute(
                    text(
                        "select geometry is not null as has_geom from public.crises where id = :id"
                    ),
                    {"id": str(crisis_id)},
                )
            ).one()
    finally:
        await engine.dispose()
    return bool(row.has_geom)


def _cleanup_buildings(prefix: str) -> None:
    settings = get_settings()

    async def _run() -> None:
        engine = create_async_engine(settings.database_url)
        try:
            async with engine.begin() as conn:
                await conn.execute(
                    text(
                        "delete from public.buildings "
                        "where source = 'overture' and source_id like :pat"
                    ),
                    {"pat": f"{prefix}%"},
                )
        finally:
            await engine.dispose()

    asyncio.run(_run())


def _delete_crisis(crisis_id: uuid.UUID) -> None:
    settings = get_settings()

    async def _run() -> None:
        engine = create_async_engine(settings.database_url)
        try:
            async with engine.begin() as conn:
                await conn.execute(
                    text("delete from public.crises where id = :id"),
                    {"id": str(crisis_id)},
                )
        finally:
            await engine.dispose()

    asyncio.run(_run())


async def _read_crisis_stamps_async(
    crisis_id: uuid.UUID,
) -> tuple[str | None, object | None]:
    settings = get_settings()
    engine = create_async_engine(settings.database_url)
    try:
        async with engine.connect() as conn:
            row = (
                await conn.execute(
                    text(
                        "select overture_release_pinned, buildings_ingested_at "
                        "from public.crises where id = :id"
                    ),
                    {"id": str(crisis_id)},
                )
            ).one()
    finally:
        await engine.dispose()
    return row.overture_release_pinned, row.buildings_ingested_at


def _read_crisis_stamps(crisis_id: uuid.UUID) -> tuple[str | None, object | None]:
    return asyncio.run(_read_crisis_stamps_async(crisis_id))


async def _read_buildings_ingested_count_async(crisis_id: uuid.UUID) -> int | None:
    settings = get_settings()
    engine = create_async_engine(settings.database_url)
    try:
        async with engine.connect() as conn:
            row = (
                await conn.execute(
                    text("select buildings_ingested_count from public.crises where id = :id"),
                    {"id": str(crisis_id)},
                )
            ).one()
    finally:
        await engine.dispose()
    return None if row.buildings_ingested_count is None else int(row.buildings_ingested_count)


async def _read_pmtiles_url_async(crisis_id: uuid.UUID) -> str | None:
    settings = get_settings()
    engine = create_async_engine(settings.database_url)
    try:
        async with engine.connect() as conn:
            row = (
                await conn.execute(
                    text("select pmtiles_url from public.crises where id = :id"),
                    {"id": str(crisis_id)},
                )
            ).one()
    finally:
        await engine.dispose()
    return row.pmtiles_url


class _FakeRunner:
    """Writes a deterministic byte sequence to the target path; never shells out."""

    def __init__(self) -> None:
        self.calls: list[list[str]] = []

    def run(self, argv: list[str]) -> tuple[int, str]:
        self.calls.append(argv)
        # The CLI normally writes to argv[3] (target_path). Mimic the side effect
        # so the extractor has bytes to upload.
        target = Path(argv[3])
        target.write_bytes(b"fake-pmtiles-bytes")
        return 0, ""


class _FakeUploader:
    def __init__(self) -> None:
        self.uploads: list[tuple[str, bytes]] = []

    def upload(self, object_name: str, content: bytes) -> str:
        self.uploads.append((object_name, content))
        return f"https://fake.bucket/crisis-pmtiles/{object_name}"


def test_ingest_job_inserts_rows_and_stamps_crisis_then_idempotent(tmp_path: Path) -> None:
    parquet_path = _write_buildings_fixture(tmp_path)
    settings = get_settings()
    name = f"Test ingest {uuid.uuid4().hex[:8]}"
    crisis_id = _seed_crisis_with_geometry(name)

    async def _run() -> tuple[int, str | None, object | None, int]:
        engine = create_async_engine(settings.database_url)
        sessionmaker = async_sessionmaker(engine, expire_on_commit=False)
        try:
            job = BuildingIngestJob(
                sessionmaker=sessionmaker,
                reader=DuckDBBuildingsReader(parquet_url=str(parquet_path)),
                bulk_loader=BuildingsBulkLoader(sessionmaker=sessionmaker),
                release_locator=OvertureReleaseLocator(listing=lambda: "fixture-2026-01-01.0"),
            )
            await job.run(crisis_id)

            async with engine.connect() as conn:
                count1 = (
                    await conn.execute(
                        text(
                            "select count(*) from public.buildings "
                            "where source = 'overture' and source_id like 'job-in-%'"
                        )
                    )
                ).scalar_one()

            release, ingested_at = await _read_crisis_stamps_async(crisis_id)

            # Second run — must not duplicate rows.
            await job.run(crisis_id)
            async with engine.connect() as conn:
                count2 = (
                    await conn.execute(
                        text(
                            "select count(*) from public.buildings "
                            "where source = 'overture' and source_id like 'job-in-%'"
                        )
                    )
                ).scalar_one()

            return int(count1), release, ingested_at, int(count2)
        finally:
            await engine.dispose()

    try:
        first_count, release, ingested_at, second_count = asyncio.run(_run())
        # Five inside-polygon rows from the fixture; outside rows excluded.
        assert first_count == 5
        assert release == "fixture-2026-01-01.0"
        assert ingested_at is not None
        # Idempotent: no duplicate rows after second run.
        assert second_count == 5
    finally:
        _cleanup_buildings("job-in-")
        _delete_crisis(crisis_id)


def test_ingest_job_stamps_buildings_ingested_count(tmp_path: Path) -> None:
    """`BuildingIngestJob` stamps `crises.buildings_ingested_count` with the
    number of rows upserted in the most recent run. The fixture yields five
    inside-polygon buildings; the count must match after a fresh run, and a
    second idempotent run must re-stamp the same value (not, e.g., double it)
    because the upserter returns rows-processed, not rows-newly-inserted.
    """
    parquet_path = _write_buildings_fixture(tmp_path)
    settings = get_settings()
    name = f"Test ingest count {uuid.uuid4().hex[:8]}"
    crisis_id = _seed_crisis_with_geometry(name)

    async def _run() -> tuple[int | None, int | None]:
        engine = create_async_engine(settings.database_url)
        sessionmaker = async_sessionmaker(engine, expire_on_commit=False)
        try:
            job = BuildingIngestJob(
                sessionmaker=sessionmaker,
                reader=DuckDBBuildingsReader(parquet_url=str(parquet_path)),
                bulk_loader=BuildingsBulkLoader(sessionmaker=sessionmaker),
                release_locator=OvertureReleaseLocator(listing=lambda: "fixture-count-1"),
            )
            await job.run(crisis_id)
            first = await _read_buildings_ingested_count_async(crisis_id)
            await job.run(crisis_id)
            second = await _read_buildings_ingested_count_async(crisis_id)
            return first, second
        finally:
            await engine.dispose()

    try:
        first, second = asyncio.run(_run())
        assert first == 5
        assert second == 5
    finally:
        _cleanup_buildings("job-in-")
        _delete_crisis(crisis_id)


def test_ingest_job_extracts_pmtiles_and_stamps_url(tmp_path: Path) -> None:
    """When a PmtilesExtractor is wired in, the job stamps `crises.pmtiles_url`.

    Uses fakes for the subprocess (writes deterministic bytes to the target
    path) and the uploader (returns a deterministic public URL). Asserts:

    - `pmtiles_url` is non-null after the run.
    - The bbox passed to the CLI matches the buffered crisis polygon's envelope.
    - The upload was invoked exactly once with the file's bytes.
    """
    parquet_path = _write_buildings_fixture(tmp_path)
    settings = get_settings()
    name = f"Test pmtiles ingest {uuid.uuid4().hex[:8]}"
    crisis_id = _seed_crisis_with_geometry(name)
    runner = _FakeRunner()
    uploader = _FakeUploader()
    target_path = tmp_path / "crisis.pmtiles"

    async def _run() -> str | None:
        engine = create_async_engine(settings.database_url)
        sessionmaker = async_sessionmaker(engine, expire_on_commit=False)
        try:
            extractor = PmtilesExtractor(
                runner=runner,
                uploader=uploader,
                tile_release_locator=OvertureTileReleaseLocator(
                    listing=lambda: "fixture-tile-2026-01-21",
                ),
            )
            job = BuildingIngestJob(
                sessionmaker=sessionmaker,
                reader=DuckDBBuildingsReader(parquet_url=str(parquet_path)),
                bulk_loader=BuildingsBulkLoader(sessionmaker=sessionmaker),
                release_locator=OvertureReleaseLocator(listing=lambda: "fixture-2026-02-02.0"),
                pmtiles_extractor=extractor,
                pmtiles_target_path=target_path,
            )
            await job.run(crisis_id)
            return await _read_pmtiles_url_async(crisis_id)
        finally:
            await engine.dispose()

    try:
        pmtiles_url = asyncio.run(_run())
        assert pmtiles_url is not None
        assert pmtiles_url.startswith("https://fake.bucket/crisis-pmtiles/")
        assert pmtiles_url.endswith(".pmtiles")

        # Subprocess invoked once with a bbox derived from the buffered polygon.
        assert len(runner.calls) == 1
        argv = runner.calls[0]
        assert argv[0:2] == ["pmtiles", "extract"]
        # The PMTiles URL is substituted with the *tile* release (which lags
        # the parquet data release on a separate cadence), not the data release
        # used by the parquet ingest.
        assert "fixture-tile-2026-01-21" in argv[2]
        bbox_flag = next(a for a in argv if a.startswith("--bbox="))
        # The seeded polygon is (9.5, 9.5, 12.0, 12.0); the 100m buffer expands
        # it slightly. Just check the envelope is in the right neighborhood.
        bbox = [float(x) for x in bbox_flag.removeprefix("--bbox=").split(",")]
        xmin, ymin, xmax, ymax = bbox
        assert xmin < 9.5 and ymin < 9.5
        assert xmax > 12.0 and ymax > 12.0

        # Upload happened exactly once with the bytes the fake runner wrote.
        assert len(uploader.uploads) == 1
        _, content = uploader.uploads[0]
        assert content == b"fake-pmtiles-bytes"
    finally:
        _cleanup_buildings("job-in-")
        _delete_crisis(crisis_id)


class _CancellingPmtilesExtractor:
    """Stand-in for `PmtilesExtractor` whose `extract` raises a non-swallowed error.

    The PMTiles failure path inside `_maybe_extract_pmtiles` only catches
    `PmtilesExtractorError`. Anything else (e.g. `asyncio.CancelledError` at
    the Arq timeout boundary, an unexpected runtime error from the Go binary
    wrapper) propagates. The split-stamp contract is what protects the row
    stamps in that case: they are written *before* the extract runs.
    """

    def extract(self, *, polygon_wkb: bytes, target_path: Path) -> tuple[str, str]:
        _ = polygon_wkb, target_path
        raise RuntimeError("synthetic mid-bake cancellation")


def test_ingest_job_pmtiles_failure_leaves_row_stamps(tmp_path: Path) -> None:
    """Split-stamp contract: an exception from extract must not wipe row stamps.

    The bake runs after the row stamps land. When the extract raises (here,
    a non-swallowed `RuntimeError` standing in for cancellation), the
    exception propagates out of `run()`, but `overture_release_pinned` and
    `buildings_ingested_at` are already written and `pmtiles_url` stays NULL.
    """
    parquet_path = _write_buildings_fixture(tmp_path)
    settings = get_settings()
    name = f"Test pmtiles-fail {uuid.uuid4().hex[:8]}"
    crisis_id = _seed_crisis_with_geometry(name)

    async def _run() -> tuple[int, str | None, object | None, str | None]:
        engine = create_async_engine(settings.database_url)
        sessionmaker = async_sessionmaker(engine, expire_on_commit=False)
        try:
            job = BuildingIngestJob(
                sessionmaker=sessionmaker,
                reader=DuckDBBuildingsReader(parquet_url=str(parquet_path)),
                bulk_loader=BuildingsBulkLoader(sessionmaker=sessionmaker),
                release_locator=OvertureReleaseLocator(listing=lambda: "fixture-2026-03-03.0"),
                pmtiles_extractor=_CancellingPmtilesExtractor(),  # type: ignore[arg-type]
                pmtiles_target_path=tmp_path / "crisis.pmtiles",
            )
            with pytest.raises(RuntimeError, match="synthetic mid-bake cancellation"):
                await job.run(crisis_id)

            async with engine.connect() as conn:
                count = (
                    await conn.execute(
                        text(
                            "select count(*) from public.buildings "
                            "where source = 'overture' and source_id like 'job-in-%'"
                        )
                    )
                ).scalar_one()
            release, ingested_at = await _read_crisis_stamps_async(crisis_id)
            pmtiles_url = await _read_pmtiles_url_async(crisis_id)
            return int(count), release, ingested_at, pmtiles_url
        finally:
            await engine.dispose()

    try:
        count, release, ingested_at, pmtiles_url = asyncio.run(_run())
        assert count == 5
        assert release == "fixture-2026-03-03.0"
        assert ingested_at is not None
        assert pmtiles_url is None
    finally:
        _cleanup_buildings("job-in-")
        _delete_crisis(crisis_id)


def test_ingest_job_skips_crisis_without_geometry(tmp_path: Path) -> None:
    """Reserved `Other / Unspecified` row has null geometry — must skip cleanly."""
    parquet_path = _write_buildings_fixture(tmp_path)
    settings = get_settings()
    name = f"Test reserved-shaped {uuid.uuid4().hex[:8]}"
    crisis_id = _seed_crisis_without_geometry(name)

    async def _run() -> None:
        engine = create_async_engine(settings.database_url)
        sessionmaker = async_sessionmaker(engine, expire_on_commit=False)
        try:
            job = BuildingIngestJob(
                sessionmaker=sessionmaker,
                reader=DuckDBBuildingsReader(parquet_url=str(parquet_path)),
                bulk_loader=BuildingsBulkLoader(sessionmaker=sessionmaker),
                release_locator=OvertureReleaseLocator(listing=lambda: "fixture-x"),
            )
            with pytest.raises(SkippedNoGeometryError):
                await job.run(crisis_id)
        finally:
            await engine.dispose()

    try:
        asyncio.run(_run())
        # Crisis stamps stay null after the no-op.
        release, ingested_at = _read_crisis_stamps(crisis_id)
        assert release is None
        assert ingested_at is None
    finally:
        _delete_crisis(crisis_id)


def test_ingest_job_resolves_geometry_from_countries_when_geometry_is_null(
    tmp_path: Path,
) -> None:
    """When `geometry` is null but `countries` is set and a polygon resolver is
    wired in, the job resolves the unioned country polygon, stamps
    `crises.geometry`, and runs the ingest against it.

    The polygon-union itself is implemented by `read_countries_union` (slice
    #4); this test exercises the full ingest-fallback path against a fake
    reader that returns the pre-built union WKB.
    """
    parquet_path = _write_buildings_fixture(tmp_path)
    settings = get_settings()
    name = f"Test countries-only {uuid.uuid4().hex[:8]}"
    crisis_id = _seed_crisis_countries_only(name, ["QA"])

    # Fake reader returns a MultiPolygon that covers the inside-fixture buildings
    # so the ingest finds rows after the stamp.
    aoi = MultiPolygon([Polygon([(9.5, 9.5), (12.0, 9.5), (12.0, 12.0), (9.5, 12.0), (9.5, 9.5)])])

    from api.areas import Area, Country, ResolvedArea

    class _FakeOvertureReader:
        """Minimal stand-in for `OvertureDivisionsReader` — only the union
        method is exercised by the ingest's countries fallback."""

        def __init__(self) -> None:
            self.union_calls: list[list[str]] = []

        async def search(self, q: str, country: str | None, limit: int) -> list[Area]:
            raise AssertionError("search must not be called from ingest")

        async def list_countries(self) -> list[Country]:
            raise AssertionError("list_countries must not be called from ingest")

        async def read_area_geometry(self, area_id: str) -> ResolvedArea:
            raise AssertionError("read_area_geometry must not be called from ingest")

        async def read_area_geojson(self, area_id: str) -> dict[str, object]:
            raise AssertionError("read_area_geojson must not be called from ingest")

        async def read_countries_union(self, iso2_codes: list[str]) -> bytes | None:
            self.union_calls.append(list(iso2_codes))
            return aoi.wkb

        async def read_countries_geojson_union(
            self, iso2_codes: list[str]
        ) -> dict[str, object] | None:
            raise AssertionError("read_countries_geojson_union must not be called from ingest")

    async def _run() -> tuple[int, bool, _FakeOvertureReader]:
        engine = create_async_engine(settings.database_url)
        sessionmaker = async_sessionmaker(engine, expire_on_commit=False)
        try:
            reader = _FakeOvertureReader()
            job = BuildingIngestJob(
                sessionmaker=sessionmaker,
                reader=DuckDBBuildingsReader(parquet_url=str(parquet_path)),
                bulk_loader=BuildingsBulkLoader(sessionmaker=sessionmaker),
                release_locator=OvertureReleaseLocator(listing=lambda: "fixture-cn-1"),
                polygon_resolver=CrisisPolygonResolver(reader=reader),
            )
            await job.run(crisis_id)

            async with engine.connect() as conn:
                count = (
                    await conn.execute(
                        text(
                            "select count(*) from public.buildings "
                            "where source = 'overture' and source_id like 'job-in-%'"
                        )
                    )
                ).scalar_one()
            has_geom = await _read_crisis_geometry_present_async(crisis_id)
            return int(count), has_geom, reader
        finally:
            await engine.dispose()

    try:
        count, has_geom, reader = asyncio.run(_run())
        # Union resolver was consulted exactly once for the single country.
        assert reader.union_calls == [["QA"]]
        # Geometry was stamped onto the crisis row.
        assert has_geom is True
        # Buildings inside the stamped polygon were ingested.
        assert count == 5
        # Standard ingest stamps still applied.
        release, ingested_at = _read_crisis_stamps(crisis_id)
        assert release == "fixture-cn-1"
        assert ingested_at is not None
    finally:
        _cleanup_buildings("job-in-")
        _delete_crisis(crisis_id)


def test_ingest_job_skips_when_no_geometry_and_no_resolver(tmp_path: Path) -> None:
    """A countries-only crisis with no resolver wired in still fails clean —
    `SkippedNoGeometryError` rather than a crash."""
    parquet_path = _write_buildings_fixture(tmp_path)
    settings = get_settings()
    name = f"Test countries-no-resolver {uuid.uuid4().hex[:8]}"
    crisis_id = _seed_crisis_countries_only(name, ["QA"])

    async def _run() -> None:
        engine = create_async_engine(settings.database_url)
        sessionmaker = async_sessionmaker(engine, expire_on_commit=False)
        try:
            job = BuildingIngestJob(
                sessionmaker=sessionmaker,
                reader=DuckDBBuildingsReader(parquet_url=str(parquet_path)),
                bulk_loader=BuildingsBulkLoader(sessionmaker=sessionmaker),
                release_locator=OvertureReleaseLocator(listing=lambda: "fixture-x"),
                # polygon_resolver intentionally omitted.
            )
            with pytest.raises(SkippedNoGeometryError):
                await job.run(crisis_id)
        finally:
            await engine.dispose()

    try:
        asyncio.run(_run())

        # Geometry stays null and stamps stay null when the fallback is unavailable.
        async def _check() -> tuple[bool, str | None]:
            release, _ingested = await _read_crisis_stamps_async(crisis_id)
            has_geom = await _read_crisis_geometry_present_async(crisis_id)
            return has_geom, release

        has_geom, release = asyncio.run(_check())
        assert has_geom is False
        assert release is None
    finally:
        _delete_crisis(crisis_id)


# --- worker terminal-write tests ---------------------------------------------


def _read_crisis_job_row(crisis_id: uuid.UUID) -> dict[str, object] | None:
    settings = get_settings()

    async def _run() -> dict[str, object] | None:
        engine = create_async_engine(settings.database_url)
        try:
            async with engine.connect() as conn:
                row = (
                    await conn.execute(
                        text(
                            "select status, error, ended_at "
                            "from public.crisis_jobs "
                            "where crisis_id = :id and job_type = 'ingest_buildings'"
                        ),
                        {"id": str(crisis_id)},
                    )
                ).first()
        finally:
            await engine.dispose()
        if row is None:
            return None
        return {"status": row.status, "error": row.error, "ended_at": row.ended_at}

    return asyncio.run(_run())


def test_worker_terminal_write_marks_succeeded_on_success(tmp_path: Path) -> None:
    """After a successful job run, the wrapper writes status=succeeded."""
    from api.workers.buildings import run_ingest_buildings_with_terminal_write

    parquet_path = _write_buildings_fixture(tmp_path)
    settings = get_settings()
    name = f"Worker success {uuid.uuid4().hex[:8]}"
    crisis_id = _seed_crisis_with_geometry(name)

    async def _run() -> None:
        engine = create_async_engine(settings.database_url)
        sessionmaker = async_sessionmaker(engine, expire_on_commit=False)
        try:
            job = BuildingIngestJob(
                sessionmaker=sessionmaker,
                reader=DuckDBBuildingsReader(parquet_url=str(parquet_path)),
                bulk_loader=BuildingsBulkLoader(sessionmaker=sessionmaker),
                release_locator=OvertureReleaseLocator(listing=lambda: "fixture-x"),
            )
            await run_ingest_buildings_with_terminal_write(crisis_id, job, sessionmaker)
        finally:
            await engine.dispose()

    try:
        asyncio.run(_run())
        row = _read_crisis_job_row(crisis_id)
        assert row is not None
        assert row["status"] == "succeeded"
        assert row["error"] is None
        assert row["ended_at"] is not None
    finally:
        _cleanup_buildings("job-in-")
        _delete_crisis(crisis_id)


class _ExplodingReader:
    def read(self, polygon_wkb: bytes, release: str):  # type: ignore[no-untyped-def]
        _ = polygon_wkb, release
        raise RuntimeError("kaboom: synthetic reader failure")
        yield  # pragma: no cover


def test_worker_terminal_write_marks_failed_on_exception_and_reraises(
    tmp_path: Path,
) -> None:
    from api.workers.buildings import run_ingest_buildings_with_terminal_write

    _ = tmp_path  # not used
    settings = get_settings()
    name = f"Worker failure {uuid.uuid4().hex[:8]}"
    crisis_id = _seed_crisis_with_geometry(name)

    async def _run() -> None:
        engine = create_async_engine(settings.database_url)
        sessionmaker = async_sessionmaker(engine, expire_on_commit=False)
        try:
            job = BuildingIngestJob(
                sessionmaker=sessionmaker,
                reader=_ExplodingReader(),  # type: ignore[arg-type]
                bulk_loader=BuildingsBulkLoader(sessionmaker=sessionmaker),
                release_locator=OvertureReleaseLocator(listing=lambda: "fixture-x"),
            )
            with pytest.raises(RuntimeError):
                await run_ingest_buildings_with_terminal_write(crisis_id, job, sessionmaker)
        finally:
            await engine.dispose()

    try:
        asyncio.run(_run())
        row = _read_crisis_job_row(crisis_id)
        assert row is not None
        assert row["status"] == "failed"
        assert row["error"] is not None
        assert "kaboom" in str(row["error"])
        assert len(str(row["error"])) <= 500
        assert row["ended_at"] is not None
    finally:
        _delete_crisis(crisis_id)


class _CancellingReader:
    def read(self, polygon_wkb: bytes, release: str):  # type: ignore[no-untyped-def]
        _ = polygon_wkb, release
        raise asyncio.CancelledError
        yield  # pragma: no cover


def test_worker_terminal_write_marks_failed_on_cancellation_and_reraises(
    tmp_path: Path,
) -> None:
    """The `job_timeout` ceiling cancels via `asyncio.CancelledError`, a
    `BaseException`. The wrapper must record `status='failed'` and re-raise
    the cancellation rather than letting it slip past and wedge the row at
    `running`. Regression guard for the `except BaseException` widening."""
    from api.workers.buildings import run_ingest_buildings_with_terminal_write

    _ = tmp_path  # not used
    settings = get_settings()
    name = f"Worker cancel {uuid.uuid4().hex[:8]}"
    crisis_id = _seed_crisis_with_geometry(name)

    async def _run() -> None:
        engine = create_async_engine(settings.database_url)
        sessionmaker = async_sessionmaker(engine, expire_on_commit=False)
        try:
            job = BuildingIngestJob(
                sessionmaker=sessionmaker,
                reader=_CancellingReader(),  # type: ignore[arg-type]
                bulk_loader=BuildingsBulkLoader(sessionmaker=sessionmaker),
                release_locator=OvertureReleaseLocator(listing=lambda: "fixture-x"),
            )
            with pytest.raises(asyncio.CancelledError):
                await run_ingest_buildings_with_terminal_write(crisis_id, job, sessionmaker)
        finally:
            await engine.dispose()

    try:
        asyncio.run(_run())
        row = _read_crisis_job_row(crisis_id)
        assert row is not None
        assert row["status"] == "failed"
        assert row["error"] is not None
        assert "CancelledError" in str(row["error"])
        assert row["ended_at"] is not None
    finally:
        _delete_crisis(crisis_id)
