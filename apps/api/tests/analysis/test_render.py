"""Golden smoke tests for the PDF renderer (`api.analysis.render`).

No DB, no LLM: the bundle is hand-constructed so these are fully offline and
deterministic. They assert two things — that `render_report_pdf` produces a
real, multi-page PDF, and that `build_html` places the key section headings,
chart SVGs and a district name. The structural correctness of the metrics is
covered elsewhere (`test_builder_components`, `test_spatial`).
"""

from __future__ import annotations

import dataclasses
import uuid
from datetime import UTC, date, datetime, timedelta
from typing import Any, cast

import fitz  # type: ignore[reportMissingTypeStubs]  # pymupdf — counts pages

from api.analysis.metrics import (
    COMPONENT_DAMAGE,
    COMPONENT_REACHABILITY,
    COMPONENT_SERVICES,
    FLAG_BLIND_SPOT,
    FLAG_BLOCKED,
    FLAG_SEVERE,
    AggregateViews,
    BBox,
    CommunitySummary,
    CommunityTheme,
    CoverageMetrics,
    CrisisHeadline,
    CrisisReportData,
    DamageMix,
    DayCount,
    District,
    DistrictThemes,
    HotspotMetrics,
    ImpactEstimates,
    InfraItem,
    InfraMetrics,
    MeshCell,
    Narrative,
    ReportMeta,
    ReportMetrics,
    ReportPoint,
)
from api.analysis.render import build_html, render_report_pdf

_DISTRICT_NAME = "Al Rayyan"
_BLIND_DISTRICT_NAME = "Umm Salal"


def _square(lon: float, lat: float, d: float = 0.06) -> dict[str, Any]:
    """A simple square GeoJSON polygon around a centroid, for the map fixtures."""
    return {
        "type": "Polygon",
        "coordinates": [
            [
                [lon - d, lat - d],
                [lon + d, lat - d],
                [lon + d, lat + d],
                [lon - d, lat + d],
                [lon - d, lat - d],
            ]
        ],
    }


