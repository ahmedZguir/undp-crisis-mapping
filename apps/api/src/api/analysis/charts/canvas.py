"""SVG primitives for the analysis report: palette, projections, basemap tiles, ramps.

Tile maps inline CARTO basemap tiles as base64; a failed fetch leaves a flat
background. WeasyPrint's SVG engine ignores document CSS, so every `<svg>` sets
`font-family` itself.
"""

from __future__ import annotations

import base64
import html as html_mod
import math
import tempfile
import urllib.request
from collections.abc import Iterator, Sequence
from datetime import date, timedelta
from itertools import pairwise
from pathlib import Path
from typing import Any

from api.analysis.metrics import (
    BBox,
    District,
    ReportPoint,
)

# GeoJSON geometry (Polygon / MultiPolygon); coordinate nesting is dynamic.
Geom = dict[str, Any]

# Palette

INK = "#1a2733"
MUTED = "#5b6b7a"
FAINT = "#8a98a5"
RULE = "#d7dde3"
LAND = "#eef1f4"
SEA = "#ffffff"
BOUNDARY = "#b7c2cc"
UNDP = "#0468b1"
C_MIN = "#f3c14b"
C_PAR = "#e0701f"
C_COM = "#b3261e"
DAMAGE_COLOR = {"minimal": C_MIN, "partial": C_PAR, "complete": C_COM}
DEBRIS = "#c77c10"

# Matches the "Source Sans 3" @font-face family in report.css.
_SVG_FONT = "Source Sans 3, Helvetica, sans-serif"

SEV_RANK = {"complete": 2, "partial": 1, "minimal": 0}

esc = html_mod.escape


# Geometry


def rings(geom: Geom) -> Iterator[tuple[list[Any], list[Any]]]:
    """Yield (outer_ring, [hole_rings]) for a GeoJSON Polygon / MultiPolygon."""
    polys = [geom["coordinates"]] if geom["type"] == "Polygon" else geom["coordinates"]
    for poly in polys:
        yield poly[0], poly[1:]


def _in_ring(lon: float, lat: float, ring: list[Any]) -> bool:
    inside = False
    n = len(ring)
    j = n - 1
    for i in range(n):
        xi, yi = ring[i][0], ring[i][1]
        xj, yj = ring[j][0], ring[j][1]
        if (yi > lat) != (yj > lat) and lon < (xj - xi) * (lat - yi) / (yj - yi) + xi:
            inside = not inside
        j = i
    return inside


def contains(geom: Geom | None, lon: float, lat: float) -> bool:
    if not geom:
        return False
    for outer, holes in rings(geom):
        if _in_ring(lon, lat, outer) and not any(_in_ring(lon, lat, h) for h in holes):
            return True
    return False


# Projections


class _Projection:
    """Fits a lon/lat box into a pixel frame; subclasses supply the projection."""

    w: float
    h: float

    def xy(self, lon: float, lat: float) -> tuple[float, float]:
        raise NotImplementedError

    def path(self, geom: Geom) -> str:
        d: list[str] = []
        for outer, holes in rings(geom):
            for ring in [outer, *holes]:
                pts = [self.xy(lon, lat) for lon, lat, *_ in ring]
                d.append("M" + "L".join(f"{x:.1f} {y:.1f}" for x, y in pts) + "Z")
        return "".join(d)


class View(_Projection):
    """Equirectangular fit of a lon/lat box into a pixel frame (locator only)."""

    def __init__(
        self,
        minlon: float,
        minlat: float,
        maxlon: float,
        maxlat: float,
        w: float,
        h: float,
        pad: float = 0.06,
    ) -> None:
        dlon = (maxlon - minlon) or 1e-9
        dlat = (maxlat - minlat) or 1e-9
        minlon -= dlon * pad
        maxlon += dlon * pad
        minlat -= dlat * pad
        maxlat += dlat * pad
        self.kx = math.cos(math.radians((minlat + maxlat) / 2))
        sx = w / ((maxlon - minlon) * self.kx)
        sy = h / (maxlat - minlat)
        self.s = min(sx, sy)
        self.w, self.h = w, h
        self.ox = (w - (maxlon - minlon) * self.kx * self.s) / 2
        self.oy = (h - (maxlat - minlat) * self.s) / 2
        self.minlon, self.maxlat = minlon, maxlat

    def xy(self, lon: float, lat: float) -> tuple[float, float]:
        x = self.ox + (lon - self.minlon) * self.kx * self.s
        y = self.oy + (self.maxlat - lat) * self.s
        return x, y


