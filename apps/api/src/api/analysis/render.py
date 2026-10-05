"""Render `CrisisReportData` to PDF via Jinja2 HTML, inline SVG charts and WeasyPrint.

Map tiles are fetched at render time on a best-effort basis.
"""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from jinja2 import Environment, FileSystemLoader, select_autoescape
from weasyprint import CSS, HTML  # pyright: ignore[reportMissingTypeStubs]
from weasyprint.text.fonts import FontConfiguration  # pyright: ignore[reportMissingTypeStubs]

from api.analysis.charts import canvas, figures, maps
from api.analysis.format import fmt_coverage_pct, fmt_usd
from api.analysis.metrics import (
    FLAG_BLIND_SPOT,
    BBox,
    CrisisReportData,
    District,
    ImpactEstimates,
    InfraItem,
    MeshCell,
    ReportMeta,
)
from api.buildings.density_grid import DENSITY_GRID_STEP_DEG

_TEMPLATE_DIR = Path(__file__).parent / "templates"
_TEMPLATE_NAME = "report.html.j2"
_CSS_PATH = _TEMPLATE_DIR / "report.css"

# Cap the facility table length; the by-type bars still carry full counts.
_MAX_FACILITIES = 8
# Columns in the "which infrastructure is hit in the top districts" crosstab.
_XTAB_COLS = 5

# Minimal ISO-3166 alpha-2 -> display name for the countries the platform runs
# in; unknown codes fall back to the code itself.
_COUNTRY_NAME = {
    "QA": "Qatar",
    "LY": "Libya",
    "SY": "Syria",
    "TR": "Türkiye",
    "UA": "Ukraine",
    "MA": "Morocco",
}

_FLAG_LABEL = {"severe": "Severe", "blocked": "Blocked", "blind_spot": "Blind spot"}
_FLAG_CLASS = {"severe": "severe", "blocked": "blocked", "blind_spot": "blind"}


# En and em dash, escaped to keep the glyphs out of the source.
_DASHES = "\u2013\u2014"


def _nodash(text: str | None) -> str:
    """Replace dashes in rendered prose: hyphen between digits, comma elsewhere."""
    if not text:
        return text or ""
    s = str(text)
    s = re.sub(rf"(?<=\d)\s*[{_DASHES}]\s*(?=\d)", "-", s)
    s = re.sub(rf"\s*[{_DASHES}]\s*", ", ", s)
    return re.sub(r",\s*,", ", ", s)


def _build_env() -> Environment:
    env = Environment(
        loader=FileSystemLoader(str(_TEMPLATE_DIR)),
        autoescape=select_autoescape(["html", "xml", "j2"]),
        trim_blocks=True,
        lstrip_blocks=True,
    )
    env.filters["nodash"] = _nodash
    return env


def _display_name(name: str) -> str:
    """Title-case an all-lowercase crisis name; leave capitalised names untouched."""
    stripped = name.strip()
    if not stripped:
        return "Untitled crisis"
    if stripped == stripped.lower():
        return stripped.title()
    return stripped


def _fmt_dt(dt: datetime) -> str:
    """Human, unambiguous timestamp: '03 June 2026, 14:00 UTC'."""
    tz = dt.tzname() or "UTC"
    return f"{dt.day:02d} {dt:%B %Y}, {dt:%H:%M} {tz}"


def _as_of_label(meta: ReportMeta) -> str:
    """When the underlying data was last current (latest report in the window)."""
    return _fmt_dt(meta.as_of)


def _econ_labels(impact: ImpactEstimates) -> tuple[str, str]:
    if not impact.economic_available:
        return "", ""
    return fmt_usd(impact.economic_low_usd), fmt_usd(impact.economic_high_usd)


def _infra_by_district(
    items: list[InfraItem], districts: list[District]
) -> dict[str, Counter[str]]:
    """Per-district critical-infra-type counts by point-in-polygon on item location."""
    out: dict[str, Counter[str]] = {}
    for it in items:
        if it.lat is None or it.lon is None:
            continue
        for d in districts:
            if canvas.contains(d.geom, it.lon, it.lat):
                out.setdefault(d.name, Counter())[it.infra_type] += 1
                break
    return out


def _district_rows(
    districts: list[District],
    *,
    sparkline_for: Callable[[District], str],
) -> list[dict[str, Any]]:
    """Priority-table rows with pre-rendered locator, mix-bar and sparkline SVGs."""
    rows: list[dict[str, Any]] = []
    for d in districts:
        if not d.report_count:
            continue
        rows.append(
            {
                "rank": d.rank,
                "name": d.name,
                "top": d.rank <= 3,
                "flags": [(_FLAG_CLASS[f], _FLAG_LABEL[f]) for f in d.flags if f in _FLAG_CLASS],
                "report_count": d.report_count,
                "complete": d.damage.complete,
                "partial": d.damage.partial,
                "minimal": d.damage.minimal,
                "debris_pct": (f"{d.debris_blocked_share:.0%}" if d.debris_known >= 3 else "n/a"),
                "services_hit": d.services_hit,
                "locator_svg": maps.locator_svg(districts, d.name),
                "mix_bar_svg": figures.mix_bar_svg(
                    d.damage.minimal, d.damage.partial, d.damage.complete
                ),
                "sparkline_svg": sparkline_for(d),
            }
        )
    return rows


