"""Unit tests for the minimum-content gate and its input normalisation.

Pure tests — no DB, no Storage. They exercise:

  - `ReportSubmissionService._validate_minimum_content` (the OR-pairs gate):
    photo OR description, and location OR route_description.
  - `ReportSubmitPayload`'s empty-string -> None normalisation for
    `description` (mirrors the existing `route_description` rule), which is
    what makes the gate and the DB CHECK meaningful.

The full end-to-end path (route + DB CHECK) lives in
`test_reports_integration.py`; the route's `ReportContentError` -> 422
mapping lives in `test_reports_validation.py`.
"""

from __future__ import annotations

import uuid

import pytest

from api.reports.service import ReportContentError, ReportSubmissionService
from api.schemas.reports import ReportSubmitPayload

_CRISIS_ID = uuid.uuid4()


def _payload(**overrides: object) -> ReportSubmitPayload:
    base: dict[str, object] = {
        "crisis_id": str(_CRISIS_ID),
        "damage_class": "partial",
    }
    base.update(overrides)
    return ReportSubmitPayload.model_validate(base)


# --- the OR-pairs gate ---------------------------------------------------


def test_photo_only_passes() -> None:
    # Photo present satisfies pair 1; location satisfies pair 2.
    payload = _payload(location={"lat": 25.2854, "lng": 51.5310})
    ReportSubmissionService._validate_minimum_content(True, payload)


def test_description_only_no_photo_passes() -> None:
    payload = _payload(description="Wall crack on south face", location={"lat": 1.0, "lng": 2.0})
    ReportSubmissionService._validate_minimum_content(False, payload)


def test_no_photo_no_description_raises() -> None:
    payload = _payload(location={"lat": 1.0, "lng": 2.0})
    with pytest.raises(ReportContentError) as exc:
        ReportSubmissionService._validate_minimum_content(False, payload)
    assert "photo_or_description" in str(exc.value)


def test_route_description_only_no_location_passes() -> None:
    payload = _payload(description="x", route_description="Behind the blue mosque, 2nd alley")
    ReportSubmissionService._validate_minimum_content(False, payload)


def test_no_location_no_route_raises() -> None:
    # Photo present (pair 1 satisfied) but neither location nor route (pair 2).
    payload = _payload()
    with pytest.raises(ReportContentError) as exc:
        ReportSubmissionService._validate_minimum_content(True, payload)
    assert "location_or_route" in str(exc.value)


def test_photo_present_short_circuits_description_requirement() -> None:
    # A photo with a GPS fix and no text at all is valid.
    payload = _payload(location={"lat": 1.0, "lng": 2.0})
    ReportSubmissionService._validate_minimum_content(True, payload)


# --- description normalisation (empty/whitespace -> None) ----------------


def test_blank_description_normalises_to_none() -> None:
    payload = _payload(description="   ", location={"lat": 1.0, "lng": 2.0})
    assert payload.description is None


def test_description_is_trimmed() -> None:
    payload = _payload(description="  hi  ", location={"lat": 1.0, "lng": 2.0})
    assert payload.description == "hi"


def test_blank_description_no_photo_fails_the_gate() -> None:
    # Whitespace-only description is normalised away, so a photo-less report
    # carrying only blanks must be rejected by pair 1.
    payload = _payload(description="   ", location={"lat": 1.0, "lng": 2.0})
    with pytest.raises(ReportContentError):
        ReportSubmissionService._validate_minimum_content(False, payload)
