from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Annotated, Any, Literal

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, model_validator

from api.schemas.common import CrisisStatus, PublicVisibility

# The activation cron ticks once a minute, so a nearer activate_at would race it.
_SCHEDULE_MIN_FUTURE_DELTA = timedelta(minutes=1)


def check_scheduling_inputs(
    *,
    activate_at: datetime | None,
    activate_on_ingest_success: bool,
    target_status: CrisisStatus,
    geometry_present: bool | None,
) -> None:
    """Scheduling checks that need no DB state; shared by create and patch.

    geometry_present is None on PATCH, where the route checks crisis_jobs instead.
    A DB CHECK backs these up.
    """
    if activate_at is None and not activate_on_ingest_success:
        return

    if activate_at is not None and activate_on_ingest_success:
        raise ValueError(
            "Set either activate_at or activate_on_ingest_success, not both.",
        )

    if target_status != "inactive":
        raise ValueError(
            "Scheduling is only valid when status is 'inactive'.",
        )

    if activate_at is not None:
        now = datetime.now(UTC)
        if activate_at.tzinfo is None:
            activate_at = activate_at.replace(tzinfo=UTC)
        if activate_at - now < _SCHEDULE_MIN_FUTURE_DELTA:
            raise ValueError(
                "activate_at must be at least 1 minute in the future.",
            )

    if activate_on_ingest_success and geometry_present is False:
        raise ValueError(
            "activate_on_ingest_success requires the crisis to have a "
            "geometry; an ingest job cannot run without one.",
        )


def _require_exactly_one(model: BaseModel, fields: tuple[str, ...], message: str) -> None:
    if sum(1 for f in fields if getattr(model, f) is not None) != 1:
        raise ValueError(message)


def _empty_to_none(value: list[str] | None) -> list[str] | None:
    return value or None


# Public infra-type whitelist for buildings/full modes; None (or []) means no filter.
InfraTypes = Annotated[list[str] | None, AfterValidator(_empty_to_none)]


class CrisisListItem(BaseModel):
    id: uuid.UUID
    name: str
    # Building footprint tiles; null until the building ingest has run.
    pmtiles_url: str | None = None
    overture_release_pinned: str | None = None
    # WGS84 MultiPolygon; null on the reserved Other / Unspecified crisis.
    geometry: dict[str, Any] | None = None
    # The PWA picks its public map renderer from this before the first fetch.
    public_visibility: PublicVisibility


# Crisis create


BBox = Annotated[
    tuple[float, float, float, float],
    Field(
        description=("Bounding box as `[xmin, ymin, xmax, ymax]` in WGS84 lon/lat."),
    ),
]


class CrisisGeometryPart(BaseModel):
    """One part of a multi-area union: an Overture division id or inline GeoJSON."""

    division_id: str | None = Field(default=None, min_length=1)
    polygon: dict[str, Any] | None = None

    @model_validator(mode="after")
    def _exactly_one(self) -> CrisisGeometryPart:
        _require_exactly_one(
            self,
            ("division_id", "polygon"),
            "each part must specify exactly one of division_id, polygon",
        )
        return self


class CrisisGeometryInput(BaseModel):
    """Exactly one of bbox, polygon, division_id (Overture GERS id) or parts.

    The resolver normalizes every form to the stored MultiPolygon.
    """

    bbox: BBox | None = None
    polygon: dict[str, Any] | None = None
    division_id: str | None = Field(default=None, min_length=1)
    parts: list[CrisisGeometryPart] | None = Field(default=None, min_length=1)

    @model_validator(mode="after")
    def _exactly_one(self) -> CrisisGeometryInput:
        _require_exactly_one(
            self,
            ("bbox", "polygon", "division_id", "parts"),
            "geometry must specify exactly one of bbox, polygon, division_id, parts",
        )
        return self