def _merc(lon: float, lat: float) -> tuple[float, float]:
    """Web Mercator, normalised world coordinates in [0, 1]."""
    mx = (lon + 180.0) / 360.0
    my = (1.0 - math.log(math.tan(math.pi / 4 + math.radians(lat) / 2)) / math.pi) / 2.0
    return mx, my


class MercView(_Projection):
    """Web Mercator fit of a lon/lat box into a pixel frame (for tile maps)."""

    def __init__(
        self,
        minlon: float,
        minlat: float,
        maxlon: float,
        maxlat: float,
        w: float,
        h: float,
        pad: float = 0.06,
    ) -> None:
        dlon = (maxlon - minlon) or 1e-9
        dlat = (maxlat - minlat) or 1e-9
        minlon -= dlon * pad
        maxlon += dlon * pad
        minlat -= dlat * pad
        maxlat += dlat * pad
        x0, y1 = _merc(minlon, minlat)
        x1, y0 = _merc(maxlon, maxlat)
        self.s = min(w / (x1 - x0), h / (y1 - y0))
        self.w, self.h = w, h
        self.ox = (w - (x1 - x0) * self.s) / 2 - x0 * self.s
        self.oy = (h - (y1 - y0) * self.s) / 2 - y0 * self.s
        self.midlat = (minlat + maxlat) / 2

    def xy(self, lon: float, lat: float) -> tuple[float, float]:
        mx, my = _merc(lon, lat)
        return self.ox + mx * self.s, self.oy + my * self.s

    def scalebar(self, x: float, y: float) -> str:
        px_per_deg = self.xy(1.0, self.midlat)[0] - self.xy(0.0, self.midlat)[0]
        km_per_px = 111.32 * math.cos(math.radians(self.midlat)) / px_per_deg
        px = 55.0
        km = 1
        for km in (1, 2, 5, 10, 20, 50):
            px = km / km_per_px
            if px >= 55:
                break
        return (
            f'<g font-size="7.5" fill="{MUTED}">'
            f'<rect x="{x}" y="{y}" width="{px:.0f}" height="3" fill="{INK}"/>'
            f'<rect x="{x}" y="{y}" width="{px / 2:.0f}" height="3" fill="#fff" stroke="{INK}" stroke-width="0.6"/>'
            f'<text x="{x}" y="{y - 3}" stroke="#fff" stroke-width="2" paint-order="stroke">0</text>'
            f'<text x="{x}" y="{y - 3}">0</text>'
            f'<text x="{x + px:.0f}" y="{y - 3}" text-anchor="end" stroke="#fff" stroke-width="2">{km} km</text>'
            f'<text x="{x + px:.0f}" y="{y - 3}" text-anchor="end">{km} km</text>'
            f"</g>"
        )


# Basemap tiles

TILE_URL = "https://basemaps.cartocdn.com/light_nolabels/{z}/{x}/{y}.png"
TILE_ATTRIBUTION = "© OpenStreetMap contributors © CARTO"
_TILE_CACHE = Path(tempfile.gettempdir()) / "undp_report_tiles"


def _fetch_tile(z: int, x: int, y: int) -> bytes | None:
    """Disk-cached, best-effort tile fetch; None on any failure."""
    cached = _TILE_CACHE / str(z) / str(x) / f"{y}.png"
    if cached.exists():
        return cached.read_bytes()
    try:
        req = urllib.request.Request(
            TILE_URL.format(z=z, x=x, y=y),
            headers={"User-Agent": "undp-crisis-mapping"},
        )
        with urllib.request.urlopen(req, timeout=8) as resp:
            data = resp.read()
    except Exception:
        return None
    try:
        cached.parent.mkdir(parents=True, exist_ok=True)
        cached.write_bytes(data)
    except OSError:
        pass
    return data


def tile_layer(view: MercView) -> str:
    """Base64-inlined <image> tiles covering the view, at about 1.5x display resolution."""
    px_per_world = view.s
    z = 1
    while 256 * (2**z) < 1.5 * px_per_world and z < 18:
        z += 1
    n = 2**z
    wx0 = (0 - view.ox) / view.s
    wy0 = (0 - view.oy) / view.s
    wx1 = (view.w - view.ox) / view.s
    wy1 = (view.h - view.oy) / view.s
    parts: list[str] = []
    for tx in range(max(0, math.floor(wx0 * n)), min(n - 1, math.floor(wx1 * n)) + 1):
        for ty in range(max(0, math.floor(wy0 * n)), min(n - 1, math.floor(wy1 * n)) + 1):
            data = _fetch_tile(z, tx, ty)
            if data is None:
                continue
            x = view.ox + (tx / n) * view.s
            y = view.oy + (ty / n) * view.s
            size = view.s / n
            b64 = base64.b64encode(data).decode("ascii")
            parts.append(
                f'<image x="{x:.2f}" y="{y:.2f}" width="{size:.2f}" height="{size:.2f}" '
                f'href="data:image/png;base64,{b64}"/>'
            )
    return "".join(parts)


