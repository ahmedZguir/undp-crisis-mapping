"""Pure unit tests for the reporter-quality scoring logic.

No DB / no AI — exercises `compute_points` and `confidence_from_quality`
(`api/reports/quality.py`). These pin the calibration-sensitive behaviour:
never-penalise points, photo-skip, the
present-factor renormalisation, the verify floor, and the duplicate cap.
"""

from __future__ import annotations

from api.reports.quality import (
    DUP_PENALTY_CAP,
    MAX_POINTS,
    VERIFIED_FLOOR,
    Quality,
    compute_points,
    confidence_from_quality,
    point_factors,
)


def _q(**overrides: object) -> Quality:
    base: dict[str, object] = {
        "has_photo": True,
        "has_description": True,
        "relevance_label": "relevant",
        "damage_agreement": True,
        "photo_fresh": True,
        "gps_pin_match": True,
        "corroborators_500m": 0,
        "reporter_reputation": 0,
        "is_duplicate_image": False,
    }
    base.update(overrides)
    return Quality(**base)  # type: ignore[arg-type]


# --- points breakdown ----------------------------------------------------


def _states(q: Quality) -> dict[str, str]:
    return {f.key: f.state for f in point_factors(q)}


def test_point_factors_sum_equals_compute_points() -> None:
    # The breakdown is the single source of truth behind the total.
    for q in (
        _q(),
        _q(has_photo=False),
        _q(relevance_label="irrelevant"),
        _q(damage_agreement=False),
    ):
        assert sum(f.points for f in point_factors(q)) == compute_points(q)


def test_point_factors_full_quality_report_all_earned() -> None:
    assert _states(_q()) == {
        "submitted": "earned",
        "photo": "earned",
        "description": "earned",
        "photo_relevant": "earned",
        "damage_match": "earned",
    }


def test_point_factors_description_only_skips_photo_checks() -> None:
    # No photo -> photo checks are "na" (skipped), not "missed" (failed).
    states = _states(_q(has_photo=False, relevance_label=None, damage_agreement=None))
    assert states["description"] == "earned"
    assert states["photo"] == "missed"
    assert states["photo_relevant"] == "na"
    assert states["damage_match"] == "na"


def test_point_factors_irrelevant_photo_is_missed() -> None:
    states = _states(_q(relevance_label="irrelevant", damage_agreement=False))
    assert states["photo"] == "earned"
    assert states["photo_relevant"] == "missed"
    assert states["damage_match"] == "missed"


def test_point_factors_low_confidence_damage_is_na() -> None:
    # damage_agreement null = classifier was not confident -> N/A, not a miss.
    assert _states(_q(damage_agreement=None))["damage_match"] == "na"


def test_point_factors_duplicate_earns_nothing() -> None:
    states = _states(_q(is_duplicate_image=True))
    assert set(states.values()) == {"na"}
    assert compute_points(_q(is_duplicate_image=True)) == 0


# --- points --------------------------------------------------------------


def test_points_full_photo_report_caps_at_five() -> None:
    # valid + photo + description + relevant + damage-agree = 5
    assert compute_points(_q()) == MAX_POINTS


def test_points_description_only_skips_photo_checks() -> None:
    # valid(+1) + description(+1); photo/relevance/damage skipped, not failed.
    q = _q(has_photo=False, relevance_label=None, damage_agreement=None)
    assert compute_points(q) == 2


def test_points_photo_only_no_description() -> None:
    # valid(+1) + photo(+1) + relevant(+1) + damage(+1) = 4
    q = _q(has_description=False)
    assert compute_points(q) == 4


def test_points_irrelevant_photo_earns_no_relevance_point() -> None:
    q = _q(relevance_label="irrelevant", damage_agreement=False)
    # valid + photo + description = 3
    assert compute_points(q) == 3


def test_duplicate_image_earns_zero() -> None:
    assert compute_points(_q(is_duplicate_image=True)) == 0


# --- confidence ----------------------------------------------------------


def test_confidence_in_unit_range() -> None:
    score = confidence_from_quality(_q(corroborators_500m=3), verified=False)
    assert 0.0 <= score <= 1.0


def test_confidence_renormalises_over_present_factors() -> None:
    # A description-only report drops relevance/damage/freshness/GPS. The
    # remaining always-present factors (corroboration/reputation/richness)
    # must still produce a finite score, not collapse to a divide-by-zero.
    q = _q(
        has_photo=False,
        relevance_label=None,
        damage_agreement=None,
        photo_fresh=None,
        gps_pin_match=None,
        corroborators_500m=3,
    )
    score = confidence_from_quality(q, verified=False)
    assert 0.0 < score <= 1.0


def test_na_factors_do_not_count_as_failures() -> None:
    # Same signals, but with photo factors present-and-perfect vs N/A. The
    # N/A variant must not score LOWER than treating them as zeros would —
    # they are dropped, not failed. Compare against an all-zero-photo report.
    perfect = confidence_from_quality(_q(corroborators_500m=2), verified=False)
    failed_photo = confidence_from_quality(
        _q(
            relevance_label="irrelevant",
            damage_agreement=False,
            photo_fresh=False,
            gps_pin_match=False,
            corroborators_500m=2,
        ),
        verified=False,
    )
    assert perfect > failed_photo


def test_more_corroboration_raises_confidence_until_saturation() -> None:
    low = confidence_from_quality(_q(corroborators_500m=0), verified=False)
    mid = confidence_from_quality(_q(corroborators_500m=2), verified=False)
    sat = confidence_from_quality(_q(corroborators_500m=3), verified=False)
    over = confidence_from_quality(_q(corroborators_500m=50), verified=False)
    assert low < mid < sat
    assert sat == over  # saturates — extra reporters round to zero


def test_verified_report_floors_high() -> None:
    # Even a weak report, once verified, floors at VERIFIED_FLOOR.
    weak = _q(
        has_photo=False,
        relevance_label=None,
        damage_agreement=None,
        photo_fresh=None,
        gps_pin_match=None,
        corroborators_500m=0,
    )
    assert confidence_from_quality(weak, verified=True) >= VERIFIED_FLOOR


def test_duplicate_image_capped_unless_verified() -> None:
    dup = _q(is_duplicate_image=True, corroborators_500m=3)
    capped = confidence_from_quality(dup, verified=False)
    assert capped <= DUP_PENALTY_CAP
    # Coordinator verify overrides the cap (their judgement wins).
    assert confidence_from_quality(dup, verified=True) >= VERIFIED_FLOOR


def test_unclear_relevance_between_relevant_and_irrelevant() -> None:
    relevant = confidence_from_quality(_q(relevance_label="relevant"), verified=False)
    unclear = confidence_from_quality(_q(relevance_label="unclear"), verified=False)
    irrelevant = confidence_from_quality(_q(relevance_label="irrelevant"), verified=False)
    assert irrelevant < unclear < relevant
