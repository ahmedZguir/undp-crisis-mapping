"""Photo-export schemas. Coordinator-only, so one schema per shape."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel

from api.schemas.admin_search import SearchRequest

# "expired": parts purged after retention; the row is kept for audit.
ReportExportStatus = Literal["running", "succeeded", "failed", "expired"]
ExportScope = Literal["view", "crisis"]
ExportManifestFormat = Literal["csv", "geojson"]


class PhotoExportCreateRequest(BaseModel):
    """filters are resolved to reports when the job runs. format is the bundled manifest."""

    filters: SearchRequest = SearchRequest()
    scope: ExportScope = "view"
    format: ExportManifestFormat = "geojson"


class PhotoExportCreatedResponse(BaseModel):
    """attached is True when the request joined an identical running export."""

    id: uuid.UUID
    status: ReportExportStatus
    created_at: datetime
    attached: bool = False


class PhotoExportPart(BaseModel):
    part_number: int
    download_url: str
    bytes: int
    photo_count: int


class PhotoExportListItem(BaseModel):
    id: uuid.UUID
    created_at: datetime
    created_by: uuid.UUID
    created_by_email: str | None
    status: ReportExportStatus
    phase: str | None
    scope: ExportScope | None
    format: ExportManifestFormat | None
    photo_count: int | None
    total_bytes: int | None
    expires_at: datetime | None
    download_ready: bool


class PhotoExportDetail(BaseModel):
    """parts are set only for an unexpired successful bundle; URLs expire with it."""

    id: uuid.UUID
    crisis_id: uuid.UUID
    created_at: datetime
    created_by: uuid.UUID
    created_by_email: str | None
    status: ReportExportStatus
    phase: str | None
    progress_count: int | None
    progress_total: int | None
    error: str | None
    ended_at: datetime | None
    scope: ExportScope | None
    format: ExportManifestFormat | None
    photo_count: int | None
    total_bytes: int | None
    expires_at: datetime | None
    parts: list[PhotoExportPart]
