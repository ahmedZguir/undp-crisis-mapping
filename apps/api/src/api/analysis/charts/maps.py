"""Report maps: hero, choropleth, debris, H3 hotspots, blind spots, density, locator."""

from __future__ import annotations

import math
from collections import Counter
from collections.abc import Sequence
from typing import Any, cast

import h3  # pyright: ignore[reportMissingTypeStubs]

from api.analysis.charts.canvas import (
    C_COM,
    C_MIN,
    C_PAR,
    DAMAGE_COLOR,
    DENSITY_GAMMA,
    INK,
    MUTED,
    RULE,
    UNDP,
    Geom,
    MercView,
    View,
    aoi_contour,
    blocked_labels,
    choropleth_ramp,
    debris_ramp,
    district_labels,
    esc,
    halo_text,
    map_close,
    map_open,
    points_extent,
    ramp_yr,
    rank_pins,
    rings,
    svg_open,
)
from api.analysis.metrics import (
    BBox,
    District,
    MeshCell,
    ReportPoint,
)

# Page-1 hero map


def hero_map_svg(
    points: Sequence[ReportPoint],
    districts: Sequence[District],
    *,
    bbox: BBox | None = None,
    geometry: Geom | None = None,
    w: int = 420,
    h: int = 448,
) -> str:
    """Basemap with the top-3 priority districts outlined and one dot per report."""
    view = MercView(*points_extent(points, bbox), w, h, pad=0.10)
    top3 = [d for d in districts if d.rank <= 3 and d.geom]
    parts = map_open(view, "hero-clip")
    for d in top3:
        assert d.geom is not None
        parts.append(
            f'<path d="{view.path(d.geom)}" fill="{UNDP}" fill-opacity="0.10" '
            f'stroke="{UNDP}" stroke-width="1.2"/>'
        )
    # complete drawn last so red sits on top where dense
    for cls in ("minimal", "partial", "complete"):
        col = DAMAGE_COLOR[cls]
        for p in points:
            if p.damage_class == cls:
                x, y = view.xy(p.lon, p.lat)
                parts.append(
                    f'<circle cx="{x:.1f}" cy="{y:.1f}" r="2.5" fill="{col}" '
                    f'fill-opacity="0.9" stroke="#fff" stroke-width="0.4"/>'
                )
    parts.append(aoi_contour(view, geometry))
    parts.append("</g>")
    parts.append(district_labels(view, districts))
    parts.append(rank_pins(view, top3))
    lx, ly = w - 122, 10
    parts.append(
        f'<rect x="{lx}" y="{ly}" width="112" height="74" fill="#ffffff" fill-opacity="0.93" '
        f'stroke="{RULE}" stroke-width="1"/>'
        f'<text x="{lx + 8}" y="{ly + 13}" font-size="6.8" letter-spacing="1.2" fill="{MUTED}" '
        f'font-weight="700">DAMAGE REPORTED</text>'
    )
    for i, (lbl, col) in enumerate(
        (("Minimal damage", C_MIN), ("Partial damage", C_PAR), ("Complete loss", C_COM))
    ):
        yy = ly + 27 + i * 12
        parts.append(
            f'<circle cx="{lx + 13}" cy="{yy - 2.6}" r="3.2" fill="{col}"/>'
            f'<text x="{lx + 21}" y="{yy}" font-size="7.6" fill="{INK}">{lbl}</text>'
        )
    yy = ly + 27 + 3 * 12
    parts.append(
        f'<circle cx="{lx + 13}" cy="{yy - 2.6}" r="4.4" fill="{INK}"/>'
        f'<text x="{lx + 13}" y="{yy - 0.2}" text-anchor="middle" font-size="6.5" '
        f'font-weight="700" fill="#fff">1</text>'
        f'<text x="{lx + 21}" y="{yy}" font-size="7.6" fill="{INK}">Priority rank</text>'
    )
    return map_close(parts, view, scalebar_at=(12, 14))


# Damage geography maps


