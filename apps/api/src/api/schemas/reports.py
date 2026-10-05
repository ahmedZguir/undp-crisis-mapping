"""Citizen-facing report schemas: submit, history, deletion, photo EXIF."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator
from pydantic.alias_generators import to_camel

from api.schemas.common import (
    CrisisStatus,
    DamageClass,
    Debris,
    LocationIn,
    LocationOut,
)

# Photo EXIF metadata. The PWA reads it before its canvas re-encode strips the
# tags, so the server cannot recover it from the bytes. Coordinator-only on output.


# camelCase on the wire to match the PWA's PhotoMetadata type; snake_case
# names are accepted too.
_CAMEL = ConfigDict(alias_generator=to_camel, populate_by_name=True)


class PhotoGps(BaseModel):
    model_config = _CAMEL

    latitude: float = Field(ge=-90, le=90)
    longitude: float = Field(ge=-180, le=180)
    # EXIF GPSHPositioningError in metres; phones rarely set it.
    accuracy: float | None = None


class PhotoCamera(BaseModel):
    model_config = _CAMEL

    make: str | None = None
    model: str | None = None
    software: str | None = None


class PhotoMetadata(BaseModel):
    """captured_at is not checked against the clock: late submission and camera skew are normal."""

    model_config = _CAMEL

    gps: PhotoGps | None = None
    captured_at: datetime | None = None
    orientation: int | None = None
    width: int | None = None
    height: int | None = None
    camera: PhotoCamera | None = None
    extracted_at: datetime


# Submit


class ReportSubmitPayload(BaseModel):
    crisis_id: uuid.UUID
    damage_class: DamageClass
    description: str | None = None
    # Typed directions when there is no GPS pin. The PWA caps it at 300 chars.
    route_description: str | None = Field(default=None, max_length=1000)
    location: LocationIn | None = None
    # Per-install UUID the user can reset; not a device fingerprint.
    client_id: uuid.UUID | None = None
    # Open vocabulary; [] normalizes to None.
    infra_type: list[str] | None = None
    infra_name: str | None = None
    crisis_type: str | None = None
    crisis_type_detailed: str | None = None
    debris: Debris | None = None
    # Overture GERS id, resolved server-side to buildings.id.
    building_id: str | None = None
    # Idempotency key: one per outbox draft, resent on every retry.
    client_submission_id: uuid.UUID | None = None
    # Honeypot: the PWA never sets it; when present the route returns a fake 200.
    message: str | None = Field(default=None, exclude=True, description="reserved")
    photo_metadata: PhotoMetadata | None = None
    # Form version the draft was filled against, even if a newer one is published since.
    form_version: int | None = None
    # Keyed by question id; a list for multi_select.
    generic_answers: dict[str, str | list[str]] | None = None

    @model_validator(mode="after")
    def _normalize_infra_type(self) -> ReportSubmitPayload:
        if self.infra_type is not None and len(self.infra_type) == 0:
            self.infra_type = None
        return self

    @model_validator(mode="after")
    def _normalize_route_description(self) -> ReportSubmitPayload:
        if self.route_description is not None:
            trimmed = self.route_description.strip()
            self.route_description = trimmed or None
        return self

    @model_validator(mode="after")
    def _normalize_description(self) -> ReportSubmitPayload:
        # Blank counts as no description. The content gate itself lives in the
        # service so the honeypot check runs first.
        if self.description is not None:
            trimmed = self.description.strip()
            self.description = trimmed or None
        return self


class ReportCreatedResponse(BaseModel):
    id: uuid.UUID
    crisis_id: uuid.UUID
    created_at: datetime
    damage_class: DamageClass
    description: str | None = None
    route_description: str | None = None
    location: LocationOut | None = None
    infra_type: list[str] | None = None
    infra_name: str | None = None
    crisis_type: str | None = None
    crisis_type_detailed: str | None = None
    debris: Debris | None = None
    # Null when the GERS id was absent or unknown.
    building_id: uuid.UUID | None = None


# Citizen history


class ReportPointFactor(BaseModel):
    """One points check; "na" for checks that do not apply (e.g. photo checks without a photo)."""

    key: str
    state: Literal["earned", "missed", "na"]


class CitizenReportQuality(BaseModel):
    """scored is False, with no factors, until the scoring pipeline has run."""

    scored: bool
    points: int
    max_points: int = 5
    verified: bool
    is_duplicate_image: bool
    factors: list[ReportPointFactor]


class CitizenReportHistoryItem(BaseModel):
    """One row of GET /reports?client_id=..., scoped by client_id."""

    id: uuid.UUID
    crisis_id: uuid.UUID
    crisis_name: str
    crisis_status: CrisisStatus
    created_at: datetime
    damage_class: DamageClass
    description: str | None = None
    route_description: str | None = None
    location: LocationOut | None = None
    infra_type: list[str] | None = None
    infra_name: str | None = None
    crisis_type: str | None = None
    crisis_type_detailed: str | None = None
    debris: Debris | None = None
    building_id: uuid.UUID | None = None
    # Signed per request; None if the blob is missing, so one row degrades, not the feed.
    photo_url: str | None = None
    client_submission_id: uuid.UUID | None = None
    # None when the report has no report_quality row.
    quality: CitizenReportQuality | None = None


class CitizenReportHistoryResponse(BaseModel):
    items: list[CitizenReportHistoryItem]
    # len(items); the feed is capped, so this is not a true total.
    total: int


# Citizen deletion


class ReportDeleteRequest(BaseModel):
    """Anyone holding the client_id can delete its reports."""

    model_config = ConfigDict(extra="forbid")
    client_id: uuid.UUID


class ReportDeleteResponse(BaseModel):
    """Always {"status": "ok"}, whether or not anything matched, so the delete
    routes cannot be used to probe which ids exist."""

    status: Literal["ok"] = "ok"


class ReportDeleteByIdRequest(BaseModel):
    """Deletes only when the report's client_id matches."""

    model_config = ConfigDict(extra="forbid")
    client_id: uuid.UUID
