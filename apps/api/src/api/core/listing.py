"""Bbox parsing and opaque (created_at, id) cursors shared by report feeds."""

from __future__ import annotations

import base64
import binascii
import json
import uuid
from dataclasses import dataclass
from datetime import datetime

from fastapi import HTTPException


@dataclass(frozen=True, slots=True)
class Bbox:
    west: float
    south: float
    east: float
    north: float


def parse_bbox(raw: str) -> Bbox:
    """Parse "w,s,e,n" into a Bbox. Antimeridian-crossing boxes are rejected."""
    parts = raw.split(",")
    if len(parts) != 4:
        raise HTTPException(status_code=400, detail="bbox must be 'west,south,east,north'")
    try:
        west, south, east, north = (float(p) for p in parts)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=f"bbox values must be numeric: {exc}") from exc

    if not (-180.0 <= west <= 180.0 and -180.0 <= east <= 180.0):
        raise HTTPException(status_code=400, detail="bbox longitudes must be in [-180, 180]")
    if not (-90.0 <= south <= 90.0 and -90.0 <= north <= 90.0):
        raise HTTPException(status_code=400, detail="bbox latitudes must be in [-90, 90]")
    if west >= east:
        raise HTTPException(status_code=400, detail="bbox west must be < east")
    if south >= north:
        raise HTTPException(status_code=400, detail="bbox south must be < north")
    return Bbox(west=west, south=south, east=east, north=north)


def encode_cursor(created_at: datetime, report_id: uuid.UUID) -> str:
    # Format must stay stable so cursors issued before a deploy still decode.
    payload = json.dumps({"ts": created_at.isoformat(), "id": str(report_id)})
    return base64.urlsafe_b64encode(payload.encode()).decode()


def decode_cursor(cursor: str) -> tuple[datetime, uuid.UUID]:
    try:
        raw = base64.urlsafe_b64decode(cursor.encode()).decode()
        payload = json.loads(raw)
        return datetime.fromisoformat(payload["ts"]), uuid.UUID(payload["id"])
    except (ValueError, KeyError, binascii.Error) as exc:
        raise HTTPException(status_code=400, detail="invalid cursor") from exc


__all__ = ["Bbox", "decode_cursor", "encode_cursor", "parse_bbox"]
