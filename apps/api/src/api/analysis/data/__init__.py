"""Database queries for the crisis analysis report.

All bucketing and counting happens in SQL; only per-cell and per-district
aggregates come back to Python.
"""

from api.analysis.data.geography import (
    CellRow,
    CoverRow,
    CrisisRow,
    DistrictRow,
    fetch_cover,
    fetch_crisis,
    fetch_districts,
    fetch_division_for_cells,
    fetch_mesh_cells,
)
from api.analysis.data.impact import ImpactCellRow, ImpactRow, fetch_impact, fetch_impact_cells
from api.analysis.data.reports import (
    DailyCountRow,
    DivisionDayRow,
    InfraRow,
    ReportPointRow,
    fetch_daily_by_division,
    fetch_daily_counts,
    fetch_debris_totals,
    fetch_infra,
    fetch_report_points,
)
from api.analysis.data.sql import CRITICAL_INFRA_TYPES

__all__ = [
    "CRITICAL_INFRA_TYPES",
    "CellRow",
    "CoverRow",
    "CrisisRow",
    "DailyCountRow",
    "DistrictRow",
    "DivisionDayRow",
    "ImpactCellRow",
    "ImpactRow",
    "InfraRow",
    "ReportPointRow",
    "fetch_cover",
    "fetch_crisis",
    "fetch_daily_by_division",
    "fetch_daily_counts",
    "fetch_debris_totals",
    "fetch_districts",
    "fetch_division_for_cells",
    "fetch_impact",
    "fetch_impact_cells",
    "fetch_infra",
    "fetch_mesh_cells",
    "fetch_report_points",
]
