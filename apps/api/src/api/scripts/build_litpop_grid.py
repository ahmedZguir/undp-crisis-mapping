# pandas is largely untyped, so strict mode only adds noise.
# pyright: basic
"""Build public.litpop_value as a CSV from CLIMADA's precomputed LitPop exposure.

Each 150-arcsec LitPop cell's USD is split onto the 0.02 degree density grid,
weighted by building count times area overlap, so per-building value lines up
with the density grid. Totals are optionally scaled by GDP(latest)/GDP(2018).
A superuser loads the CSV (the script prints the commands).

Usage (from apps/api/):
    uv run python -m api.scripts.build_litpop_grid QAT BHR KWT [--no-gdp-scale]
    uv run python -m api.scripts.build_litpop_grid --all
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import math
import re
from dataclasses import dataclass
from pathlib import Path
from typing import cast

import asyncpg
import httpx
import numpy as np
import pandas as pd

from api.analysis.constants import LITPOP_GRID_STEP_DEG
from api.core.config import get_settings

CLIMADA_API = "https://climada.ethz.ch/data-api/v1/dataset/"
WORLD_BANK_GDP = "https://api.worldbank.org/v2/country/{iso3}/indicator/NY.GDP.MKTP.CD"

LITPOP_NATIVE_STEP_DEG = 150.0 / 3600.0


@dataclass(frozen=True)
class LitPopProduct:
    iso3: str
    version: str  # e.g. 'v3'
    reference_year: int  # e.g. 2018
    file_url: str
    file_name: str
    md5: str


def lookup_product(client: httpx.Client, iso3: str) -> LitPopProduct:
    """Resolve the active precomputed LitPop dataset for a country."""
    resp = client.get(
        CLIMADA_API,
        params={"data_type": "litpop", "name": f"LitPop_150arcsec_{iso3}", "status": "active"},
    )
    resp.raise_for_status()
    datasets = resp.json()
    if not datasets:
        raise LookupError(f"no active LitPop_150arcsec_{iso3} dataset on the CLIMADA API")
    ds = datasets[0]
    (file,) = ds["files"]
    m = re.search(r"reference_year': '(\d{4})", ds.get("description") or "")
    return LitPopProduct(
        iso3=iso3,
        version=ds["version"],
        reference_year=int(m.group(1)) if m else 2018,
        file_url=file["url"],
        file_name=file["file_name"],
        md5=file["check_sum"].removeprefix("md5:"),
    )


def list_all_iso3(client: httpx.Client) -> list[str]:
    """Every country with an active default LitPop_150arcsec dataset."""
    limit = 10_000
    resp = client.get(
        CLIMADA_API, params={"data_type": "litpop", "status": "active", "limit": limit}
    )
    resp.raise_for_status()
    page = resp.json()
    if len(page) >= limit:
        raise RuntimeError("litpop dataset listing hit the page limit; raise it")
    iso3s = {
        m.group(1) for ds in page if (m := re.fullmatch(r"LitPop_150arcsec_([A-Z]{3})", ds["name"]))
    }
    return sorted(iso3s)


def download(client: httpx.Client, product: LitPopProduct, cache_dir: Path) -> Path:
    cache_dir.mkdir(parents=True, exist_ok=True)
    path = cache_dir / product.file_name
    if path.exists() and hashlib.md5(path.read_bytes()).hexdigest() == product.md5:
        print(f"[{product.iso3}] cached    {path.name}", flush=True)
        return path
    resp = client.get(product.file_url)
    resp.raise_for_status()
    digest = hashlib.md5(resp.content).hexdigest()
    if digest != product.md5:
        raise RuntimeError(f"{product.file_name}: md5 {digest} != expected {product.md5}")
    path.write_bytes(resp.content)
    print(f"[{product.iso3}] downloaded {path.name} ({len(resp.content):,} B)", flush=True)
    return path


def gdp_scale(client: httpx.Client, iso3: str, ref_year: int) -> tuple[float, int]:
    """GDP(latest)/GDP(ref_year) from the World Bank; (1.0, ref_year) on any failure."""
    try:
        resp = client.get(
            WORLD_BANK_GDP.format(iso3=iso3), params={"format": "json", "per_page": 100}
        )
        resp.raise_for_status()
        body = resp.json()
    except Exception as exc:
        print(f"[{iso3}] WARN: World Bank GDP lookup failed ({exc}); not scaling", flush=True)
        return 1.0, ref_year
    rows = body[1] if len(body) > 1 and body[1] else []
    by_year = {int(r["date"]): float(r["value"]) for r in rows if r["value"] is not None}
    if ref_year not in by_year or not by_year:
        print(f"[{iso3}] WARN: no World Bank GDP for {ref_year}; not scaling", flush=True)
        return 1.0, ref_year
    latest = max(by_year)
    return by_year[latest] / by_year[ref_year], latest


def candidate_cells(df: pd.DataFrame) -> tuple[list[int], list[int]]:
    """Every 0.02° cell overlapped by a native LitPop cell, as parallel lists.

    Using the country bbox instead would pull whole latitude bands for
    countries crossing the antimeridian.
    """
    step = LITPOP_GRID_STEP_DEG
    half = LITPOP_NATIVE_STEP_DEG / 2.0
    eps = 1e-12
    lon = df["longitude"].to_numpy()
    lat = df["latitude"].to_numpy()
    ix_lo = np.floor((lon - half) / step).astype(np.int64)
    ix_hi = np.floor((lon + half - eps) / step).astype(np.int64)
    iy_lo = np.floor((lat - half) / step).astype(np.int64)
    iy_hi = np.floor((lat + half - eps) / step).astype(np.int64)
    pairs: list[np.ndarray] = []
    for dx in range(int((ix_hi - ix_lo).max()) + 1):
        for dy in range(int((iy_hi - iy_lo).max()) + 1):
            m = (ix_lo + dx <= ix_hi) & (iy_lo + dy <= iy_hi)
            if m.any():
                pairs.append(np.stack([ix_lo[m] + dx, iy_lo[m] + dy], axis=1))
    uniq = np.unique(np.concatenate(pairs), axis=0)
    return uniq[:, 0].tolist(), uniq[:, 1].tolist()


async def fetch_density(dsn: str, ixs: list[int], iys: list[int]) -> dict[tuple[int, int], int]:
    """Building counts for exactly the given 0.02° cells, latest grid release."""
    chunk = 200_000  # bounds both the bind-array size and asyncpg result memory
    conn = await asyncpg.connect(dsn)
    try:
        release = await conn.fetchval("select max(release) from public.building_density_grid")
        if release is None:
            raise RuntimeError("building_density_grid is empty; build it first")
        out: dict[tuple[int, int], int] = {}
        for i in range(0, len(ixs), chunk):
            rows = await conn.fetch(
                """
                select g.ix, g.iy, g.n
                from public.building_density_grid g
                join unnest($2::int[], $3::int[]) as t(ix, iy)
                  on g.ix = t.ix and g.iy = t.iy
                where g.release = $1
                """,
                release,
                ixs[i : i + chunk],
                iys[i : i + chunk],
            )
            out.update({(r["ix"], r["iy"]): r["n"] for r in rows})
    finally:
        await conn.close()
    return out


def rebin(
    df: pd.DataFrame, scale: float, density: dict[tuple[int, int], int]
) -> dict[tuple[int, int], float]:
    """Split each native LitPop cell's USD across overlapping 0.02° cells.

    Weight is building count x area overlap. Conserves the scaled country total.
    """
    step = LITPOP_GRID_STEP_DEG
    half = LITPOP_NATIVE_STEP_DEG / 2.0
    eps = 1e-12  # native-cell edges can land exactly on 0.02° boundaries
    out: dict[tuple[int, int], float] = {}
    for lon, lat, value in zip(
        df["longitude"].to_numpy(),
        df["latitude"].to_numpy(),
        df["value"].to_numpy() * scale,
        strict=True,
    ):
        x0, x1, y0, y1 = lon - half, lon + half, lat - half, lat + half
        cells: list[tuple[tuple[int, int], float]] = []  # ((ix, iy), overlap area)
        for ix in range(math.floor(x0 / step), math.floor((x1 - eps) / step) + 1):
            wx = min((ix + 1) * step, x1) - max(ix * step, x0)
            for iy in range(math.floor(y0 / step), math.floor((y1 - eps) / step) + 1):
                wy = min((iy + 1) * step, y1) - max(iy * step, y0)
                cells.append(((ix, iy), wx * wy))
        weights = [density.get(c, 0) * a for c, a in cells]
        if not any(w > 0 for w in weights):
            # No buildings: put the value in the max-overlap cell to avoid
            # many near-zero rows.
            cell = max(cells, key=lambda ca: ca[1])[0]
            out[cell] = out.get(cell, 0.0) + value
            continue
        total = sum(weights)
        for (cell, _), w in zip(cells, weights, strict=True):
            if w > 0:
                out[cell] = out.get(cell, 0.0) + value * (w / total)
    return out


async def build_country(
    client: httpx.Client, iso3: str, *, dsn: str, cache_dir: Path, scale_gdp: bool
) -> tuple[str, dict[tuple[int, int], float]]:
    product = lookup_product(client, iso3)
    path = download(client, product, cache_dir)
    df = pd.read_hdf(path)
    assert isinstance(df, pd.DataFrame)
    # Zero-value cells (unlit, unpopulated) carry nothing.
    df = cast(pd.DataFrame, df[df["value"] > 0])
    if df.empty:
        print(f"[{iso3}] no positive-value cells; skipping", flush=True)
        return f"litpop_{product.version}_empty_{iso3}", {}

    if scale_gdp:
        scale, gdp_year = gdp_scale(client, iso3, product.reference_year)
    else:
        scale, gdp_year = 1.0, product.reference_year
    tag = f"litpop_{product.version}_gdp{gdp_year}_{iso3}"

    ixs, iys = candidate_cells(df)
    density = await fetch_density(dsn, ixs, iys)
    grid = rebin(df, scale, density)

    expected = float(cast(float, df["value"].sum())) * scale
    got = sum(grid.values())
    if not math.isclose(got, expected, rel_tol=1e-9):
        raise AssertionError(f"{iso3}: conservation broken: {got:.6e} != {expected:.6e}")
    print(
        f"[{iso3}] {len(df):,} native cells -> {len(grid):,} grid cells | "
        f"scale x{scale:.3f} (GDP {product.reference_year}->{gdp_year}) | "
        f"total ${got:.4e} | dataset {tag}",
        flush=True,
    )
    return tag, grid


async def main_async(args: argparse.Namespace) -> None:
    with httpx.Client(timeout=httpx.Timeout(300.0, connect=30.0), follow_redirects=True) as client:
        iso3s: list[str] = list_all_iso3(client) if args.all else args.iso3
        if not iso3s:
            raise SystemExit("no countries given (pass ISO3 codes or --all)")
        print(f"[build] countries  = {len(iso3s)}", flush=True)
        print(f"[build] grid step  = {LITPOP_GRID_STEP_DEG} deg", flush=True)
        print(f"[build] out        = {args.out}", flush=True)

        tags: list[str] = []
        failures: list[str] = []
        with args.out.open("w") as fh:
            for iso3 in iso3s:
                try:
                    tag, grid = await build_country(
                        client,
                        iso3,
                        dsn=args.dsn,
                        cache_dir=args.cache_dir,
                        scale_gdp=not args.no_gdp_scale,
                    )
                except Exception as exc:  # keep a world run going
                    print(f"[{iso3}] FAILED: {exc}", flush=True)
                    failures.append(iso3)
                    continue
                for (ix, iy), usd in sorted(grid.items()):
                    fh.write(f"{tag},{ix},{iy},{usd}\n")
                tags.append(tag)

    if failures:
        print(f"\n[build] FAILED countries ({len(failures)}): {' '.join(failures)}", flush=True)
    print(f"\n[build] CSV: {args.out}\n", flush=True)
    print("Load it as a DB superuser (idempotent per country):", flush=True)
    # Delete by ISO3 suffix: lookups sum all rows spatially, so an older
    # differently-tagged load would double-count.
    if args.all:
        deletes = '    -c "delete from public.litpop_value;"'
    else:
        deletes = " \\\n".join(
            '    -c "delete from public.litpop_value '
            f"where dataset like '%\\_{tag.rsplit('_', 1)[1]}';\""
            for tag in tags
        )
    print(
        "  PGPASSWORD=<pw> psql -h 127.0.0.1 -p 54322 -U postgres -d postgres \\\n"
        f"{deletes} \\\n"
        f"    -c \"\\copy public.litpop_value(dataset,ix,iy,usd) from '{args.out}' csv\"",
        flush=True,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Build the LitPop asset-value grid CSV.")
    parser.add_argument("iso3", nargs="*", help="ISO3 country codes, e.g. QAT BHR KWT.")
    parser.add_argument("--all", action="store_true", help="All countries on the CLIMADA API.")
    parser.add_argument(
        "--no-gdp-scale",
        action="store_true",
        help="Keep the product's native reference-year totals (2018).",
    )
    parser.add_argument(
        "--dsn",
        default=None,
        help="Postgres DSN for the density grid. Defaults to Settings.database_url.",
    )
    parser.add_argument(
        "--cache-dir",
        type=Path,
        default=Path("/tmp/litpop"),
        help="HDF5 download cache (default: /tmp/litpop).",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=Path("/tmp/litpop_value.csv"),
        help="CSV output path (default: /tmp/litpop_value.csv).",
    )
    args = parser.parse_args()
    # asyncpg rejects the SQLAlchemy +asyncpg driver tag.
    args.dsn = args.dsn or get_settings().database_url.replace(
        "postgresql+asyncpg://", "postgresql://", 1
    )
    args.iso3 = [c.upper() for c in args.iso3]
    asyncio.run(main_async(args))


if __name__ == "__main__":
    main()
