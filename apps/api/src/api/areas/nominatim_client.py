"""OpenStreetMap Nominatim client, used when Overture has no match for an area
search and by the geocoding pipeline. Calls are serialised and spaced to honour
Nominatim's usage policy; failures return an empty list.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from typing import Any, Protocol, cast

import httpx

NOMINATIM_BASE_URL = "https://nominatim.openstreetmap.org"
# Nominatim returns 403 for placeholder-looking UAs (example.com, fake contacts).
NOMINATIM_USER_AGENT = "undp-crisis-mapping/0.1"
NOMINATIM_TIMEOUT_SECONDS = 5.0
NOMINATIM_MIN_SPACING_SECONDS = 1.1
NOMINATIM_MAX_LIMIT = 10

_logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class OSMArea:
    """A Nominatim hit in Overture's area shape. osm_id is prefixed, e.g.
    osm:relation/123; bbox is (xmin, ymin, xmax, ymax)."""

    osm_id: str
    name: str
    subtype: str
    country: str | None
    parents: list[str]
    bbox: tuple[float, float, float, float]
    geometry: dict[str, object]


class OSMAreaSearcher(Protocol):
    async def search(self, q: str, country: str | None, limit: int) -> list[OSMArea]: ...


class OSMNominatimClient:
    """One instance per process, since it holds the rate-limit lock."""

    def __init__(self, http_client: httpx.AsyncClient | None = None) -> None:
        self._lock = asyncio.Lock()
        self._last_call_at: float = 0.0
        self._client: httpx.AsyncClient | None = http_client
        self._owns_client = http_client is None

    async def search(self, q: str, country: str | None, limit: int) -> list[OSMArea]:
        """Area-picker search; node results are dropped since the picker needs a polygon."""
        clamped_limit = max(1, min(limit, NOMINATIM_MAX_LIMIT))
        params: dict[str, str] = {
            "q": q,
            "format": "jsonv2",
            "polygon_geojson": "1",
            "addressdetails": "1",
            "limit": str(clamped_limit),
        }
        if country is not None:
            params["countrycodes"] = country.lower()
        rows = await self._locked_request(params, q)
        hits: list[OSMArea] = []
        for row in rows:
            mapped = _row_to_osm_area(row)
            if mapped is not None:
                hits.append(mapped)
        return hits

    async def geocode_search(
        self,
        q: str,
        *,
        country_codes: list[str],
        viewbox: tuple[float, float, float, float] | None,
        limit: int,
    ) -> list[dict[str, Any]]:
        """Geocoding search returning raw Nominatim rows, including point POIs.

        viewbox (xmin, ymin, xmax, ymax) is sent with bounded=1 to restrict
        results to the AOI rectangle.
        """
        clamped_limit = max(1, min(limit, NOMINATIM_MAX_LIMIT))
        params: dict[str, str] = {
            "q": q,
            "format": "jsonv2",
            "polygon_geojson": "1",
            "addressdetails": "1",
            "limit": str(clamped_limit),
        }
        if country_codes:
            params["countrycodes"] = ",".join(c.lower() for c in country_codes)
        if viewbox is not None:
            xmin, ymin, xmax, ymax = viewbox
            # Nominatim viewbox is "left,top,right,bottom" = xmin,ymax,xmax,ymin.
            params["viewbox"] = f"{xmin},{ymax},{xmax},{ymin}"
            params["bounded"] = "1"
        rows = await self._locked_request(params, q)
        return [cast(dict[str, Any], r) for r in rows if isinstance(r, dict)]

    async def aclose(self) -> None:
        if self._owns_client and self._client is not None:
            await self._client.aclose()
            self._client = None

    def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=NOMINATIM_TIMEOUT_SECONDS)
        return self._client

    async def _locked_request(self, params: dict[str, str], q: str) -> list[Any]:
        """One GET at a time, at least NOMINATIM_MIN_SPACING_SECONDS apart."""
        async with self._lock:
            spacing = NOMINATIM_MIN_SPACING_SECONDS - (time.monotonic() - self._last_call_at)
            if spacing > 0:
                await asyncio.sleep(spacing)
            try:
                return await self._request(params, q)
            finally:
                self._last_call_at = time.monotonic()

    async def _request(self, params: dict[str, str], q: str) -> list[Any]:
        """Raw payload list, or [] on any failure."""
        headers = {"User-Agent": NOMINATIM_USER_AGENT, "Accept": "application/json"}
        _logger.info(
            "osm_search_request",
            extra={
                "event": "osm_search_request",
                "q": q,
                "country": params.get("countrycodes"),
            },
        )
        started = time.monotonic()
        client = self._get_client()
        try:
            response = await client.get(
                f"{NOMINATIM_BASE_URL}/search", params=params, headers=headers
            )
        except httpx.TimeoutException:
            _logger.info(
                "osm_search_failed",
                extra={"event": "osm_search_failed", "reason": "timeout", "q": q},
            )
            return []
        except httpx.HTTPError as exc:
            _logger.info(
                "osm_search_failed",
                extra={
                    "event": "osm_search_failed",
                    "reason": f"http-{type(exc).__name__}",
                    "q": q,
                },
            )
            return []

        if response.status_code >= 300:
            _logger.info(
                "osm_search_failed",
                extra={
                    "event": "osm_search_failed",
                    "reason": f"http-status-{response.status_code}",
                    "q": q,
                },
            )
            return []

        try:
            payload = response.json()
        except ValueError:
            _logger.info(
                "osm_search_failed",
                extra={"event": "osm_search_failed", "reason": "json-decode", "q": q},
            )
            return []

        if not isinstance(payload, list):
            _logger.info(
                "osm_search_failed",
                extra={"event": "osm_search_failed", "reason": "empty-result-shape", "q": q},
            )
            return []

        rows = cast(list[Any], payload)
        elapsed_ms = int((time.monotonic() - started) * 1000)
        _logger.info(
            "osm_search_ok",
            extra={"event": "osm_search_ok", "q": q, "n_rows": len(rows), "ms": elapsed_ms},
        )
        return rows


def _row_to_osm_area(row: object) -> OSMArea | None:
    """None for nodes and rows without a usable polygon, name, or bbox."""
    if not isinstance(row, dict):
        return None
    typed_row = cast(dict[str, Any], row)
    osm_type = typed_row.get("osm_type")
    raw_osm_id = typed_row.get("osm_id")
    if not isinstance(osm_type, str) or osm_type == "node":
        return None
    if not isinstance(raw_osm_id, int | str):
        return None

    geometry_raw = typed_row.get("geojson")
    if not isinstance(geometry_raw, dict):
        return None
    geometry = cast(dict[str, object], geometry_raw)
    if geometry.get("type") not in ("Polygon", "MultiPolygon"):
        return None

    display_name = typed_row.get("display_name")
    if not isinstance(display_name, str) or not display_name:
        return None
    segments = [seg.strip() for seg in display_name.split(",")]
    name = segments[0] if segments else display_name
    parents = [seg for seg in segments[1:6] if seg]

    addresstype = typed_row.get("addresstype") or typed_row.get("type") or "place"
    subtype = f"osm:{addresstype}" if isinstance(addresstype, str) else "osm:place"

    address = typed_row.get("address")
    country: str | None = None
    if isinstance(address, dict):
        cc = cast(dict[str, Any], address).get("country_code")
        if isinstance(cc, str) and cc:
            country = cc.upper()

    bbox = _parse_bbox(typed_row.get("boundingbox"))
    if bbox is None:
        return None

    return OSMArea(
        osm_id=f"osm:{osm_type}/{raw_osm_id}",
        name=name,
        subtype=subtype,
        country=country,
        parents=parents,
        bbox=bbox,
        geometry=geometry,
    )


def _parse_bbox(raw: object) -> tuple[float, float, float, float] | None:
    """Nominatim's [ymin, ymax, xmin, xmax] strings to (xmin, ymin, xmax, ymax)."""
    if not isinstance(raw, list):
        return None
    items = cast(list[Any], raw)
    if len(items) != 4:
        return None
    try:
        ymin, ymax, xmin, xmax = (float(v) for v in items)
    except (TypeError, ValueError):
        return None
    return (xmin, ymin, xmax, ymax)


__all__ = [
    "NOMINATIM_BASE_URL",
    "NOMINATIM_MAX_LIMIT",
    "NOMINATIM_MIN_SPACING_SECONDS",
    "NOMINATIM_TIMEOUT_SECONDS",
    "NOMINATIM_USER_AGENT",
    "OSMArea",
    "OSMAreaSearcher",
    "OSMNominatimClient",
]