def choropleth_svg(
    districts: Sequence[District],
    points: Sequence[ReportPoint],
    *,
    bbox: BBox | None = None,
    geometry: Geom | None = None,
    w: int = 340,
    h: int = 302,
    min_n: int = 5,
) -> str:
    """Districts filled by sqrt-scaled severe-report count; under `min_n` reports are hatched."""
    view = MercView(*points_extent(points, bbox), w, h, pad=0.10)
    drawable = [d for d in districts if d.geom]
    mx = max((d.damage.severe for d in drawable), default=1) or 1
    parts = map_open(
        view,
        "chor-clip",
        defs=(
            '<defs><pattern id="hatch" width="5" height="5" patternTransform="rotate(45)" '
            'patternUnits="userSpaceOnUse">'
            '<line x1="0" y1="0" x2="0" y2="5" stroke="#9fb0bd" stroke-width="1.6"/></pattern></defs>'
        ),
    )
    for d in drawable:
        assert d.geom is not None
        if d.report_count < min_n:
            fill = 'fill="url(#hatch)"'
        else:
            fill = f'fill="{choropleth_ramp(math.sqrt(d.damage.severe / mx))}" fill-opacity="0.62"'
        parts.append(f'<path d="{view.path(d.geom)}" {fill} stroke="#fff" stroke-width="1"/>')
    parts.append(aoi_contour(view, geometry))
    parts.append("</g>")
    for d in drawable:
        x, y = view.xy(d.centroid_lon, d.centroid_lat)
        if not (0 <= x <= w and 0 <= y <= h):
            continue
        parts.append(halo_text(x, y - 2, d.name, size=7.5))
        sub = (
            f"{d.damage.severe} severe of {d.report_count} rpt"
            if d.report_count >= min_n
            else f"only {d.report_count} rpt"
        )
        parts.append(halo_text(x, y + 8, sub, size=7, fill=MUTED, weight="500"))
    return map_close(parts, view)


def debris_map_svg(
    points: Sequence[ReportPoint],
    districts: Sequence[District],
    *,
    bbox: BBox | None = None,
    geometry: Geom | None = None,
    w: int = 340,
    h: int = 302,
) -> str:
    """Blocked access as red crosses, other reports as faint dots."""
    view = MercView(*points_extent(points, bbox), w, h, pad=0.10)
    parts = map_open(view, "deb-clip")
    for p in points:
        if p.debris != "yes":
            x, y = view.xy(p.lon, p.lat)
            parts.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="1.6" fill="#c3ccd4"/>')
    a = 2.4
    for p in points:
        if p.debris == "yes":
            x, y = view.xy(p.lon, p.lat)
            parts.append(
                f'<path d="M{x - a:.1f} {y - a:.1f}L{x + a:.1f} {y + a:.1f}'
                f'M{x - a:.1f} {y + a:.1f}L{x + a:.1f} {y - a:.1f}" '
                f'stroke="{C_COM}" stroke-width="1.4" stroke-linecap="round"/>'
            )
    parts.append(aoi_contour(view, geometry))
    parts.append("</g>")
    parts.append(district_labels(view, districts))
    parts.append(blocked_labels(view, districts))
    return map_close(parts, view)


# Hotspots & blind spots


def _h3_cell(lat: float, lon: float, res: int) -> str:
    """h3.latlng_to_cell, narrowing the stubless return to a cell-id str."""
    return str(h3.latlng_to_cell(lat, lon, res))  # pyright: ignore[reportUnknownArgumentType, reportUnknownMemberType]


def _h3_boundary(cell: str) -> list[tuple[float, float]]:
    """h3.cell_to_boundary, narrowing the stubless return to (lat, lng) pairs."""
    raw = cast(
        "list[tuple[float, float]]",
        h3.cell_to_boundary(cell),  # pyright: ignore[reportUnknownMemberType, reportUnknownArgumentType]
    )
    return [(float(lat), float(lng)) for lat, lng in raw]


def _h3_density_render(
    agg: Counter[str], view: MercView, *, geometry: Geom | None, w: int, h: int
) -> tuple[str, int]:
    """Draw H3 cell counts as filled hexagons. Returns (svg, hex count)."""
    nmax = max(agg.values(), default=1)
    parts = map_open(view, "h3-clip")
    for cell, n in sorted(agg.items(), key=lambda kv: kv[1]):  # dense hexes on top
        pts = [view.xy(lng, lat) for lat, lng in _h3_boundary(cell)]
        d = "M" + "L".join(f"{x:.1f} {y:.1f}" for x, y in pts) + "Z"
        t = (n / nmax) ** DENSITY_GAMMA
        parts.append(
            f'<path d="{d}" fill="{ramp_yr(t)}" fill-opacity="0.92" stroke="#fff" stroke-width="0.5"/>'
        )
    parts.append(aoi_contour(view, geometry))
    parts.append("</g>")
    return map_close(parts, view), len(agg)


