"""Live crisis-analysis metrics: the report's deterministic parts, without the
LLM narrative or raw points. Coordinator-only, so one schema per shape."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel

from api.analysis.metrics import ReportMetrics


class BBoxModel(BaseModel):
    min_lon: float
    min_lat: float
    max_lon: float
    max_lat: float


class DamageMixModel(BaseModel):
    minimal: int
    partial: int
    complete: int


class AnalysisMetaModel(BaseModel):
    crisis_id: uuid.UUID
    crisis_name: str
    crisis_type: str | None
    countries: list[str]
    as_of: datetime
    bbox: BBoxModel | None
    # Simplified AOI outline; None when the crisis has no geometry.
    geometry: dict[str, Any] | None
    report_count: int
    device_count: int
    building_count: int
    reported_building_count: int
    coverage_pct: float


class DistrictModel(BaseModel):
    id: str
    name: str
    official: bool
    centroid_lat: float
    centroid_lon: float
    report_count: int
    damage: DamageMixModel
    exposure_buildings: int
    debris_yes: int
    debris_known: int
    services_hit: int
    coverage_residual_z: float
    flags: list[str]
    red_components: list[str]
    tier: int
    rank: int
    priority_score: float
    severity_score: float
    reachability_score: float
    geom: dict[str, Any] | None


class MeshCellModel(BaseModel):
    """Coverage and impact for one mesh cell; economic values are None where
    LitPop has no coverage."""

    lat: float
    lon: float
    observed: int
    expected: float
    residual_z: float
    gi_z: float
    blind_spot: bool
    affected_buildings: int
    displaced_low: int
    displaced_high: int
    economic_available: bool
    economic_low_usd: float | None
    economic_high_usd: float | None


class ImpactHeadlineModel(BaseModel):
    """Crisis-wide impact ranges."""

    affected_buildings: int
    displaced_low: int
    displaced_high: int
    population_available: bool  # displaced came from WorldPop, not the 3-6 fallback
    economic_available: bool
    economic_low_usd: float | None
    economic_high_usd: float | None
    notes: list[str]


class HotspotModel(BaseModel):
    hot_cell_count: int
    cold_cell_count: int
    significance_z: float


class AnalysisMetricsResponse(BaseModel):
    meta: AnalysisMetaModel
    damage: DamageMixModel
    priority_districts: list[DistrictModel]
    cells: list[MeshCellModel]
    blind_spot_district_ids: list[str]
    observed_total: int
    expected_total: float
    hotspots: HotspotModel
    infra_by_type: dict[str, int]
    impact: ImpactHeadlineModel

    @classmethod
    def from_metrics(cls, m: ReportMetrics) -> AnalysisMetricsResponse:
        """Explicit mapping, so dropping the narrative and raw points is visible."""
        meta = m.meta
        return cls(
            meta=AnalysisMetaModel(
                crisis_id=meta.crisis_id,
                crisis_name=meta.crisis_name,
                crisis_type=meta.crisis_type,
                countries=meta.countries,
                as_of=meta.as_of,
                bbox=(
                    BBoxModel(
                        min_lon=meta.bbox.min_lon,
                        min_lat=meta.bbox.min_lat,
                        max_lon=meta.bbox.max_lon,
                        max_lat=meta.bbox.max_lat,
                    )
                    if meta.bbox
                    else None
                ),
                geometry=meta.geometry,
                report_count=meta.report_count,
                device_count=meta.device_count,
                building_count=meta.building_count,
                reported_building_count=meta.reported_building_count,
                coverage_pct=meta.coverage_pct,
            ),
            damage=DamageMixModel(
                minimal=m.damage.minimal, partial=m.damage.partial, complete=m.damage.complete
            ),
            priority_districts=[
                DistrictModel(
                    id=d.id,
                    name=d.name,
                    official=d.official,
                    centroid_lat=d.centroid_lat,
                    centroid_lon=d.centroid_lon,
                    report_count=d.report_count,
                    damage=DamageMixModel(
                        minimal=d.damage.minimal,
                        partial=d.damage.partial,
                        complete=d.damage.complete,
                    ),
                    exposure_buildings=d.exposure_buildings,
                    debris_yes=d.debris_yes,
                    debris_known=d.debris_known,
                    services_hit=d.services_hit,
                    coverage_residual_z=d.coverage_residual_z,
                    flags=d.flags,
                    red_components=d.red_components,
                    tier=d.tier,
                    rank=d.rank,
                    priority_score=d.priority_score,
                    severity_score=d.severity_score,
                    reachability_score=d.reachability_score,
                    geom=d.geom,
                )
                for d in m.priority_districts
            ],
            cells=[
                MeshCellModel(
                    lat=c.lat,
                    lon=c.lon,
                    observed=c.observed,
                    expected=c.expected,
                    residual_z=c.residual_z,
                    gi_z=c.gi_z,
                    blind_spot=c.blind_spot,
                    affected_buildings=c.affected_buildings,
                    displaced_low=c.displaced_low,
                    displaced_high=c.displaced_high,
                    economic_available=c.economic_available,
                    economic_low_usd=c.economic_low_usd,
                    economic_high_usd=c.economic_high_usd,
                )
                for c in m.coverage.cells
            ],
            blind_spot_district_ids=m.coverage.blind_spot_district_ids,
            observed_total=m.coverage.observed_total,
            expected_total=m.coverage.expected_total,
            hotspots=HotspotModel(
                hot_cell_count=m.hotspots.hot_cell_count,
                cold_cell_count=m.hotspots.cold_cell_count,
                significance_z=m.hotspots.significance_z,
            ),
            infra_by_type=m.infra.by_type,
            impact=ImpactHeadlineModel(
                affected_buildings=m.impact.affected_buildings,
                displaced_low=m.impact.displaced_low,
                displaced_high=m.impact.displaced_high,
                population_available=m.impact.population_available,
                economic_available=m.impact.economic_available,
                economic_low_usd=m.impact.economic_low_usd,
                economic_high_usd=m.impact.economic_high_usd,
                notes=m.impact.notes,
            ),
        )
