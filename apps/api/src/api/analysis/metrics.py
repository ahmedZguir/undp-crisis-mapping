"""Frozen dataclasses passed between the report builder, narrative layer and renderer."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any

# Cover


@dataclass(frozen=True)
class BBox:
    """AOI bounding box, for framing the maps. Lon/lat degrees."""

    min_lon: float
    min_lat: float
    max_lon: float
    max_lat: float


@dataclass(frozen=True)
class ReportPoint:
    """One report's location and attributes for the point maps; nothing identifies the reporter."""

    lon: float
    lat: float
    damage_class: str
    debris: str | None
    created_at: datetime
    infra_type: list[str]
    infra_name: str | None


@dataclass(frozen=True)
class DamageMix:
    """Counts by damage class. Used at every altitude (crisis, district)."""

    minimal: int = 0
    partial: int = 0
    complete: int = 0

    @property
    def total(self) -> int:
        return self.minimal + self.partial + self.complete

    @property
    def severe(self) -> int:
        """partial + complete."""
        return self.partial + self.complete

    @property
    def severe_share(self) -> float:
        return self.severe / self.total if self.total else 0.0


@dataclass(frozen=True)
class ReportMeta:
    """Cover-page identity and coverage denominator."""

    crisis_id: uuid.UUID
    crisis_name: str
    crisis_type: str | None
    countries: list[str]
    as_of: datetime
    bbox: BBox | None

    report_count: int
    device_count: int  # distinct reporting devices, a crude reach proxy
    building_count: int  # building stock in the AOI (the coverage denominator)
    reported_building_count: int  # distinct buildings with ≥1 report

    # Simplified AOI polygon as GeoJSON; None when the crisis has no geometry.
    geometry: dict[str, Any] | None = None

    @property
    def coverage_pct(self) -> float:
        """% of building stock with at least one report."""
        if self.building_count <= 0:
            return 0.0
        return 100.0 * self.reported_building_count / self.building_count


# Priority districts

# District flags. BLIND_SPOT calls for reconnaissance rather than response.
FLAG_SEVERE = "severe"
FLAG_BLOCKED = "blocked"
FLAG_BLIND_SPOT = "blind_spot"

# Scorecard components, shown side by side.
COMPONENT_DAMAGE = "damage"
COMPONENT_EXPOSURE = "exposure"
COMPONENT_REACHABILITY = "reachability"
COMPONENT_SERVICES = "services"
COMPONENT_COVERAGE = "coverage"


@dataclass(frozen=True)
class District:
    """One ranked area with its scorecard and severity/reachability placement."""

    id: str  # Overture division id, or "area:<n>" for a grid-cell fallback blob
    name: str
    official: bool  # False for a grid-cell fallback label ("Area A1")
    centroid_lat: float
    centroid_lon: float

    report_count: int
    damage: DamageMix
    exposure_buildings: int  # building stock in the district
    debris_yes: int
    debris_known: int  # yes + no (the debris denominator)
    services_hit: int  # damaged/non-functional critical-infra reports
    coverage_residual_z: float  # district-level local Poisson residual

    flags: list[str]  # subset of {severe, blocked, blind_spot}
    red_components: list[str]  # which of the five components are "red"
    tier: int  # 0 if flagged severe or blocked, else 1; display only
    rank: int  # 1-based overall priority order
    priority_score: float  # weighted score that drives `rank`

    # both normalised 0..1 (1 = most severe / most reachable)
    severity_score: float
    reachability_score: float

    # Simplified division polygon as GeoJSON; None for the "Unmapped area" fallback.
    geom: dict[str, Any] | None = None

    @property
    def debris_blocked_share(self) -> float:
        return self.debris_yes / self.debris_known if self.debris_known else 0.0


# Coverage gaps and hotspots


@dataclass(frozen=True)
class MeshCell:
    """One analysis mesh cell backing the coverage, hotspot and impact maps.

    The impact fields are only filled for the live analysis tab; `economic_*_usd`
    is None where LitPop has no coverage.
    """

    lat: float
    lon: float
    observed: int  # reports in the cell
    expected: float  # locally-expected reports from exposure
    smoothed_rate: float  # EB-smoothed reporting/damage rate
    residual_z: float  # local Poisson residual (negative = under-reported)
    gi_z: float  # Getis-Ord Gi* z-score (positive = hot, negative = cold)
    blind_spot: bool

    # Blocked-access reports, for the aggregated debris-density map.
    debris_yes: int = 0

    # impact enrichment
    affected_buildings: int = 0  # partial/complete damaged buildings in the cell
    displaced_low: int = 0
    displaced_high: int = 0
    economic_available: bool = False
    economic_low_usd: float | None = None
    economic_high_usd: float | None = None


