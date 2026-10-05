"""Unit tests for OvertureReleaseLocator and OvertureTileReleaseLocator.

Both locators expose a `listing` constructor seam — tests substitute a fake
to avoid network calls. Default listings (one wraps the `overturemaps`
library, the other lists the public tiles bucket) are exercised once each as
smoke tests so the wiring is regression-protected without pinning the exact
returned value.
"""

from __future__ import annotations

import re

from api.buildings.overture_release_locator import (
    OvertureReleaseLocator,
    OvertureTileReleaseLocator,
)


def test_locator_returns_release_string_from_listing() -> None:
    locator = OvertureReleaseLocator(listing=lambda: "2026-04-15.0")
    assert locator.latest() == "2026-04-15.0"


def test_locator_default_listing_returns_non_empty_release() -> None:
    """The default listing wraps the `overturemaps` library — exercise it once
    so the wiring is regression-protected without pinning the exact value."""
    locator = OvertureReleaseLocator()
    release = locator.latest()
    assert isinstance(release, str)
    assert len(release) > 0


def test_tile_locator_returns_release_string_from_listing() -> None:
    locator = OvertureTileReleaseLocator(listing=lambda: "2026-01-21")
    assert locator.latest() == "2026-01-21"


def test_tile_locator_default_listing_returns_dated_release() -> None:
    """Default lists the Overture tile bucket; expect a `YYYY-MM-DD` string."""
    locator = OvertureTileReleaseLocator()
    release = locator.latest()
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}", release), release
