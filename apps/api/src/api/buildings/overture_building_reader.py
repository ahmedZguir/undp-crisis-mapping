"""Stream Overture buildings intersecting a polygon from GeoParquet via DuckDB."""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Any, Protocol, cast

import duckdb
from shapely import wkb as shapely_wkb

DEFAULT_BUILDINGS_URL_TEMPLATE = (
    "s3://overturemaps-us-west-2/release/{release}/theme=buildings/type=building/*"
)
DEFAULT_BATCH_SIZE = 50_000


@dataclass(frozen=True)
class BuildingRow:
    source: str
    source_id: str
    footprint_wkb: bytes
    name: str | None
    building_class: str | None
    height_m: float | None
    num_floors: int | None
    properties: dict[str, Any] = field(default_factory=lambda: cast(dict[str, Any], {}))


class BuildingsReader(Protocol):
    def read(self, polygon_wkb: bytes, release: str) -> Iterator[list[BuildingRow]]: ...

    def approx_count(self, polygon_wkb: bytes, release: str) -> int: ...


class DuckDBBuildingsReader:
    """parquet_url may contain a {release} placeholder or be a literal path."""

    def __init__(
        self,
        parquet_url: str = DEFAULT_BUILDINGS_URL_TEMPLATE,
        batch_size: int = DEFAULT_BATCH_SIZE,
    ) -> None:
        self._parquet_url_template = parquet_url
        self._batch_size = batch_size

    def _resolve_url(self, release: str) -> str:
        return (
            self._parquet_url_template.format(release=release)
            if "{release}" in self._parquet_url_template
            else self._parquet_url_template
        )

    def _connect(self) -> duckdb.DuckDBPyConnection:
        con = duckdb.connect(":memory:")
        con.execute("install spatial")
        con.execute("load spatial")
        try:
            con.execute("install httpfs")
            con.execute("load httpfs")
            # One stalled range request fails the whole read; the 30 s default
            # is too tight on slow links.
            con.execute("set http_timeout = 120000")
            con.execute("set http_retries = 5")
            con.execute("set http_retry_wait_ms = 2000")
            # Avoids a 301 redirect from us-east-1 on every range GET.
            con.execute("set s3_region = 'us-west-2'")
        except duckdb.Error:  # offline with local fixtures
            pass
        # Keep a runaway query from OOMing the worker container.
        con.execute("set memory_limit = '4GB'")
        return con

    @staticmethod
    def _columns(con: duckdb.DuckDBPyConnection, url: str) -> set[str]:
        """Available columns; fixture parquets omit many Overture columns."""
        return {
            row[0]
            for row in con.execute(
                "select column_name from (describe select * from read_parquet(?))",
                [url],
            ).fetchall()
        }

    @staticmethod
    def _bbox_clause(cols: set[str], polygon_wkb: bytes, params: list[Any]) -> str:
        """Append bbox-prefilter params and return the SQL fragment (or "").

        Row groups are pruned on the bbox columns but not on st_intersects;
        without this DuckDB scans the whole global parquet.
        """
        if "bbox" not in cols:
            return ""
        xmin, ymin, xmax, ymax = shapely_wkb.loads(polygon_wkb).bounds
        params.extend([xmax, xmin, ymax, ymin])
        return "bbox.xmin <= ? and bbox.xmax >= ? and bbox.ymin <= ? and bbox.ymax >= ? and "

    def approx_count(self, polygon_wkb: bytes, release: str) -> int:
        """Count of buildings whose bbox overlaps the AOI bbox; overcounts.

        Fixture parquets without bbox get an exact st_intersects count.
        """
        url = self._resolve_url(release)
        con = self._connect()
        try:
            cols = self._columns(con, url)
            params: list[Any] = [url]
            bbox_clause = self._bbox_clause(cols, polygon_wkb, params)
            if bbox_clause:
                query = f"select count(*) from read_parquet(?) where {bbox_clause[:-5]}"
            else:  # fixture-only path
                params.append(polygon_wkb)
                query = (
                    "select count(*) from read_parquet(?) "
                    "where st_intersects(geometry, st_geomfromwkb(?))"
                )
            row = con.execute(query, params).fetchone()
            return int(row[0]) if row and row[0] is not None else 0
        finally:
            con.close()

    def read(self, polygon_wkb: bytes, release: str) -> Iterator[list[BuildingRow]]:
        url = self._resolve_url(release)

        con = self._connect()
        try:
            cols = self._columns(con, url)

            select_id = "id" if "id" in cols else "cast(null as varchar)"
            select_geom = "st_aswkb(geometry)" if "geometry" in cols else "cast(null as blob)"
            select_name = (
                "names.primary"
                if "names" in cols
                else ("name" if "name" in cols else "cast(null as varchar)")
            )
            select_class = "class" if "class" in cols else "cast(null as varchar)"
            select_height = "height" if "height" in cols else "cast(null as double)"
            select_floors = "num_floors" if "num_floors" in cols else "cast(null as integer)"

            # Bbox prefilter for row-group pruning, then the exact polygon test.
            params: list[Any] = [url]
            bbox_clause = self._bbox_clause(cols, polygon_wkb, params)
            params.append(polygon_wkb)

            query = (
                f"select {select_id} as source_id, "
                f"       {select_geom} as wkb, "
                f"       {select_name} as name, "
                f"       {select_class} as building_class, "
                f"       {select_height} as height_m, "
                f"       {select_floors} as num_floors "
                f"from read_parquet(?) "
                f"where {bbox_clause}st_intersects(geometry, st_geomfromwkb(?))"
            )
            cur = con.execute(query, params)

            batch: list[BuildingRow] = []
            while True:
                rows = cur.fetchmany(self._batch_size)
                if not rows:
                    break
                for row in rows:
                    source_id, wkb_bytes, name, building_class, height_m, num_floors = row
                    if source_id is None or wkb_bytes is None:
                        continue
                    batch.append(
                        BuildingRow(
                            source="overture",
                            source_id=str(source_id),
                            footprint_wkb=bytes(wkb_bytes),
                            name=name if name else None,
                            building_class=building_class if building_class else None,
                            height_m=float(height_m) if height_m is not None else None,
                            num_floors=int(num_floors) if num_floors is not None else None,
                            properties={},
                        )
                    )
                    if len(batch) >= self._batch_size:
                        yield batch
                        batch = []
            if batch:
                yield batch
        finally:
            con.close()


__all__ = [
    "DEFAULT_BATCH_SIZE",
    "DEFAULT_BUILDINGS_URL_TEMPLATE",
    "BuildingRow",
    "BuildingsReader",
    "DuckDBBuildingsReader",
]