def h3_density_svg(
    points: Sequence[ReportPoint],
    *,
    bbox: BBox | None = None,
    geometry: Geom | None = None,
    res: int = 6,
    w: int = 340,
    h: int = 302,
) -> tuple[str, int]:
    """Report density on H3 hexagons. Returns (svg, hex count)."""
    view = MercView(*points_extent(points, bbox), w, h, pad=0.10)
    agg: Counter[str] = Counter()
    for p in points:
        agg[_h3_cell(p.lat, p.lon, res)] += 1
    return _h3_density_render(agg, view, geometry=geometry, w=w, h=h)


def h3_density_from_cells_svg(
    cells: Sequence[MeshCell],
    *,
    bbox: BBox | None = None,
    geometry: Geom | None = None,
    res: int = 6,
    w: int = 340,
    h: int = 302,
) -> tuple[str, int]:
    """`h3_density_svg` from mesh cell counts, binned by centroid. Returns (svg, hex count)."""
    view = MercView(*points_extent((), bbox), w, h, pad=0.10)
    agg: Counter[str] = Counter()
    for c in cells:
        if c.observed:
            agg[_h3_cell(c.lat, c.lon, res)] += c.observed
    return _h3_density_render(agg, view, geometry=geometry, w=w, h=h)


def blind_spot_svg(
    cells: Sequence[MeshCell],
    districts: Sequence[District],
    *,
    step: float,
    bbox: BBox | None = None,
    geometry: Geom | None = None,
    points: Sequence[ReportPoint] = (),
    w: int = 340,
    h: int = 302,
) -> str:
    """Possible blind spots as solid blue circles; observed cells are not drawn."""
    view = MercView(*points_extent(points, bbox), w, h, pad=0.10)
    # Radius tracks the cell size, floored for legibility and capped for dense patches.
    cell_px = abs(view.xy(step, 0.0)[0] - view.xy(0.0, 0.0)[0])
    r = min(max(cell_px * 0.5, 4.0), 5.5)
    parts = map_open(view, "cov-clip")
    for c in cells:
        if c.blind_spot:
            x, y = view.xy(c.lon, c.lat)
            parts.append(
                f'<circle cx="{x:.1f}" cy="{y:.1f}" r="{r:.1f}" fill="{UNDP}" '
                f'fill-opacity="0.85" stroke="#fff" stroke-width="1"/>'
            )
    parts.append(aoi_contour(view, geometry))
    parts.append("</g>")
    parts.append(district_labels(view, districts))
    return map_close(parts, view)


# Aggregated density maps, used above REPORT_POINTS_THRESHOLD instead of point maps.


def _mesh_rect(view: MercView, c: MeshCell, step: float, style: str) -> str:
    """One mesh cell as an SVG rect (centroid ± step/2)."""
    x0, y0 = view.xy(c.lon - step / 2, c.lat + step / 2)
    x1, y1 = view.xy(c.lon + step / 2, c.lat - step / 2)
    return f'<rect x="{x0:.1f}" y="{y0:.1f}" width="{x1 - x0:.1f}" height="{y1 - y0:.1f}" {style}/>'


def _density_legend(w: int, label: str, *, grad_id: str, ramp: Any = ramp_yr) -> str:
    """Small top-right gradient-bar legend."""
    bw = 156
    lx, ly = w - bw - 10, 10
    stops = "".join(f'<stop offset="{i / 4:.2f}" stop-color="{ramp(i / 4)}"/>' for i in range(5))
    return (
        f'<defs><linearGradient id="{grad_id}">{stops}</linearGradient></defs>'
        f'<rect x="{lx}" y="{ly}" width="{bw}" height="34" fill="#fff" fill-opacity="0.93" '
        f'stroke="{RULE}" stroke-width="1"/>'
        f'<text x="{lx + 8}" y="{ly + 12}" font-size="6.6" fill="{MUTED}" font-weight="700">'
        f"{esc(label)}</text>"
        f'<rect x="{lx + 8}" y="{ly + 17}" width="{bw - 16}" height="7" fill="url(#{grad_id})"/>'
        f'<text x="{lx + 8}" y="{ly + 31}" font-size="6.2" fill="{MUTED}">fewer</text>'
        f'<text x="{lx + bw - 8}" y="{ly + 31}" font-size="6.2" fill="{MUTED}" '
        f'text-anchor="end">more</text>'
    )


