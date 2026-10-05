"""Build public.building_density_grid as a CSV from Overture's bbox column.

The runtime role cannot write the grid table, so a superuser loads the CSV
(the script prints the command). Run once per Overture release.

Usage (from apps/api/):
    uv run python -m api.scripts.build_density_grid [--release 2025-05-21.0] [--out /tmp/grid.csv]
"""

from __future__ import annotations

import argparse
import time
from pathlib import Path

import duckdb

from api.buildings.density_grid import DENSITY_GRID_STEP_DEG
from api.buildings.overture_building_reader import (
    DEFAULT_BUILDINGS_URL_TEMPLATE,
)
from api.buildings.overture_release_locator import OvertureReleaseLocator


def _connect() -> duckdb.DuckDBPyConnection:
    """DuckDB tuned for a minutes-long whole-globe read."""
    con = duckdb.connect(":memory:")
    con.execute("install spatial")
    con.execute("load spatial")
    con.execute("install httpfs")
    con.execute("load httpfs")
    con.execute("set http_timeout = 300000")
    con.execute("set http_retries = 8")
    con.execute("set http_retry_wait_ms = 2000")
    con.execute("set s3_region = 'us-west-2'")
    con.execute("set memory_limit = '6GB'")
    # Lowers aggregate memory; output order does not matter.
    con.execute("set preserve_insertion_order = false")
    con.execute("pragma enable_progress_bar")
    return con


def build(release: str | None, out: Path, url_template: str) -> None:
    release = release or OvertureReleaseLocator().latest()
    url = url_template.format(release=release)
    step = DENSITY_GRID_STEP_DEG

    print(f"[build] release   = {release}", flush=True)
    print(f"[build] url        = {url}", flush=True)
    print(f"[build] step       = {step} deg", flush=True)
    print(f"[build] out        = {out}", flush=True)
    print("[build] scanning bbox column over S3 (this is the slow part)…", flush=True)

    con = _connect()
    t0 = time.time()
    # Only bbox is read, so the geometry column is pruned. Buildings are counted
    # in the cell of their bbox centre.
    con.execute(
        f"""
        copy (
            select
                '{release}' as release,
                cast(floor(((bbox.xmin + bbox.xmax) / 2.0) / {step}) as integer) as ix,
                cast(floor(((bbox.ymin + bbox.ymax) / 2.0) / {step}) as integer) as iy,
                count(*) as n
            from read_parquet('{url}')
            group by 2, 3
        ) to '{out}' (format csv, header false)
        """
    )
    secs = time.time() - t0

    summary = con.execute(
        f"select count(*), sum(column3) from read_csv_auto('{out}', header=false)"
    ).fetchone()
    con.close()
    cells = int(summary[0]) if summary else 0
    total = int(summary[1]) if summary and summary[1] is not None else 0

    print(f"\n[build] done in {secs / 60:.1f} min", flush=True)
    print(f"[build] {cells:,} cells, {int(total):,} buildings total", flush=True)
    print(f"[build] CSV: {out}\n", flush=True)
    print("Load it as a DB superuser (scoped to this release, idempotent):", flush=True)
    print(
        "  PGPASSWORD=<pw> psql -h 127.0.0.1 -p 54322 -U postgres -d postgres \\\n"
        f"    -c \"delete from public.building_density_grid where release = '{release}';\" \\\n"
        f"    -c \"\\copy public.building_density_grid(release,ix,iy,n) from '{out}' csv\"",
        flush=True,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Build the building-density grid CSV.")
    parser.add_argument(
        "--release",
        default=None,
        help="Overture data release (e.g. 2025-05-21.0). Defaults to the latest.",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=Path("/tmp/building_density_grid.csv"),
        help="CSV output path (default: /tmp/building_density_grid.csv).",
    )
    parser.add_argument(
        "--url-template",
        default=DEFAULT_BUILDINGS_URL_TEMPLATE,
        help="Override the parquet URL template (for fixtures/testing).",
    )
    args = parser.parse_args()
    build(args.release, args.out, args.url_template)


if __name__ == "__main__":
    main()
