"""Unit tests for PmtilesExtractor.

The extractor shells out to the `pmtiles` Go CLI to slice a per-crisis subset
out of Overture's published global PMTiles file, then uploads the resulting
file to the `crisis-pmtiles` public bucket. All three seams (subprocess,
uploader, tile-release locator) are behind Protocols / a callable so the test
substitutes fakes — no real network call.

Contract pinned here:

- `extract(polygon_wkb, target_path)` returns `(public_url, tile_release)`.
- The tile release in the returned tuple and in the URL passed to the CLI both
  come from the injected `OvertureTileReleaseLocator` — *not* from the parquet
  data release.
- The subprocess command is
  `pmtiles extract <overture_global_url> <target_path> --bbox=xmin,ymin,xmax,ymax`.
- The bbox is computed from the supplied polygon's envelope.
- The uploader is called with the produced `target_path` and the resulting
  public URL is returned verbatim.
- Subprocess failures surface the CLI's combined stdout+stderr in the error
  message (the CLI logs to stdout, so capturing only stderr would lose it).
"""

from __future__ import annotations

from pathlib import Path

import pytest
from shapely.geometry import MultiPolygon, Polygon

from api.buildings.overture_release_locator import OvertureTileReleaseLocator
from api.buildings.pmtiles_extractor import (
    PmtilesExtractor,
    PmtilesExtractorError,
    public_pmtiles_url,
)


class _FakeRunner:
    """Records subprocess invocations; never shells out for real."""

    def __init__(self, returncode: int = 0, output: str = "") -> None:
        self.returncode = returncode
        self.output = output
        self.calls: list[list[str]] = []

    def run(self, argv: list[str]) -> tuple[int, str]:
        self.calls.append(argv)
        return self.returncode, self.output


class _FakeUploader:
    """Returns a deterministic public URL for the uploaded file."""

    def __init__(self) -> None:
        self.base_url = "https://example.supabase.co/storage/v1/object/public/crisis-pmtiles"
        self.uploaded: list[tuple[str, bytes]] = []

    def upload(self, object_name: str, content: bytes) -> str:
        self.uploaded.append((object_name, content))
        return f"{self.base_url}/{object_name}"


def _polygon_wkb(xmin: float, ymin: float, xmax: float, ymax: float) -> bytes:
    poly = Polygon([(xmin, ymin), (xmax, ymin), (xmax, ymax), (xmin, ymax), (xmin, ymin)])
    return MultiPolygon([poly]).wkb


def _locator(tile_release: str) -> OvertureTileReleaseLocator:
    return OvertureTileReleaseLocator(listing=lambda: tile_release)


def test_extract_passes_envelope_bbox_to_pmtiles_cli(tmp_path: Path) -> None:
    runner = _FakeRunner()
    uploader = _FakeUploader()
    target_path = tmp_path / "crisis.pmtiles"
    target_path.write_bytes(b"PMTILES-FIXTURE")

    extractor = PmtilesExtractor(
        runner=runner,
        uploader=uploader,
        tile_release_locator=_locator("2026-01-21"),
    )
    polygon_wkb = _polygon_wkb(50.7, 24.4, 51.7, 26.2)

    public_url, tile_release = extractor.extract(
        polygon_wkb=polygon_wkb,
        target_path=target_path,
    )

    # The tile release returned is the one the locator produced — separate
    # from the parquet data release used for ingest.
    assert tile_release == "2026-01-21"
    assert public_url.startswith(
        "https://example.supabase.co/storage/v1/object/public/crisis-pmtiles/"
    )
    assert len(runner.calls) == 1
    argv = runner.calls[0]
    assert argv[0] == "pmtiles"
    assert argv[1] == "extract"
    # The URL must be substituted with the tile release, not any parquet release.
    assert "2026-01-21" in argv[2]
    assert argv[2].endswith("buildings.pmtiles")
    assert str(target_path) in argv
    bbox_flag = next(a for a in argv if a.startswith("--bbox="))
    assert bbox_flag == "--bbox=50.7,24.4,51.7,26.2"


def test_extract_uploads_file_and_returns_url(tmp_path: Path) -> None:
    runner = _FakeRunner()
    uploader = _FakeUploader()
    target_path = tmp_path / "out.pmtiles"
    target_path.write_bytes(b"hello-pmtiles")

    extractor = PmtilesExtractor(
        runner=runner,
        uploader=uploader,
        tile_release_locator=_locator("2026-01-21"),
    )
    public_url, _ = extractor.extract(
        polygon_wkb=_polygon_wkb(0.0, 0.0, 1.0, 1.0),
        target_path=target_path,
    )

    assert len(uploader.uploaded) == 1
    object_name, content = uploader.uploaded[0]
    assert object_name.endswith(".pmtiles")
    assert content == b"hello-pmtiles"
    assert public_url == f"{uploader.base_url}/{object_name}"


def test_extract_raises_when_subprocess_fails(tmp_path: Path) -> None:
    runner = _FakeRunner(returncode=1, output="boom: bad bbox")
    uploader = _FakeUploader()
    target_path = tmp_path / "out.pmtiles"

    extractor = PmtilesExtractor(
        runner=runner,
        uploader=uploader,
        tile_release_locator=_locator("2026-01-21"),
    )

    with pytest.raises(PmtilesExtractorError) as exc:
        extractor.extract(
            polygon_wkb=_polygon_wkb(0.0, 0.0, 1.0, 1.0),
            target_path=target_path,
        )
    assert "boom: bad bbox" in str(exc.value)
    # Failed subprocess must not trigger an upload.
    assert uploader.uploaded == []


# --- public_pmtiles_url: read-time origin rebuild ---------------------------
# `crises.pmtiles_url` is persisted at ingest from the then-current
# `supabase_public_url`. When the public base later changes (LAN dev → ngrok →
# real domain), the stored host goes stale; `public_pmtiles_url` rebuilds the
# origin on read so no DB migration is needed. See crises/routes.list_crises.


def test_public_pmtiles_url_rebuilds_stale_origin() -> None:
    # A value baked at ingest with the old LAN http origin...
    stored = "http://10.4.64.22:54321/storage/v1/object/public/crisis-pmtiles/abc.pmtiles"
    # ...is rebuilt against the current single-origin https base.
    out = public_pmtiles_url(stored, "https://crisis.example.org/supabase")
    assert out == (
        "https://crisis.example.org/supabase/storage/v1/object/public/crisis-pmtiles/abc.pmtiles"
    )


def test_public_pmtiles_url_strips_trailing_slash_on_base() -> None:
    stored = "http://127.0.0.1:54321/storage/v1/object/public/crisis-pmtiles/x.pmtiles"
    out = public_pmtiles_url(stored, "https://host/supabase/")
    assert out == "https://host/supabase/storage/v1/object/public/crisis-pmtiles/x.pmtiles"


def test_public_pmtiles_url_passes_through_null_and_unrecognized() -> None:
    assert public_pmtiles_url(None, "https://host/supabase") is None
    # Not a public-object URL (e.g. already a dev-proxy URL) → unchanged.
    other = "https://host/dev/pmtiles/crisis-pmtiles/abc.pmtiles"
    assert public_pmtiles_url(other, "https://host/supabase") == other