def tile_attribution(view: MercView) -> str:
    return (
        f'<text x="{view.w - 4}" y="{view.h - 4}" text-anchor="end" font-size="5.5" '
        f'fill="{FAINT}" stroke="#fff" stroke-width="1.6" paint-order="stroke">{esc(TILE_ATTRIBUTION)}</text>'
        f'<text x="{view.w - 4}" y="{view.h - 4}" text-anchor="end" font-size="5.5" '
        f'fill="{FAINT}">{esc(TILE_ATTRIBUTION)}</text>'
    )


def halo_text(
    x: float,
    y: float,
    s: str,
    size: float = 8,
    fill: str = "#45525e",
    anchor: str = "middle",
    weight: str = "600",
) -> str:
    # Two-pass halo because WeasyPrint does not support paint-order="stroke".
    common = (
        f'x="{x:.0f}" y="{y:.0f}" text-anchor="{anchor}" font-size="{size}" font-weight="{weight}"'
    )
    return (
        f'<text {common} fill="#ffffff" stroke="#ffffff" stroke-width="2.4" '
        f'stroke-linejoin="round">{esc(s)}</text>'
        f'<text {common} fill="{fill}">{esc(s)}</text>'
    )


def svg_open(w: float, h: float) -> str:
    return f'<svg width="{w}" height="{h}" viewBox="0 0 {w} {h}" font-family="{_SVG_FONT}">'


# Colour ramps


def map_open(view: MercView, clip_id: str, *, defs: str = "") -> list[str]:
    """Opening of a tile map: svg root, clip to the frame, sea fill, then the tiles.

    The caller closes the clip group with "</g>" before drawing labels.
    """
    w, h = view.w, view.h
    return [
        svg_open(w, h),
        f'{defs}<clipPath id="{clip_id}"><rect width="{w}" height="{h}"/></clipPath>'
        f'<rect width="{w}" height="{h}" fill="{SEA}"/>'
        f'<g clip-path="url(#{clip_id})">',
        tile_layer(view),
    ]


def map_close(parts: list[str], view: MercView, *, scalebar_at: tuple[int, int] = (10, 12)) -> str:
    """Scale bar (x, offset from the bottom), tile attribution and the closing tag."""
    x, bottom = scalebar_at
    parts.append(view.scalebar(x, view.h - bottom))
    parts.append(tile_attribution(view))
    parts.append("</svg>")
    return "".join(parts)


def rank_pins(view: MercView, districts: Sequence[District]) -> str:
    """Numbered priority badges just above each district centroid."""
    out: list[str] = []
    for d in districts:
        x, y = view.xy(d.centroid_lon, d.centroid_lat)
        y -= 16
        out.append(
            f'<circle cx="{x:.0f}" cy="{y:.0f}" r="7.5" fill="{INK}" stroke="#fff" stroke-width="1.4"/>'
            f'<text x="{x:.0f}" y="{y + 3:.0f}" text-anchor="middle" font-size="9" '
            f'font-weight="700" fill="#fff">{d.rank}</text>'
        )
    return "".join(out)


def blocked_labels(view: MercView, districts: Sequence[District]) -> str:
    """ "N% blocked" under the three districts with the highest blocked share."""
    worst = sorted(
        (d for d in districts if d.debris_known >= 5 and d.debris_blocked_share > 0),
        key=lambda d: d.debris_blocked_share,
        reverse=True,
    )[:3]
    out: list[str] = []
    for d in worst:
        x, y = view.xy(d.centroid_lon, d.centroid_lat)
        if 0 <= x <= view.w and 0 <= y <= view.h:
            out.append(
                halo_text(x, y + 10, f"{d.debris_blocked_share:.0%} blocked", size=7.5, fill=C_COM)
            )
    return "".join(out)