@dataclass(frozen=True)
class CoverageMetrics:
    """Where reports are sparse relative to expected building stock."""

    coverage_pct: float
    observed_total: int
    expected_total: float
    blind_spot_district_ids: list[str]
    cells: list[MeshCell]  # the full mesh, shared with hotspots


@dataclass(frozen=True)
class HotspotMetrics:
    """Statistically significant damage clusters (Gi* on smoothed rates)."""

    hot_cell_count: int
    cold_cell_count: int
    significance_z: float  # the threshold used (for the inline caveat)


# Critical infrastructure


@dataclass(frozen=True)
class InfraItem:
    report_id: uuid.UUID
    infra_type: str
    name: str | None
    damage_class: str
    debris: str | None
    district_name: str | None
    lat: float | None = None
    lon: float | None = None


@dataclass(frozen=True)
class InfraMetrics:
    """Damaged or non-functional critical infrastructure."""

    items: list[InfraItem]
    by_type: dict[str, int]  # damaged/non-functional count per infra type


# Impact estimates


@dataclass(frozen=True)
class ImpactEstimates:
    """Displaced-people and economic-damage ranges.

    `population_available` is False when the displaced band fell back to the
    occupancy assumption; `economic_available` is False without LitPop coverage.
    """

    affected_buildings: int  # partial + complete buildings
    displaced_low: int
    displaced_high: int
    population_available: bool

    economic_available: bool
    economic_low_usd: float | None
    economic_high_usd: float | None

    notes: list[str]  # inline caveats (assumptions + deferral to PDNA/DTM)


# Community summary


@dataclass(frozen=True)
class CommunityTheme:
    """One clustered theme: a short topic label, its size, and one example.

    `quote` is the English-translated description of the example report
    (the report is English-only); None when no usable text was available.
    """

    label: str
    count: int
    quote: str | None = None


@dataclass(frozen=True)
class CrisisHeadline:
    """Sampled crisis-wide headline and its strongest themes."""

    headline: str  # synthesized 2-4 sentence paragraph (or small-N prose pass)
    themes: list[CommunityTheme]
    sampled: int  # reports fed to the summariser
    total: int  # total reports in the crisis (the sampling denominator)
    other_count: int = 0  # sampled reports not in a theme (embed-queue orphans)


@dataclass(frozen=True)
class DistrictThemes:
    """One priority district's short write-up and its themes."""

    district_id: str
    district_name: str
    rank: int
    write_up: str  # short per-district summary, grounded in its stats + themes
    themes: list[CommunityTheme]
    sampled: int
    report_count: int  # the district's full report count (the sampling denominator)
    other_count: int = 0


@dataclass(frozen=True)
class CommunitySummary:
    """Crisis headline plus top-district themes; empty when no summary could be produced."""

    headline: CrisisHeadline | None
    districts: list[DistrictThemes] = field(default_factory=list["DistrictThemes"])


# Narrative


@dataclass(frozen=True)
class Narrative:
    """BLUF and recommended actions, plus optional per-section prose."""

    bottom_line: str
    recommended_actions: list[str]
    section_prose: dict[str, str]  # keyed by section id; may be empty


# Aggregated views for large crises


@dataclass(frozen=True)
class DayCount:
    """Reports submitted on one day, split by damage class."""

    day: date
    minimal: int = 0
    partial: int = 0
    complete: int = 0

    @property
    def total(self) -> int:
        return self.minimal + self.partial + self.complete


@dataclass(frozen=True)
class AggregateViews:
    """SQL-aggregated stand-ins for `ReportPoint`s above `REPORT_POINTS_THRESHOLD`.

    `daily_by_district` is keyed by `District.id`, each list aligned to `days`.
    """

    daily: list[DayCount]
    daily_by_district: dict[str, list[int]]
    debris_yes: int
    debris_known: int

    @property
    def days(self) -> list[date]:
        return [d.day for d in self.daily]


# Bundles


@dataclass(frozen=True)
class ReportMetrics:
    """The deterministic part of the report, no LLM involved."""

    meta: ReportMeta
    damage: DamageMix
    priority_districts: list[District]
    coverage: CoverageMetrics
    hotspots: HotspotMetrics
    infra: InfraMetrics
    impact: ImpactEstimates
    # Below `REPORT_POINTS_THRESHOLD`: every located report, for the point maps.
    # Above it: empty, and `aggregated` carries the density-map substitutes.
    points: list[ReportPoint] = field(default_factory=list[ReportPoint])
    aggregated: AggregateViews | None = None


@dataclass(frozen=True)
class CrisisReportData:
    """Everything the renderer needs: deterministic metrics + grounded prose."""

    metrics: ReportMetrics
    narrative: Narrative
    community: CommunitySummary