class CrisisCreatePayload(BaseModel):
    # Keeps list rows and map labels from overflowing; the PWA form mirrors it.
    name: str = Field(min_length=1, max_length=60)
    # Hidden from citizens until a coordinator activates it.
    status: CrisisStatus = "inactive"
    geometry: CrisisGeometryInput | None = None
    type: str = Field(min_length=1)
    # None seeds countries from the resolved geometry; an explicit [] stays empty.
    countries: list[str] | None = None
    started_at: datetime | None = None
    ended_at: datetime | None = None
    activate_at: datetime | None = None
    activate_on_ingest_success: bool = False
    public_visibility: PublicVisibility = "aggregate_view"
    # 1 shows every non-empty heatmap cell.
    heatmap_k_anonymity: int = Field(default=1, ge=1)
    public_infra_types: InfraTypes = None
    # Replaces the default form; validated by the route.
    form_schema: dict[str, Any] | None = None

    @model_validator(mode="after")
    def _validate_scheduling(self) -> CrisisCreatePayload:
        # A non-empty countries list also resolves to a polygon.
        has_geometry_input = self.geometry is not None or bool(self.countries)
        check_scheduling_inputs(
            activate_at=self.activate_at,
            activate_on_ingest_success=self.activate_on_ingest_success,
            target_status=self.status,
            geometry_present=has_geometry_input,
        )
        return self


class CrisisCreatedResponse(BaseModel):
    id: uuid.UUID
    name: str
    status: CrisisStatus
    created_at: datetime
    has_geometry: bool
    type: str
    countries: list[str]
    started_at: datetime | None
    ended_at: datetime | None
    activate_at: datetime | None = None
    activate_on_ingest_success: bool = False
    public_visibility: PublicVisibility = "aggregate_view"
    heatmap_k_anonymity: int = 1
    public_infra_types: list[str] | None = None


class CrisisJobOut(BaseModel):
    """One crisis_jobs row for the admin polling endpoint."""

    job_type: str
    status: Literal["running", "succeeded", "failed"]
    error: str | None
    started_at: datetime
    ended_at: datetime | None
    # Null until the worker reports progress; progress_total stays null without an estimate.
    phase: str | None = None
    progress_count: int | None = None
    progress_total: int | None = None


class IngestEstimateSeconds(BaseModel):
    download: int
    load: int
    total: int


class IngestEstimateResponse(BaseModel):
    """approx_building_count is an overcount: it counts the AOI bbox, not the polygon."""

    approx_building_count: int
    estimate_basis: str
    est_seconds: IngestEstimateSeconds
    note: str


class IngestEstimateGeometryRequest(BaseModel):
    """Estimate from a GeoJSON (Multi)Polygon before the crisis row exists."""

    geometry: dict[str, Any]


class CrisisAdminListItem(CrisisCreatedResponse):
    """Coordinator list item: every status, plus operational metadata."""

    # [w, s, e, n] envelope so the map can frame the crisis without its geometry.
    bbox: tuple[float, float, float, float] | None = None
    pmtiles_url: str | None
    overture_release_pinned: str | None
    # Set by a successful ingest and cleared when the geometry changes.
    buildings_ingested_at: datetime | None = None
    buildings_ingested_count: int | None = None


class CrisisPatchPayload(BaseModel):
    """Partial update: only fields present in the body are written.

    Unknown fields are rejected so typos 422. A new geometry also clears the
    building ingest, which must be re-run.
    """

    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, min_length=1)
    status: CrisisStatus | None = None
    type: str | None = Field(default=None, min_length=1)
    countries: list[str] | None = None
    started_at: datetime | None = None
    ended_at: datetime | None = None
    geometry: CrisisGeometryInput | None = None
    activate_at: datetime | None = None
    activate_on_ingest_success: bool = False
    # The admin UI confirms "full"; the API accepts it as is.
    public_visibility: PublicVisibility | None = None
    heatmap_k_anonymity: int | None = Field(default=None, ge=1)
    # Omitted leaves it untouched; explicit null (or []) clears the filter.
    public_infra_types: InfraTypes = None

    @model_validator(mode="after")
    def _validate_scheduling(self) -> CrisisPatchPayload:
        # Without status in the body the route checks against the stored row.
        set_fields = self.model_fields_set
        if "activate_at" not in set_fields and "activate_on_ingest_success" not in set_fields:
            return self
        if "status" not in set_fields:
            return self
        assert self.status is not None
        check_scheduling_inputs(
            activate_at=self.activate_at,
            activate_on_ingest_success=self.activate_on_ingest_success,
            target_status=self.status,
            geometry_present=None,  # route layer answers this from the DB
        )
        return self