def choropleth_ramp(t: float) -> str:
    """Light-peach to deep-red (choropleth)."""
    a = (0xFD, 0xEE, 0xE0)
    b = (0xB3, 0x26, 0x1E)
    return "#{:02x}{:02x}{:02x}".format(*(round(a[i] + (b[i] - a[i]) * t) for i in range(3)))


# Below 0.5 so the long tail of low-count cells lifts off the yellow floor.
DENSITY_GAMMA = 0.4


def _interp_stops(stops: list[tuple[float, tuple[int, int, int]]], t: float) -> str:
    """Piecewise-linear colour interpolation over (position, rgb) stops."""
    t = max(0.0, min(1.0, t))
    for (p0, a), (p1, b) in pairwise(stops):
        if t <= p1 or p1 >= 1.0:
            f = (t - p0) / (p1 - p0) if p1 > p0 else 0.0
            return "#{:02x}{:02x}{:02x}".format(
                *(round(a[i] + (b[i] - a[i]) * f) for i in range(3))
            )
    return "#{:02x}{:02x}{:02x}".format(*stops[-1][1])


# Yellow to red, weighted toward red so low-count cells read orange.
_YR_STOPS = [
    (0.00, (0xF3, 0xC1, 0x4B)),  # yellow, only the very lowest cells
    (0.22, (0xE0, 0x70, 0x1F)),  # orange
    (1.00, (0xB3, 0x26, 0x1E)),  # red
]


def ramp_yr(t: float) -> str:
    return _interp_stops(_YR_STOPS, t)


# Crimson to oxblood for the aggregated blocked-access map; the floor stays
# saturated so a lightly blocked cell still reads red.
_DEBRIS_STOPS = [
    (0.00, (0xC4, 0x18, 0x12)),  # saturated crimson, a lightly-blocked cell
    (1.00, (0x57, 0x0A, 0x06)),  # deep oxblood, heavily blocked
]


def debris_ramp(t: float) -> str:
    return _interp_stops(_DEBRIS_STOPS, t)


# Shared helpers over points / districts


def points_extent(
    points: Sequence[ReportPoint], bbox: BBox | None
) -> tuple[float, float, float, float]:
    """Map frame: the reports' extent, else the AOI bbox, else a tiny default."""
    if points:
        lons = [p.lon for p in points]
        lats = [p.lat for p in points]
        return min(lons), min(lats), max(lons), max(lats)
    if bbox:
        return bbox.min_lon, bbox.min_lat, bbox.max_lon, bbox.max_lat
    return 0.0, 0.0, 0.01, 0.01


def district_labels(view: MercView, districts: Sequence[District]) -> str:
    parts: list[str] = []
    for d in districts:
        x, y = view.xy(d.centroid_lon, d.centroid_lat)
        if -10 <= x <= view.w + 10 and 0 <= y <= view.h:
            parts.append(halo_text(x, y, d.name))
    return "".join(parts)


# Same teal the admin dashboard map uses for the crisis boundary.
_AOI_TEAL = "#0d9488"


def aoi_contour(view: MercView, geometry: Geom | None) -> str:
    """Dashed AOI boundary drawn above the data layers; empty without geometry."""
    if not geometry:
        return ""
    d = view.path(geometry)
    if not d:
        return ""
    return (
        f'<path d="{d}" fill="none" stroke="{_AOI_TEAL}" stroke-width="4.5" '
        f'stroke-opacity="0.22" stroke-linejoin="round"/>'
        f'<path d="{d}" fill="none" stroke="{_AOI_TEAL}" stroke-width="1.8" '
        f'stroke-opacity="0.95" stroke-dasharray="4 2.4" stroke-linejoin="round"/>'
    )


def day_range(points: Sequence[ReportPoint]) -> list[date]:
    days = sorted({p.created_at.date() for p in points})
    if not days:
        return []
    d0, d1 = days[0], days[-1]
    return [d0 + timedelta(days=i) for i in range((d1 - d0).days + 1)]


def daily_counts(points: Sequence[ReportPoint], days: list[date]) -> dict[str, list[int]]:
    by = {c: [0] * len(days) for c in ("minimal", "partial", "complete")}
    idx = {d: i for i, d in enumerate(days)}
    for p in points:
        i = idx.get(p.created_at.date())
        if i is not None and p.damage_class in by:
            by[p.damage_class][i] += 1
    return by


def points_in_district(points: Sequence[ReportPoint], d: District) -> list[ReportPoint]:
    if not d.geom:
        return []
    return [p for p in points if contains(d.geom, p.lon, p.lat)]