def hero_density_svg(
    cells: Sequence[MeshCell],
    districts: Sequence[District],
    *,
    step: float,
    bbox: BBox | None = None,
    geometry: Geom | None = None,
    w: int = 420,
    h: int = 448,
) -> str:
    """Density version of `hero_map_svg`, filling mesh cells by report count."""
    view = MercView(*points_extent((), bbox), w, h, pad=0.10)
    top3 = [d for d in districts if d.rank <= 3 and d.geom]
    observed = [c for c in cells if c.observed > 0]
    nmax = max((c.observed for c in observed), default=1) or 1
    parts = map_open(view, "herod-clip")
    for c in sorted(observed, key=lambda c: c.observed):  # dense on top
        fill = ramp_yr((c.observed / nmax) ** DENSITY_GAMMA)
        parts.append(
            _mesh_rect(
                view, c, step, f'fill="{fill}" fill-opacity="0.95" stroke="#fff" stroke-width="0.3"'
            )
        )
    for d in top3:
        assert d.geom is not None
        parts.append(
            f'<path d="{view.path(d.geom)}" fill="none" stroke="{UNDP}" stroke-width="1.4"/>'
        )
    parts.append(aoi_contour(view, geometry))
    parts.append("</g>")
    parts.append(district_labels(view, districts))
    parts.append(rank_pins(view, top3))
    parts.append(_density_legend(w, "Report density: deeper = more reports", grad_id="lg-hero"))
    return map_close(parts, view, scalebar_at=(12, 14))


def debris_density_svg(
    cells: Sequence[MeshCell],
    districts: Sequence[District],
    *,
    step: float,
    bbox: BBox | None = None,
    geometry: Geom | None = None,
    w: int = 340,
    h: int = 302,
) -> str:
    """Density version of `debris_map_svg`, filling cells by blocked-report count."""
    view = MercView(*points_extent((), bbox), w, h, pad=0.10)
    dmax = max((c.debris_yes for c in cells), default=1) or 1
    parts = map_open(view, "debd-clip")
    for c in sorted(cells, key=lambda c: c.debris_yes):
        if c.debris_yes == 0:
            continue
        t = (c.debris_yes / dmax) ** DENSITY_GAMMA
        parts.append(
            _mesh_rect(
                view,
                c,
                step,
                f'fill="{debris_ramp(t)}" fill-opacity="0.95" stroke="#fff" stroke-width="0.3"',
            )
        )
    parts.append(aoi_contour(view, geometry))
    parts.append("</g>")
    parts.append(district_labels(view, districts))
    parts.append(blocked_labels(view, districts))
    parts.append(
        _density_legend(
            w, "Blocked access: deeper = more blocked", grad_id="lg-deb", ramp=debris_ramp
        )
    )
    return map_close(parts, view)


def locator_svg(
    districts: Sequence[District], target_name: str, *, w: int = 46, h: int = 56
) -> str:
    """Tiny locator: all districts as context, the target filled red."""
    drawable = [d for d in districts if d.geom]
    if not drawable:
        return f'<svg width="{w}" height="{h}"></svg>'
    lons: list[float] = []
    lats: list[float] = []
    for d in drawable:
        assert d.geom is not None
        for outer, _ in rings(d.geom):
            lons += [pt[0] for pt in outer]
            lats += [pt[1] for pt in outer]
    view = View(min(lons), min(lats), max(lons), max(lats), w, h, pad=0.04)
    parts = [svg_open(w, h)]
    for d in drawable:
        assert d.geom is not None
        parts.append(
            f'<path d="{view.path(d.geom)}" fill="#e2e8ed" stroke="#fff" stroke-width="0.6"/>'
        )
    target = next((d for d in drawable if d.name == target_name), None)
    if target and target.geom:
        parts.append(f'<path d="{view.path(target.geom)}" fill="{C_COM}" fill-opacity="0.9"/>')
    parts.append("</svg>")
    return "".join(parts)