def _sample_points() -> list[ReportPoint]:
    """A spread of dated report points so the hero/debris/H3/timeline maps and
    the per-district sparklines all have something to draw."""
    base = datetime(2026, 6, 1, 8, 0, tzinfo=UTC)
    pts: list[ReportPoint] = []
    for i in range(24):
        cls = ("minimal", "partial", "complete")[i % 3]
        pts.append(
            ReportPoint(
                lon=51.4 + 0.012 * (i % 5 - 2),
                lat=25.3 + 0.012 * (i % 4 - 1),
                damage_class=cls,
                debris="yes" if i % 4 == 0 else "no",
                created_at=base + timedelta(days=i // 4),
                infra_type=["utility"] if i % 5 == 0 else [],
                infra_name=None,
            )
        )
    return pts


def _sample_data() -> CrisisReportData:
    """A small but representative bundle exercising every section path."""
    bbox = BBox(min_lon=51.0, min_lat=25.0, max_lon=51.6, max_lat=25.6)
    meta = ReportMeta(
        crisis_id=uuid.uuid4(),
        crisis_name="test floods 2026",  # lowercase → exercises display title-casing
        crisis_type="flood",
        countries=["QA"],
        as_of=datetime(2026, 6, 3, 14, 0, tzinfo=UTC),
        bbox=bbox,
        report_count=420,
        device_count=180,
        building_count=500_000,
        reported_building_count=190,
    )

    severe = District(
        id="div:rayyan",
        name=_DISTRICT_NAME,
        official=True,
        centroid_lat=25.3,
        centroid_lon=51.4,
        report_count=137,
        damage=DamageMix(minimal=57, partial=54, complete=26),
        exposure_buildings=197_057,
        debris_yes=38,
        debris_known=100,
        services_hit=12,
        coverage_residual_z=-0.4,
        flags=[FLAG_SEVERE, FLAG_BLOCKED],
        red_components=[COMPONENT_DAMAGE, COMPONENT_REACHABILITY, COMPONENT_SERVICES],
        tier=0,
        rank=1,
        priority_score=0.81,
        severity_score=0.72,
        reachability_score=0.62,
        geom=_square(51.4, 25.3),
    )
    blind = District(
        id="div:ummsalal",
        name=_BLIND_DISTRICT_NAME,
        official=True,
        centroid_lat=25.4,
        centroid_lon=51.2,
        report_count=7,
        damage=DamageMix(minimal=2, partial=4, complete=1),
        exposure_buildings=34_367,
        debris_yes=0,
        debris_known=0,
        services_hit=3,
        coverage_residual_z=-2.6,
        flags=[FLAG_SEVERE, FLAG_BLIND_SPOT],
        red_components=[COMPONENT_DAMAGE, COMPONENT_SERVICES],
        tier=0,
        rank=2,
        priority_score=0.34,
        severity_score=0.55,
        reachability_score=0.95,
        geom=_square(51.2, 25.4),
    )

    cells = [
        MeshCell(
            lat=25.0 + 0.04 * i,
            lon=51.0 + 0.03 * (i % 5),
            observed=i % 4,
            expected=2.0,
            smoothed_rate=0.3,
            residual_z=-2.5 + 0.4 * i,
            gi_z=-1.0 + 0.5 * i,
            blind_spot=(i == 2),
        )
        for i in range(12)
    ]
    coverage = CoverageMetrics(
        coverage_pct=meta.coverage_pct,
        observed_total=420,
        expected_total=500.0,
        blind_spot_district_ids=["div:ummsalal"],
        cells=cells,
    )
    hotspots = HotspotMetrics(hot_cell_count=3, cold_cell_count=5, significance_z=1.96)

    infra = InfraMetrics(
        items=[
            InfraItem(
                report_id=uuid.uuid4(),
                infra_type="utility",
                name="District Water Main",
                damage_class="complete",
                debris="no",
                district_name=None,
            ),
            InfraItem(
                report_id=uuid.uuid4(),
                infra_type="community",
                name="Al-Rayyan Clinic",
                damage_class="partial",
                debris="yes",
                district_name=None,
            ),
        ],
        by_type={"utility": 8, "community": 5, "transport": 2},
    )

    impact = ImpactEstimates(
        affected_buildings=129,
        displaced_low=387,
        displaced_high=774,
        population_available=True,
        economic_available=True,
        economic_low_usd=2_400_000.0,
        economic_high_usd=9_800_000.0,
        notes=[
            "Displaced range assumes 3-6 occupants per affected building.",
            "Economic range applies a coarse PDNA-style damage ratio per class.",
        ],
    )

    metrics = ReportMetrics(
        meta=meta,
        damage=DamageMix(minimal=59, partial=58, complete=27),
        priority_districts=[severe, blind],
        coverage=coverage,
        hotspots=hotspots,
        infra=infra,
        impact=impact,
        points=_sample_points(),
    )

    narrative = Narrative(
        bottom_line=(
            "Flood damage concentrates in two severe districts at under 0.1% coverage; "
            "Al Rayyan is blocked and Umm Salal is a blind spot."
        ),
        recommended_actions=[
            "Clear access into Al Rayyan before sending teams.",
            "Send a recon team to Umm Salal — do not assume it is safe.",
            "Prioritise restoring utility services (8 reports).",
        ],
        section_prose={},
    )

    community = CommunitySummary(
        headline=CrisisHeadline(
            headline="Shelter and water dominate; access obstruction blocks delivery.",
            themes=[
                CommunityTheme(
                    label="Shelter & displacement",
                    count=42,
                    quote="Our home is uninhabitable; where can we shelter?",
                ),
                CommunityTheme(
                    label="Water & sanitation",
                    count=27,
                    quote="Burst mains, no running water on our block.",
                ),
            ],
            sampled=200,
            total=420,
            other_count=18,
        ),
        districts=[
            DistrictThemes(
                district_id="div:Al Rayyan",
                district_name=_DISTRICT_NAME,
                rank=1,
                write_up="Severe structural damage with blocked access on main routes.",
                themes=[
                    CommunityTheme(
                        label="Collapsed homes",
                        count=20,
                        quote="The whole building came down overnight.",
                    ),
                ],
                sampled=137,
                report_count=137,
                other_count=5,
            ),
        ],
    )

    return CrisisReportData(metrics=metrics, narrative=narrative, community=community)


def test_render_report_pdf_is_multipage_pdf() -> None:
    pdf = render_report_pdf(_sample_data())

    assert pdf[:5] == b"%PDF-"
    assert len(pdf) > 20_000  # non-trivial; a near-empty PDF is a few KB

    doc = fitz.open(stream=pdf, filetype="pdf")
    try:
        page_count = cast("int", doc.page_count)
        # Part I + the page-broken Part II span at least two pages.
        assert page_count >= 2
    finally:
        doc.close()


def test_build_html_contains_key_sections_and_district() -> None:
    html = build_html(_sample_data())

    # v7 section headings (masthead title + the Part I / Part II sequence)
    for heading in (
        "Situation Analysis",
        "Bottom line",
        "Damage geography",
        "Infrastructure",
        "Priority districts",
        "Impact estimates",
        "Hotspots &amp; possible blind spots",
        "What communities are reporting",
    ):
        assert heading in html, f"missing section heading: {heading!r}"

    # a district name and a flag chip
    assert _DISTRICT_NAME in html
    assert _BLIND_DISTRICT_NAME in html
    assert "Blind spot" in html

    # narrative + community content made it through
    assert "Clear access into Al Rayyan" in html
    assert "Shelter &amp; displacement" in html

    # no em/en dashes survive into the report: the `nodash` filter rewrites the
    # em dash in the recommended-action fixture above to a comma.
    assert "\u2014" not in html
    assert "\u2013" not in html
    assert "Umm Salal, do not assume it is safe" in html

    # the maps/charts are inlined as SVG (hero, timeline, choropleth, debris, H3,
    # blind, plus per-district mix bars / sparklines / locators)
    assert html.count("<svg") >= 6

    # lowercase crisis name was title-cased for the masthead
    assert "Test Floods 2026" in html


def test_build_html_handles_empty_community_and_pending_economic() -> None:
    data = _sample_data()
    # rebuild with an empty community summary and economic-pending impact
    empty_community = CommunitySummary(headline=None, districts=[])
    pending_impact = ImpactEstimates(
        affected_buildings=129,
        displaced_low=387,
        displaced_high=774,
        population_available=False,
        economic_available=False,
        economic_low_usd=None,
        economic_high_usd=None,
        notes=["Economic estimate pending data."],
    )
    metrics = data.metrics
    new_metrics = ReportMetrics(
        meta=metrics.meta,
        damage=metrics.damage,
        priority_districts=metrics.priority_districts,
        coverage=metrics.coverage,
        hotspots=metrics.hotspots,
        infra=metrics.infra,
        impact=pending_impact,
    )
    html = build_html(
        CrisisReportData(metrics=new_metrics, narrative=data.narrative, community=empty_community)
    )

    assert "no community summary" in html.lower()
    assert "Pending data" in html


def test_build_html_uses_aggregated_density_above_threshold() -> None:
    """Above the report-points threshold the builder hands the renderer
    `aggregated` views and an empty `points` list; the point-level figures must
    fall through to the SQL-aggregated density maps without crashing on the
    missing points, and the rest of the report renders unchanged."""
    data = _sample_data()
    metrics = data.metrics

    # Give every cell reports + a few blocked-access reports so the hero density,
    # the cell-density map, and the blocked-access density map all draw.
    cells = [
        dataclasses.replace(c, observed=(i % 5) + 1, debris_yes=(i % 3))
        for i, c in enumerate(metrics.coverage.cells)
    ]
    coverage = dataclasses.replace(metrics.coverage, cells=cells)

    daily = [
        DayCount(day=date(2026, 6, 1) + timedelta(days=i), minimal=40, partial=25, complete=10)
        for i in range(5)
    ]
    aggregated = AggregateViews(
        daily=daily,
        daily_by_district={"div:rayyan": [3, 5, 2, 8, 1]},
        debris_yes=1200,
        debris_known=4000,
    )
    big_meta = dataclasses.replace(metrics.meta, report_count=50_000)
    new_metrics = dataclasses.replace(
        metrics, meta=big_meta, coverage=coverage, points=[], aggregated=aggregated
    )

    html = build_html(
        CrisisReportData(metrics=new_metrics, narrative=data.narrative, community=data.community)
    )

    # the aggregated density maps replaced the point maps (legend copy is unique
    # to them), and the report is otherwise intact
    assert "Report density: deeper = more reports" in html
    assert "Blocked access: deeper = more blocked" in html
    assert "Priority districts" in html
    assert _DISTRICT_NAME in html
    assert html.count("<svg") >= 6