def _cells_bbox(cells: Sequence[MeshCell]) -> BBox | None:
    """Bbox of cells with reports, padded by half a cell; None if none have reports.

    Frames the aggregated maps on the data instead of the larger AOI envelope.
    """
    observed = [c for c in cells if c.observed > 0]
    if not observed:
        return None
    half = DENSITY_GRID_STEP_DEG / 2
    return BBox(
        min_lon=min(c.lon for c in observed) - half,
        min_lat=min(c.lat for c in observed) - half,
        max_lon=max(c.lon for c in observed) + half,
        max_lat=max(c.lat for c in observed) + half,
    )


def _xtab(districts: list[District], infra_by_district: dict[str, Counter[str]]) -> dict[str, Any]:
    """Top-3 districts x their busiest infra types: a small crosstab of counts."""
    top3 = [d for d in districts if d.rank <= 3 and d.report_count]
    combined: Counter[str] = Counter()
    for d in top3:
        combined.update(infra_by_district.get(d.name, Counter()))
    cols = [t for t, _ in combined.most_common(_XTAB_COLS)]
    col_labels = [(t, figures.INFRA_LABEL.get(t, t.replace("_", " ").title())) for t in cols]
    mx = (
        max(
            (infra_by_district.get(d.name, Counter()).get(t, 0) for d in top3 for t in cols),
            default=1,
        )
        or 1
    )
    rows: list[dict[str, Any]] = []
    for d in top3:
        per = infra_by_district.get(d.name, Counter())
        cells: list[dict[str, Any]] = []
        for t in cols:
            v = per.get(t, 0)
            shade = 0.05 + 0.22 * v / mx if v else 0.0
            cells.append({"v": v or "", "shade": f"{shade:.2f}" if v else ""})
        rows.append({"rank": d.rank, "name": d.name, "cells": cells})
    return {"col_labels": col_labels, "rows": rows, "has_data": bool(cols)}


