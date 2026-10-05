"""Judgement constants behind the crisis analysis report's estimates.

These are the softest numbers in the report, so estimates built on them are
always shown as ranges and defer to authoritative assessments (PDNA, IOM DTM).
"""

from __future__ import annotations

from typing import NamedTuple

from api.buildings.density_grid import DENSITY_GRID_STEP_DEG

# LitPop grid

# Cell size of the precomputed `litpop_value` grid, shared by
# api/scripts/build_litpop_grid.py and the runtime lookup. Must equal the density
# grid step: `fetch_impact` divides a LitPop cell's value by the building count
# of the matching density cell. Changing it invalidates a built grid.
LITPOP_GRID_STEP_DEG = DENSITY_GRID_STEP_DEG

# Cell size of the precomputed `population_value` grid (WorldPop 100m pixels
# summed into cells), shared by api/scripts/build_population_grid.py and the runtime
# lookup. Must equal the density grid step for the same reason as LitPop.
POPULATION_GRID_STEP_DEG = DENSITY_GRID_STEP_DEG


# Damage ratios


class Range(NamedTuple):
    """A low-high band. The report never emits a point figure for an estimate."""

    low: float
    high: float


# Fraction of a building's asset value lost per damage class, loosely after
# PDNA/HAZUS mean damage ratios and widened for a 3-class scheme. `complete`
# stays below 1.0 because land and foundation value usually survive.
DAMAGE_RATIO: dict[str, Range] = {
    "minimal": Range(0.02, 0.15),
    "partial": Range(0.25, 0.55),
    "complete": Range(0.75, 1.00),
}


# Displaced-people estimate

# Fallback occupants per displacing building, used only where the WorldPop grid
# has no coverage for the AOI.
OCCUPANTS_PER_BUILDING = Range(3.0, 6.0)
DISPLACING_DAMAGE_CLASSES: frozenset[str] = frozenset({"partial", "complete"})

# Share of a building's occupants displaced per damage class, applied to the
# cell's population / building count. Summed over a cell it cannot exceed the
# cell's population. `minimal` counts here as temporary displacement.
DISPLACEMENT_FRACTION: dict[str, Range] = {
    "minimal": Range(0.1, 0.2),
    "partial": Range(0.3, 0.6),
    "complete": Range(0.9, 1.0),
}


# Spatial statistics

# k for the kNN weights shared by the coverage residuals and Gi* hotspots.
SPATIAL_NEIGHBOURS_K = 8

# Two-sided Gi* significance threshold (about 95% before multiplicity).
HOTSPOT_Z_THRESHOLD = 1.96


# Coverage gaps / blind spots

# A cell is under-reported when its local Poisson residual is below this.
BLIND_SPOT_RESIDUAL_Z = -2.0

# A blind spot must also sit near damage: its neighbourhood's smoothed damage
# rate must clear this percentile of the crisis-wide distribution. Otherwise the
# silence is just outside the event.
BLIND_SPOT_DAMAGE_CONTEXT_PCTL = 60.0


# Priority-district flags

# Severe: any confirmed collapse, or partial+complete share at or above this.
SEVERE_DAMAGE_SHARE = 0.40

# Blocked: share of reports flagging debris or blocked access.
BLOCKED_DEBRIS_SHARE = 0.30

# Thresholds at which a scorecard component counts as a red cell.
RED_SEVERE_DAMAGE_SHARE = 0.25  # ≥25% partial/complete
RED_COVERAGE_RESIDUAL_Z = BLIND_SPOT_RESIDUAL_Z  # significantly under-reported
RED_SERVICES_HIT = 1  # ≥1 damaged/non-functional critical-infra report


# Priority-district ranking

# Weighted score over min-max-normalised report volume, debris count and
# severity. The flags above are shown as badges but do not affect rank. Only the
# weights' relative size matters.
PRIORITY_WEIGHT_REPORTS = 0.5
PRIORITY_WEIGHT_DEBRIS = 0.25
PRIORITY_WEIGHT_SEVERITY = 0.25

# Severity points per report by damage class, summed per district.
SEVERITY_POINTS: dict[str, int] = {"minimal": 1, "partial": 2, "complete": 3}


# Report-map scale switch

# Above this many reports the builder skips the raw-point pull and renders
# SQL-aggregated density maps over the 0.02° mesh instead. The numeric figures
# are SQL-aggregated either way, so nothing is capped or sampled.
REPORT_POINTS_THRESHOLD = 10_000
