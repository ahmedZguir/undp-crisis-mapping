"""Read shape for GET /me/stats; points and badges come from the score_report job."""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel


class BadgeOut(BaseModel):
    slug: str
    name: str
    earned_at: datetime
    # None if the report was deleted or the badge is not tied to one report.
    report_id: uuid.UUID | None = None


class ReporterStatsResponse(BaseModel):
    client_id: uuid.UUID
    # Counts only reports that have a report_quality row.
    total_reports: int
    points: int
    badges: list[BadgeOut]
    # Badges earned after the caller's since watermark; empty without since.
    newly_earned: list[BadgeOut]
