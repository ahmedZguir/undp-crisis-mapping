"""Overture data and tile releases are published separately; tile bakes lag data
releases, so the two release strings are not interchangeable.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from datetime import UTC, datetime

import httpx
from overturemaps.core import get_latest_release  # pyright: ignore[reportMissingTypeStubs]


class OvertureReleaseLocator:
    """Latest Overture data release (parquet, YYYY-MM-DD.X)."""

    def __init__(self, listing: Callable[[], str] | None = None) -> None:
        self._listing = listing or get_latest_release

    def latest(self) -> str:
        return self._listing()


# Anonymous-readable bucket; list-objects-v2 works without AWS credentials.
OVERTURE_TILE_BUCKET_LIST_URL = "https://overturemaps-tiles-us-west-2-beta.s3.amazonaws.com/"
_TILE_KEY_RE = re.compile(r"^(\d{4}-\d{2}-\d{2})/buildings\.pmtiles$")


class OvertureTileReleaseLocator:
    """Latest tile release (YYYY-MM-DD) with a baked buildings.pmtiles.

    Probes year prefixes newest first, so usually one listing call suffices.
    """

    def __init__(self, listing: Callable[[], str] | None = None) -> None:
        self._listing = listing or self._fetch_latest_from_bucket

    def latest(self) -> str:
        return self._listing()

    def _fetch_latest_from_bucket(self) -> str:
        current_year = datetime.now(UTC).year
        with httpx.Client(timeout=30.0) as client:
            for year in range(current_year, current_year - 4, -1):
                latest = self._latest_for_prefix(client, f"{year}-")
                if latest is not None:
                    return latest
        raise RuntimeError("no buildings.pmtiles found in Overture tile bucket within last 4 years")

    @staticmethod
    def _latest_for_prefix(client: httpx.Client, prefix: str) -> str | None:
        keys: list[str] = []
        token: str | None = None
        while True:
            params: dict[str, str] = {"list-type": "2", "prefix": prefix}
            if token is not None:
                params["continuation-token"] = token
            response = client.get(OVERTURE_TILE_BUCKET_LIST_URL, params=params)
            response.raise_for_status()
            xml = response.text
            keys.extend(re.findall(r"<Key>([^<]+)</Key>", xml))
            if "<IsTruncated>true</IsTruncated>" not in xml:
                break
            match = re.search(r"<NextContinuationToken>([^<]+)</NextContinuationToken>", xml)
            if match is None:
                break
            token = match.group(1)
        dates = sorted({m.group(1) for k in keys if (m := _TILE_KEY_RE.match(k))})
        return dates[-1] if dates else None


__all__ = [
    "OVERTURE_TILE_BUCKET_LIST_URL",
    "OvertureReleaseLocator",
    "OvertureTileReleaseLocator",
]
