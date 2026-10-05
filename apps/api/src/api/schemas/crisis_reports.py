"""Crisis-analysis-report schemas. Coordinator-only, so one schema per shape."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel

CrisisReportStatus = Literal["running", "succeeded", "failed"]


class CrisisReportCreatedResponse(BaseModel):
    """Enough for the client to start polling the detail endpoint."""

    id: uuid.UUID
    status: CrisisReportStatus
    created_at: datetime


class CrisisReportListItem(BaseModel):
    """download_ready avoids signing a URL for every row."""

    id: uuid.UUID
    created_at: datetime
    created_by: uuid.UUID
    # Email snapshot at generation time; may be null. created_by is the stable id.
    created_by_email: str | None
    status: CrisisReportStatus
    phase: str | None
    report_count: int | None
    coverage_pct: float | None
    download_ready: bool


class CrisisReportDetail(BaseModel):
    """download_url is signed per request and set only on success."""

    id: uuid.UUID
    crisis_id: uuid.UUID
    created_at: datetime
    created_by: uuid.UUID
    created_by_email: str | None
    status: CrisisReportStatus
    phase: str | None
    progress_count: int | None
    progress_total: int | None
    error: str | None
    ended_at: datetime | None

    report_count: int | None
    device_count: int | None
    building_count: int | None
    coverage_pct: float | None

    download_url: str | None
