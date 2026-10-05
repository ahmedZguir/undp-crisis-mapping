# rasterio is untyped, so strict mode only adds noise.
# pyright: basic
"""Build public.population_value as a CSV from WorldPop 100m constrained rasters.

Pixels are summed into 0.02 degree cells keyed by pixel centre, matching the
density and LitPop grids. WorldPop rests on pre-war censuses for most conflict
countries, so this is a pre-crisis baseline. A superuser loads the CSV (the
script prints the commands).

Usage (from apps/api/):
    uv run python -m api.scripts.build_population_grid LBY SYR YEM TUR SDN PSE
    uv run python -m api.scripts.build_population_grid --all --delete-rasters
    uv run python -m api.scripts.build_population_grid LBY --tif /data/lby_ppp_2020.tif
"""

from __future__ import annotations

import argparse
import re
import time
from pathlib import Path

import httpx
import numpy as np
import rasterio
from rasterio.windows import Window

from api.analysis.constants import POPULATION_GRID_STEP_DEG

# WorldPop Global 2000-2020 Constrained, UN-adjusted, 100m. Countries are split
# across two method folders (e.g. Sudan is under maxar_v1); each is tried.
DEFAULT_YEAR = 2020
DEFAULT_METHODS = ("BSGM", "maxar_v1")
DEFAULT_TIF_TEMPLATE = (
    "https://data.worldpop.org/GIS/Population/Global_2000_2020_Constrained/"
    "{year}/{method}/{iso}/{iso_lower}_ppp_{year}_UNadj_constrained.tif"
)
# Scraped by --all to enumerate countries.
METHOD_DIR_TEMPLATE = (
    "https://data.worldpop.org/GIS/Population/Global_2000_2020_Constrained/{year}/{method}/"
)


def list_world_iso3(client: httpx.Client, *, year: int, methods: tuple[str, ...]) -> list[str]:
    """Every ISO3 the constrained product publishes, across method folders."""
    isos: set[str] = set()
    for method in methods:
        url = METHOD_DIR_TEMPLATE.format(year=year, method=method)
        resp = client.get(url)
        resp.raise_for_status()
        isos.update(re.findall(r'href="([A-Z]{3})/"', resp.text))
    if not isos:
        raise RuntimeError("no ISO folders found in the WorldPop directory listings")
    return sorted(isos)


def _candidate_urls(template: str, iso3: str, year: int, methods: tuple[str, ...]) -> list[str]:
    fields = {"iso": iso3, "iso_lower": iso3.lower(), "year": year}
    if "{method}" in template:
        return [template.format(method=m, **fields) for m in methods]
    return [template.format(**fields)]


def resolve_tif(
    client: httpx.Client,
    iso3: str,
    *,
    template: str,
    year: int,
    methods: tuple[str, ...],
    cache_dir: Path,
) -> Path:
    """Local path for a country's GeoTIFF: passthrough if local, else download the
    first method URL that exists (cached by file name)."""
    candidates = _candidate_urls(template, iso3, year, methods)
    if "://" not in candidates[0]:  # a local path was passed
        path = Path(candidates[0])
        if not path.exists():
            raise FileNotFoundError(f"[{iso3}] GeoTIFF not found: {path}")
        return path

    cache_dir.mkdir(parents=True, exist_ok=True)
    for url in candidates:
        path = cache_dir / url.rsplit("/", 1)[-1]
        if path.exists() and path.stat().st_size > 0:
            print(f"[{iso3}] cached    {path.name}", flush=True)
            return path
        head = client.head(url)
        if head.status_code != httpx.codes.OK:
            continue
        # Retry transient errors so one flaky read doesn't drop a country.
        for attempt in range(3):
            try:
                print(f"[{iso3}] downloading {url}", flush=True)
                with client.stream("GET", url) as resp:
                    resp.raise_for_status()
                    with path.open("wb") as fh:
                        for chunk in resp.iter_bytes(chunk_size=1 << 20):
                            fh.write(chunk)
                print(f"[{iso3}] downloaded {path.name} ({path.stat().st_size:,} B)", flush=True)
                return path
            except httpx.HTTPError as exc:
                path.unlink(missing_ok=True)
                if attempt == 2:
                    raise
                print(f"[{iso3}] download error ({exc}); retry {attempt + 1}/2", flush=True)
                time.sleep(2.0 * (attempt + 1))
    raise FileNotFoundError(f"[{iso3}] no GeoTIFF found at: {', '.join(candidates)}")


def accumulate(path: Path) -> tuple[dict[tuple[int, int], float], float]:
    """Sum a WorldPop GeoTIFF's pixels into 0.02° cells, block by block.

    Returns the per-cell grid and the pixel total. Requires a north-up,
    unrotated transform.
    """
    step = POPULATION_GRID_STEP_DEG
    grid: dict[tuple[int, int], float] = {}
    total = 0.0
    with rasterio.open(path) as ds:
        t = ds.transform
        if t.b != 0.0 or t.d != 0.0:
            raise ValueError(f"{path.name}: rotated/sheared transform unsupported")
        nodata = ds.nodata
        for _, window in ds.block_windows(1):
            assert isinstance(window, Window)
            arr = ds.read(1, window=window).astype(np.float64)
            mask = np.isfinite(arr) & (arr > 0)
            if nodata is not None:
                mask &= arr != float(nodata)
            if not mask.any():
                continue

            rows, cols = np.nonzero(mask)
            vals = arr[rows, cols]
            lon = t.c + t.a * (window.col_off + cols + 0.5)
            lat = t.f + t.e * (window.row_off + rows + 0.5)
            ix = np.floor(lon / step).astype(np.int64)
            iy = np.floor(lat / step).astype(np.int64)

            total += float(vals.sum())
            keys = np.stack([ix, iy], axis=1)
            uniq, inv = np.unique(keys, axis=0, return_inverse=True)
            sums = np.bincount(inv, weights=vals, minlength=len(uniq))
            for (cx, cy), s in zip(uniq.tolist(), sums.tolist(), strict=True):
                k = (int(cx), int(cy))
                grid[k] = grid.get(k, 0.0) + float(s)
    return grid, total


