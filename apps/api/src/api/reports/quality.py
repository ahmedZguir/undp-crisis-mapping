"""Pure report scoring: citizen points and coordinator-facing confidence.

Shared by the scoring worker and the admin verify endpoint, which recomputes
confidence from a stored row.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

# Points: photo-dependent checks are skipped (not failed) when there is no
# photo, consistent with the photo-optional min-content rule.
MAX_POINTS = 5
QUALITY_POINTS_BAR = 4  # a "quality" report for badge thresholds

# Relative weights; the score renormalises over the factors present.
W_RELEVANCE = 0.20
W_DAMAGE = 0.20
W_PHOTO_FRESH = 0.15
W_CORROBORATION = 0.25  # strong
W_GPS = 0.10  # low-medium
W_REPUTATION = 0.05  # small
W_RICHNESS = 0.05  # small

# Past this many distinct nearby reporters the extra signal is negligible.
CORROBORATION_SATURATION = 3
REPUTATION_SATURATION = 5

# Coordinator verify dominates: a verified report floors at this and is
# ordered among verified peers by its evidence blend.
VERIFIED_FLOOR = 0.85
VERIFIED_EVIDENCE_SPAN = 0.15

# A duplicate (recycled / circulating) image is weak evidence; cap its
# confidence unless a coordinator explicitly verifies it (verify wins).
DUP_PENALTY_CAP = 0.15

# Relevance label -> confidence contribution.
_RELEVANCE_VALUE = {"relevant": 1.0, "unclear": 0.5, "irrelevant": 0.0}


@dataclass(slots=True)
class Quality:
    """Stored quality signals, mirroring the report_quality columns used for scoring."""

    has_photo: bool
    has_description: bool
    relevance_label: str | None
    damage_agreement: bool | None
    photo_fresh: bool | None
    gps_pin_match: bool | None
    corroborators_500m: int
    reporter_reputation: int
    is_duplicate_image: bool


# Points breakdown shown in the citizen history feed; compute_points is its sum.
POINT_FACTOR_KEYS = ("submitted", "photo", "description", "photo_relevant", "damage_match")

FactorState = Literal["earned", "missed", "na"]


@dataclass(slots=True, frozen=True)
class PointFactor:
    """One line of the points breakdown.

    `state` is "earned", "missed", or "na" when the check does not apply
    (photo checks on a report without a photo).
    """

    key: str
    state: FactorState
    points: int


def point_factors(q: Quality) -> list[PointFactor]:
    """Per-check breakdown behind a report's points.

    A duplicate image marks every factor "na" so the breakdown matches the 0 total.
    """
    if q.is_duplicate_image:
        return [PointFactor(k, "na", 0) for k in POINT_FACTOR_KEYS]

    def factor(key: str, *, earned: bool, applicable: bool = True) -> PointFactor:
        if not applicable:
            return PointFactor(key, "na", 0)
        return PointFactor(key, "earned" if earned else "missed", 1 if earned else 0)

    return [
        factor("submitted", earned=True),
        factor("photo", earned=q.has_photo),
        factor("description", earned=q.has_description),
        factor(
            "photo_relevant",
            earned=q.relevance_label == "relevant",
            applicable=q.has_photo and q.relevance_label is not None,
        ),
        factor(
            "damage_match",
            earned=q.damage_agreement is True,
            applicable=q.has_photo and q.damage_agreement is not None,
        ),
    ]


def compute_points(q: Quality) -> int:
    """Sum of point_factors capped at MAX_POINTS; a duplicate image earns 0."""
    return min(sum(f.points for f in point_factors(q)), MAX_POINTS)


def confidence_from_quality(q: Quality, *, verified: bool) -> float:
    """Weighted blend over present factors, in [0, 1].

    Factors that do not apply (no photo) are dropped, not counted as failures.
    Coordinator verify sets a floor; a duplicate image is capped unless verified.
    """
    factors: list[tuple[float, float]] = []

    if q.relevance_label in _RELEVANCE_VALUE:
        factors.append((W_RELEVANCE, _RELEVANCE_VALUE[q.relevance_label]))
    if q.damage_agreement is not None:
        factors.append((W_DAMAGE, 1.0 if q.damage_agreement else 0.0))
    if q.photo_fresh is not None:
        factors.append((W_PHOTO_FRESH, 1.0 if q.photo_fresh else 0.0))
    if q.gps_pin_match is not None:
        factors.append((W_GPS, 1.0 if q.gps_pin_match else 0.0))

    factors.append((W_CORROBORATION, min(q.corroborators_500m / CORROBORATION_SATURATION, 1.0)))
    factors.append((W_REPUTATION, min(q.reporter_reputation / REPUTATION_SATURATION, 1.0)))
    richness = ((1.0 if q.has_photo else 0.0) + (1.0 if q.has_description else 0.0)) / 2.0
    factors.append((W_RICHNESS, richness))

    total_w = sum(w for w, _ in factors)
    base = (sum(w * v for w, v in factors) / total_w) if total_w else 0.0

    if verified:
        return round(VERIFIED_FLOOR + VERIFIED_EVIDENCE_SPAN * base, 3)
    if q.is_duplicate_image:
        return round(min(base, DUP_PENALTY_CAP), 3)
    return round(base, 3)
