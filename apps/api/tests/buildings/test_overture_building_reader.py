"""Unit tests for OvertureBuildingReader.

The reader streams rows from Overture's GeoParquet via DuckDB, filtered by a
crisis polygon, in batches of ~50,000. Tests substitute a fixture-parquet
reader (Protocol seam) so CI never hits live S3.

Schema asserted against:
- `source_id`: Overture's stable GERS id (Overture column: `id`)
- `footprint_wkb`: WKB MultiPolygon bytes
- `name`: optional human-readable name (Overture: `names.primary`)
- `building_class`: raw source value (Overture: `class`)
- `height_m`, `num_floors`: optional
- `properties`: JSON-serializable dict of source-specific extras
"""

from __future__ import annotations

from pathlib import Path

import duckdb
from shapely.geometry import MultiPolygon, Polygon

from api.buildings.overture_building_reader import (
    BuildingRow,
    BuildingsReader,
    DuckDBBuildingsReader,
)


def _write_buildings_fixture(tmp_path: Path) -> Path:
    """Write a tiny Parquet that mimics the columns OvertureBuildingReader
    reads from Overture's `theme=buildings` GeoParquet.

    Five rows inside (10..11, 10..11), five rows outside (20..21, 20..21).
    """
    parquet_path = tmp_path / "buildings.parquet"

    inside_polys = [
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
    outside_polys = [
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
        for i, poly in enumerate(inside_polys):
            con.execute(
                'insert into buildings values (?, st_geomfromwkb(?), {"primary": ?}, ?, ?, ?)',
                [f"in-{i}", poly.wkb, f"Building {i}", "residential", 12.5 + i, 3],
            )
        for i, poly in enumerate(outside_polys):
            con.execute(
                'insert into buildings values (?, st_geomfromwkb(?), {"primary": ?}, ?, ?, ?)',
                [f"out-{i}", poly.wkb, None, "commercial", None, None],
            )
        con.execute("copy buildings to ? (format parquet)", [str(parquet_path)])
    finally:
        con.close()
    return parquet_path


def test_reader_yields_only_rows_inside_polygon(tmp_path: Path) -> None:
    parquet_path = _write_buildings_fixture(tmp_path)
    reader = DuckDBBuildingsReader(parquet_url=str(parquet_path))

    aoi = Polygon(
        [
            (9.5, 9.5),
            (12.0, 9.5),
            (12.0, 12.0),
            (9.5, 12.0),
            (9.5, 9.5),
        ]
    )
    aoi_multi = MultiPolygon([aoi])

    batches = list(reader.read(polygon_wkb=aoi_multi.wkb, release="ignored"))

    assert len(batches) >= 1
    rows: list[BuildingRow] = [r for batch in batches for r in batch]
    assert len(rows) == 5
    assert {r.source_id for r in rows} == {f"in-{i}" for i in range(5)}


def test_reader_row_schema_matches_buildings_table_columns(tmp_path: Path) -> None:
    parquet_path = _write_buildings_fixture(tmp_path)
    reader = DuckDBBuildingsReader(parquet_url=str(parquet_path))

    aoi = MultiPolygon([Polygon([(9.5, 9.5), (12.0, 9.5), (12.0, 12.0), (9.5, 12.0), (9.5, 9.5)])])

    rows: list[BuildingRow] = [r for batch in reader.read(aoi.wkb, "ignored") for r in batch]
    assert rows  # sanity
    sample = next(r for r in rows if r.source_id == "in-0")

    assert sample.source == "overture"
    assert sample.source_id == "in-0"
    assert isinstance(sample.footprint_wkb, bytes)
    assert len(sample.footprint_wkb) > 0
    assert sample.name == "Building 0"
    assert sample.building_class == "residential"
    assert sample.height_m == 12.5
    assert sample.num_floors == 3
    assert isinstance(sample.properties, dict)


def test_reader_protocol_admits_fakes() -> None:
    """The Protocol seam admits hand-rolled fakes — no subclassing required."""
    rows = [
        BuildingRow(
            source="overture",
            source_id=f"fake-{i}",
            footprint_wkb=MultiPolygon([Polygon([(0, 0), (1, 0), (1, 1), (0, 1), (0, 0)])]).wkb,
            name=None,
            building_class=None,
            height_m=None,
            num_floors=None,
            properties={},
        )
        for i in range(3)
    ]

    class FakeReader:
        def read(self, polygon_wkb: bytes, release: str):  # type: ignore[no-untyped-def]
            yield rows

        def approx_count(self, polygon_wkb: bytes, release: str) -> int:
            return len(rows)

    fake: BuildingsReader = FakeReader()
    out = [r for batch in fake.read(b"", "x") for r in batch]
    assert len(out) == 3
