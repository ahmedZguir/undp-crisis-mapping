"""Cut a per-crisis PMTiles file from Overture's global buildings tiles with the
pmtiles CLI and upload it to Supabase Storage.
"""

from __future__ import annotations

import subprocess
import uuid
from pathlib import Path
from typing import Protocol

from shapely import wkb as shapely_wkb
from shapely.geometry.base import BaseGeometry

from api.buildings.overture_release_locator import OvertureTileReleaseLocator

# HTTPS form of the anonymous-readable bucket, so the CLI needs no AWS credentials.
OVERTURE_GLOBAL_PMTILES_URL_TEMPLATE = (
    "https://overturemaps-tiles-us-west-2-beta.s3.amazonaws.com/{release}/buildings.pmtiles"
)


# Splits the Supabase origin from the object path in a stored pmtiles_url.
_PUBLIC_OBJECT_MARKER = "/storage/v1/object/public/"


def public_pmtiles_url(stored: str | None, supabase_public_url: str) -> str | None:
    """Rewrite a stored pmtiles_url onto the current public Supabase origin.

    The stored host goes stale when supabase_public_url changes. Non-public-object
    URLs are returned unchanged.
    """
    if not stored:
        return stored
    idx = stored.find(_PUBLIC_OBJECT_MARKER)
    if idx == -1:
        return stored
    return f"{supabase_public_url.rstrip('/')}{stored[idx:]}"


class PmtilesExtractorError(RuntimeError):
    """The pmtiles CLI exited non-zero or the upload failed."""


class SubprocessRunner(Protocol):
    def run(self, argv: list[str]) -> tuple[int, str]: ...


class PmtilesUploader(Protocol):
    def upload(self, object_name: str, content: bytes) -> str: ...


class _RealSubprocessRunner:
    """Runs the CLI with stderr merged into stdout; pmtiles logs errors to stdout."""

    def run(self, argv: list[str]) -> tuple[int, str]:
        result = subprocess.run(
            argv,
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )
        return result.returncode, result.stdout or ""


class PmtilesExtractor:
    def __init__(
        self,
        uploader: PmtilesUploader,
        tile_release_locator: OvertureTileReleaseLocator,
        runner: SubprocessRunner | None = None,
        url_template: str = OVERTURE_GLOBAL_PMTILES_URL_TEMPLATE,
    ) -> None:
        self._uploader = uploader
        self._tile_release_locator = tile_release_locator
        self._runner = runner or _RealSubprocessRunner()
        self._url_template = url_template

    def extract(
        self,
        polygon_wkb: bytes,
        target_path: Path,
    ) -> tuple[str, str]:
        """Extract the polygon's bbox and upload it; returns (public_url, tile_release)."""
        tile_release = self._tile_release_locator.latest()
        bbox = _envelope_bbox(polygon_wkb)
        bbox_flag = f"--bbox={bbox[0]},{bbox[1]},{bbox[2]},{bbox[3]}"
        global_url = self._url_template.format(release=tile_release)
        argv = [
            "pmtiles",
            "extract",
            global_url,
            str(target_path),
            bbox_flag,
        ]
        returncode, output = self._runner.run(argv)
        if returncode != 0:
            raise PmtilesExtractorError(
                f"pmtiles extract failed (exit {returncode}): {output.strip()}"
            )

        content = target_path.read_bytes()
        object_name = f"{uuid.uuid4().hex}.pmtiles"
        public_url = self._uploader.upload(object_name, content)
        return public_url, tile_release


def _envelope_bbox(polygon_wkb: bytes) -> tuple[float, float, float, float]:
    geom: BaseGeometry = shapely_wkb.loads(polygon_wkb)
    xmin, ymin, xmax, ymax = geom.bounds
    return float(xmin), float(ymin), float(xmax), float(ymax)


class SupabasePmtilesUploader:
    """Uploads to the public crisis-pmtiles bucket. Object names are random per
    upload, so the immutable cache header is safe."""

    BUCKET = "crisis-pmtiles"
    CACHE_CONTROL = "public, max-age=31536000, immutable"

    def __init__(self, settings: object | None = None) -> None:
        # Lazy import keeps this module importable without an env file.
        from api.core.config import Settings, get_settings

        self._settings: Settings = settings if isinstance(settings, Settings) else get_settings()

    def upload(self, object_name: str, content: bytes) -> str:
        import httpx

        # Upload via the internal URL; return the browser-reachable public URL.
        url = f"{self._settings.supabase_url}/storage/v1/object/{self.BUCKET}/{object_name}"
        headers = {
            "Authorization": f"Bearer {self._settings.supabase_service_role_key}",
            "Content-Type": "application/vnd.pmtiles",
            "Cache-Control": self.CACHE_CONTROL,
        }
        with httpx.Client(timeout=120.0) as client:
            response = client.post(url, content=content, headers=headers)
        if response.status_code >= 300:
            raise PmtilesExtractorError(
                f"pmtiles upload failed ({response.status_code}): {response.text}"
            )
        return (
            f"{self._settings.supabase_public_url}"
            f"/storage/v1/object/public/{self.BUCKET}/{object_name}"
        )


__all__ = [
    "OVERTURE_GLOBAL_PMTILES_URL_TEMPLATE",
    "PmtilesExtractor",
    "PmtilesExtractorError",
    "PmtilesUploader",
    "SubprocessRunner",
    "SupabasePmtilesUploader",
    "public_pmtiles_url",
]