def build_html(data: CrisisReportData) -> str:
    """Render the report HTML; the template only places finished SVG strings and scalars."""
    metrics = data.metrics
    meta = metrics.meta
    districts = metrics.priority_districts
    points = metrics.points
    cells = metrics.coverage.cells
    bbox = meta.bbox
    geom = meta.geometry  # AOI polygon for the dashed boundary contour on the maps
    dmg = metrics.damage
    aggregated = metrics.aggregated
    step = DENSITY_GRID_STEP_DEG

    # For large crises `aggregated` is set and `points` is empty; figures are drawn
    # from SQL rollups and maps are framed on the observed cells.
    if aggregated is None:
        days = canvas.day_range(points)
        debris_yes = sum(1 for p in points if p.debris == "yes")
        debris_known = sum(1 for p in points if p.debris in ("yes", "no"))
        timeline_svg, timeline_status = figures.timeline_svg(points)
        h3_svg, h3_n = maps.h3_density_svg(points, bbox=bbox, geometry=geom, h=262)
        hero_svg = maps.hero_map_svg(points, districts, bbox=bbox, geometry=geom)
        choropleth_svg = maps.choropleth_svg(districts, points, bbox=bbox, geometry=geom)
        debris_map_svg = maps.debris_map_svg(points, districts, bbox=bbox, geometry=geom)
        blind_svg = maps.blind_spot_svg(
            cells, districts, step=step, bbox=bbox, geometry=geom, points=points, h=262
        )
        district_rows = _district_rows(
            districts,
            sparkline_for=lambda d: figures.sparkline_svg(
                canvas.points_in_district(points, d), days
            ),
        )
    else:
        days = aggregated.days
        debris_yes = aggregated.debris_yes
        debris_known = aggregated.debris_known
        dens_bbox = _cells_bbox(cells) or bbox
        timeline_svg, timeline_status = figures.timeline_from_daily(aggregated.daily)
        h3_svg, h3_n = maps.h3_density_from_cells_svg(cells, bbox=dens_bbox, geometry=geom, h=262)
        hero_svg = maps.hero_density_svg(cells, districts, step=step, bbox=dens_bbox, geometry=geom)
        choropleth_svg = maps.choropleth_svg(districts, (), bbox=dens_bbox, geometry=geom)
        debris_map_svg = maps.debris_density_svg(
            cells, districts, step=step, bbox=dens_bbox, geometry=geom
        )
        blind_svg = maps.blind_spot_svg(
            cells, districts, step=step, bbox=dens_bbox, geometry=geom, h=262
        )
        by_district = aggregated.daily_by_district
        n_days = len(days)
        district_rows = _district_rows(
            districts,
            sparkline_for=lambda d: figures.sparkline_counts_svg(
                by_district.get(d.id, [0] * n_days)
            ),
        )

    n_total = meta.report_count
    n_infra = len({it.report_id for it in metrics.infra.items})
    window = f"{days[0]:%d %b} to {days[-1]:%d %b %Y}" if days else "no dated reports"
    countries = ", ".join(_COUNTRY_NAME.get(c, c) for c in meta.countries)

    infra_rows = figures.infra_damage_rows(metrics.infra.items)
    infra_bars_svg = figures.stacked_bars_svg(
        infra_rows, bold=frozenset(lbl for lbl, _ in infra_rows)
    )

    # Page-1 rail: top districts by blocked-access report count.
    debris_donut_rows = [
        (d.name, d.debris_yes)
        for d in sorted(districts, key=lambda d: d.debris_yes, reverse=True)
        if d.debris_yes > 0
    ][:5]
    debris_bars_svg = figures.debris_donut_svg(debris_donut_rows) if debris_donut_rows else None
    blind_n = sum(1 for c in cells if c.blind_spot)
    blind_spot_names = [d.name for d in districts if FLAG_BLIND_SPOT in d.flags]
    blind_note = (
        f" Most flagged cells fall in {' and '.join(blind_spot_names[:2])}."
        if blind_spot_names
        else ""
    )

    infra_by_district = _infra_by_district(metrics.infra.items, districts)
    n_severe_districts = sum(1 for d in districts if d.damage.severe and d.report_count >= 5)
    econ_low, econ_high = _econ_labels(metrics.impact)

    # Community voice: themes are already capped by the summariser; only split paragraphs.
    community = data.community
    headline_paragraphs = (
        [p.strip() for p in community.headline.headline.split("\n") if p.strip()]
        if community.headline
        else []
    )

    env = _build_env()
    template = env.get_template(_TEMPLATE_NAME)
    return template.render(
        meta=meta,
        crisis_display_name=_display_name(meta.crisis_name),
        crisis_type=meta.crisis_type or "Not specified",
        countries=countries,
        as_of_label=_as_of_label(meta),
        generated_label=_fmt_dt(datetime.now(UTC)),
        coverage_label=fmt_coverage_pct(meta.coverage_pct),
        window=window,
        n_total=n_total,
        damage=dmg,
        severe_share=dmg.severe_share,
        debris_yes=debris_yes,
        debris_known=debris_known,
        n_infra=n_infra,
        narrative=data.narrative,
        # page-1 figures
        hero_svg=hero_svg,
        timeline_svg=timeline_svg,
        timeline_status=timeline_status,
        infra_bars_svg=infra_bars_svg,
        debris_bars_svg=debris_bars_svg,
        kpi_mix_svg=figures.mix_bar_svg(dmg.minimal, dmg.partial, dmg.complete, w=92, h=10),
        # damage geography
        choropleth_svg=choropleth_svg,
        debris_map_svg=debris_map_svg,
        # infra + priority
        facilities=figures.facility_rows(metrics.infra.items, districts, top=_MAX_FACILITIES),
        district_rows=district_rows,
        xtab=_xtab(districts, infra_by_district),
        damage_color=canvas.DAMAGE_COLOR,
        # impact
        impact=metrics.impact,
        econ_low=econ_low,
        econ_high=econ_high,
        n_severe_districts=n_severe_districts,
        # hotspots & blind spots
        h3_svg=h3_svg,
        h3_n=h3_n,
        h3_lo=canvas.ramp_yr(0.0),
        h3_hi=canvas.ramp_yr(1.0),
        blind_svg=blind_svg,
        blind_n=blind_n,
        blind_note=blind_note,
        undp_color=canvas.UNDP,
        # community voice
        community=community,
        headline_paragraphs=headline_paragraphs,
    )


def render_report_pdf(data: CrisisReportData) -> bytes:
    """Render the report to PDF bytes; FontConfiguration makes WeasyPrint honour @font-face."""
    html_str = build_html(data)
    css_text = _CSS_PATH.read_text(encoding="utf-8")
    font_config = FontConfiguration()
    document = HTML(string=html_str, base_url=str(_TEMPLATE_DIR))
    pdf: bytes | None = document.write_pdf(  # pyright: ignore[reportUnknownMemberType]
        stylesheets=[CSS(string=css_text, base_url=str(_TEMPLATE_DIR), font_config=font_config)],
        font_config=font_config,
    )
    if pdf is None:  # write_pdf(None) always returns bytes
        raise RuntimeError("WeasyPrint returned no PDF bytes")
    return pdf


__all__ = ["build_html", "render_report_pdf"]