def build_country(
    client: httpx.Client,
    iso3: str,
    *,
    template: str,
    year: int,
    methods: tuple[str, ...],
    cache_dir: Path,
) -> tuple[str, dict[tuple[int, int], float]]:
    path = resolve_tif(
        client, iso3, template=template, year=year, methods=methods, cache_dir=cache_dir
    )
    grid, total = accumulate(path)
    binned = sum(grid.values())
    # Every masked pixel lands in exactly one cell.
    if not np.isclose(binned, total, rtol=1e-9):
        raise AssertionError(f"{iso3}: binning lost population: {binned:.3f} != {total:.3f}")
    tag = f"worldpop_{year}_{iso3}"
    print(
        f"[{iso3}] {len(grid):,} cells | total {total:,.0f} people | dataset {tag}",
        flush=True,
    )
    return tag, grid


def main() -> None:
    parser = argparse.ArgumentParser(description="Build the WorldPop population grid CSV.")
    parser.add_argument("iso3", nargs="*", help="ISO3 country codes, e.g. LBY SYR YEM.")
    parser.add_argument(
        "--all",
        action="store_true",
        help="Every country the WorldPop constrained product publishes (the world).",
    )
    parser.add_argument("--year", type=int, default=DEFAULT_YEAR, help="Product year (tag + URL).")
    parser.add_argument(
        "--tif",
        help="A single local GeoTIFF path or URL (only valid with one ISO3 code).",
    )
    parser.add_argument(
        "--delete-rasters",
        action="store_true",
        help="Delete each GeoTIFF after binning it (bound disk on a world run).",
    )
    parser.add_argument(
        "--tif-template",
        default=DEFAULT_TIF_TEMPLATE,
        help="URL/path template with {iso} {iso_lower} {year} (default: WorldPop 2020).",
    )
    parser.add_argument(
        "--cache-dir",
        type=Path,
        default=Path("/tmp/worldpop"),
        help="GeoTIFF download cache (default: /tmp/worldpop).",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=Path("/tmp/population_value.csv"),
        help="CSV output path (default: /tmp/population_value.csv).",
    )
    args = parser.parse_args()
    args.iso3 = [c.upper() for c in args.iso3]
    if args.tif and len(args.iso3) != 1:
        raise SystemExit("--tif takes exactly one ISO3 code; use --tif-template for many")
    template = args.tif if args.tif else args.tif_template

    print(f"[build] grid step  = {POPULATION_GRID_STEP_DEG} deg", flush=True)
    print(f"[build] out        = {args.out}", flush=True)

    tags: list[str] = []
    failures: list[str] = []
    with (
        httpx.Client(timeout=httpx.Timeout(600.0, connect=30.0), follow_redirects=True) as client,
        args.out.open("w") as fh,
    ):
        if args.all:
            iso3s = list_world_iso3(client, year=args.year, methods=DEFAULT_METHODS)
        elif args.iso3:
            iso3s = args.iso3
        else:
            raise SystemExit("pass ISO3 codes or --all")
        print(f"[build] countries  = {len(iso3s)}", flush=True)

        for i, iso3 in enumerate(iso3s, 1):
            try:
                tag, grid = build_country(
                    client,
                    iso3,
                    template=template,
                    year=args.year,
                    methods=DEFAULT_METHODS,
                    cache_dir=args.cache_dir,
                )
            except Exception as exc:  # keep a multi-country run going
                print(f"[{iso3}] ({i}/{len(iso3s)}) FAILED: {exc}", flush=True)
                failures.append(iso3)
                continue
            for (ix, iy), people in sorted(grid.items()):
                fh.write(f"{tag},{ix},{iy},{people}\n")
            tags.append(tag)
            if args.delete_rasters:
                for p in args.cache_dir.glob(f"{iso3.lower()}_ppp_*.tif"):
                    p.unlink(missing_ok=True)

    if failures:
        print(f"\n[build] FAILED countries ({len(failures)}): {' '.join(failures)}", flush=True)
    print(f"\n[build] CSV: {args.out}\n", flush=True)
    print("Load it as a DB superuser:", flush=True)
    # Delete first: lookups sum population spatially, so a re-run would double-count.
    if args.all:
        like = f"worldpop_{args.year}\\_%"
        deletes = f"    -c \"delete from public.population_value where dataset like '{like}';\""
    else:
        deletes = " \\\n".join(
            f"    -c \"delete from public.population_value where dataset = '{tag}';\""
            for tag in tags
        )
    print(
        "  PGPASSWORD=<pw> psql -h 127.0.0.1 -p 54322 -U postgres -d postgres \\\n"
        f"{deletes} \\\n"
        f"    -c \"\\copy public.population_value(dataset,ix,iy,people) from '{args.out}' csv\"",
        flush=True,
    )


if __name__ == "__main__":
    main()
