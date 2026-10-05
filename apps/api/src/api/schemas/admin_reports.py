"""Coordinator report schemas, including fields hidden from citizens and the public."""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict

from api.schemas.admin_search import LocationSource
from api.schemas.common import DamageClass, Debris, LocationOut
from api.schemas.public_reports import PublicReportListItem
from api.schemas.reports import PhotoMetadata


class ReportQualityOut(BaseModel):
    """From report_quality; defaults until the scoring job has run."""

    confidence_score: float | None = None
    points: int | None = None
    relevance_label: str | None = None
    damage_agreement: bool | None = None
    photo_fresh: bool | None = None
    gps_pin_match: bool | None = None
    corroborators_500m: int | None = None
    reporter_reputation: int | None = None
    verified: bool = False
    verified_at: datetime | None = None
    is_duplicate_image: bool = False
    computed_at: datetime | None = None


class AdminReportListItem(PublicReportListItem):
    """Shared by map and list modes.

    location is the submitted GPS; map_point is coalesce(location, building
    centroid, AI geocode), and location_source says which one was used.
    """

    # The public base forbids extra fields; coordinator rows may carry more.
    model_config = ConfigDict(extra="ignore")

    route_description: str | None
    client_id: uuid.UUID | None
    client_submission_id: uuid.UUID | None
    # The radius/area/confidence fields are set only for ai_geocode points.
    location_source: LocationSource | None = None
    location_radius_m: float | None = None
    location_area_only: bool = False
    location_confidence: float | None = None
    photo_path: str | None
    # 0 to 1; None until scored.
    confidence_score: float | None = None
    verified: bool = False


class ReporterReputationOut(BaseModel):
    """The submitter's track record across all reports with the same client_id."""

    total_reports: int
    quality_reports: int  # points >= the quality bar
    verified_count: int
    badge_count: int
    badge_slugs: list[str]


class VerifyReportRequest(BaseModel):
    verified: bool


class VerifyReportResponse(BaseModel):
    report_id: uuid.UUID
    verified: bool
    confidence_score: float
    reporter_stats: ReporterReputationOut | None = None


class AdminReportListResponse(BaseModel):
    """List mode pages with next_cursor; map mode sets truncated and total_in_bbox."""

    items: list[AdminReportListItem]
    next_cursor: str | None = None
    truncated: bool = False
    total_in_bbox: int | None = None


class AdminReportDetailResponse(BaseModel):
    """Full row plus a short-lived signed photo URL; re-fetch to refresh it."""

    id: uuid.UUID
    crisis_id: uuid.UUID
    damage_class: DamageClass
    description: str | None
    route_description: str | None
    infra_type: list[str] | None
    infra_name: str | None
    crisis_type: str | None
    crisis_type_detailed: str | None
    debris: Debris | None
    building_id: uuid.UUID | None
    # Overture footprint name; usually None, and the UI falls back to infra_name.
    building_name: str | None = None
    client_id: uuid.UUID | None
    client_submission_id: uuid.UUID | None
    location: LocationOut | None
    building_centroid: LocationOut | None
    location_source: LocationSource | None = None
    photo_path: str | None
    photo_url: str | None
    created_at: datetime
    photo_metadata: PhotoMetadata | None = None
    # Translation status: pending, ready, passthrough, skipped or failed.
    # None until the enrichment job has run.
    description_lang: str | None = None
    description_en: str | None = None
    description_status: str | None = None
    route_description_lang: str | None = None
    route_description_en: str | None = None
    route_description_status: str | None = None
    # Caption status: pending, ready, failed or skipped (no photo).
    ai_caption: str | None = None
    ai_caption_status: str | None = None
    quality: ReportQualityOut | None = None
    # Mirrored from quality for convenience.
    confidence_score: float | None = None
    verified: bool = False
    reporter_stats: ReporterReputationOut | None = None
