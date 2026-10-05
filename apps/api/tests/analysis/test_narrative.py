"""Unit tests for the narrative layer (`api.analysis.narrative`).

DB-free and network-free: the LLM is a fake `AIClients` (or absent), and the
community-summary collector is fed a hand-rolled async frame iterator. The
grounding contract itself ("cite only computed figures") is enforced by the
prompt + the deterministic digest; here we test the seams around it — the
fallback Narrative, the defensive JSON parsing, and the frame→CommunitySummary
mapping.
"""

from __future__ import annotations

import dataclasses
import uuid
from datetime import UTC, datetime
from typing import Any

from api.ai.client import AIClients
from api.analysis.metrics import (
    FLAG_BLIND_SPOT,
    FLAG_BLOCKED,
    FLAG_SEVERE,
    BBox,
    CoverageMetrics,
    DamageMix,
    District,
    HotspotMetrics,
    ImpactEstimates,
    InfraMetrics,
    ReportMeta,
    ReportMetrics,
)
from api.analysis.narrative import (
    _crisis_digest,  # pyright: ignore[reportPrivateUsage]
    _parse_narrative_json,  # pyright: ignore[reportPrivateUsage]
    _seed_for,  # pyright: ignore[reportPrivateUsage]
    _top_districts,  # pyright: ignore[reportPrivateUsage]
    build_narrative,
)

# --- fixtures ------------------------------------------------------------


def _district(
    name: str,
    *,
    rank: int,
    flags: list[str],
    complete: int = 0,
    partial: int = 0,
    debris_yes: int = 0,
    debris_known: int = 0,
    services_hit: int = 0,
) -> District:
    return District(
        id=f"div:{name}",
        name=name,
        official=True,
        centroid_lat=25.3,
        centroid_lon=51.5,
        report_count=complete + partial + 1,
        damage=DamageMix(minimal=1, partial=partial, complete=complete),
        exposure_buildings=100,
        debris_yes=debris_yes,
        debris_known=debris_known,
        services_hit=services_hit,
        coverage_residual_z=-1.0,
        flags=flags,
        red_components=[],
        tier=0 if (FLAG_SEVERE in flags or FLAG_BLOCKED in flags) else 1,
        rank=rank,
        priority_score=0.5,
        severity_score=0.5,
        reachability_score=0.5,
    )


def _metrics() -> ReportMetrics:
    meta = ReportMeta(
        crisis_id=uuid.uuid4(),
        crisis_name="Test Crisis",
        crisis_type="flood",
        countries=["QAT"],
        as_of=datetime(2026, 6, 3, 14, 0, tzinfo=UTC),
        bbox=BBox(51.0, 25.0, 52.0, 26.0),
        report_count=420,
        device_count=180,
        building_count=10000,
        reported_building_count=900,
    )
    districts = [
        _district(
            "Al-Daayen",
            rank=1,
            flags=[FLAG_SEVERE, FLAG_BLOCKED],
            complete=30,
            partial=40,
            debris_yes=8,
            debris_known=10,
            services_hit=3,
        ),
        _district("Doha", rank=2, flags=[FLAG_SEVERE], complete=5, partial=20),
        _district("Al Khor", rank=3, flags=[FLAG_BLIND_SPOT]),
    ]
    return ReportMetrics(
        meta=meta,
        damage=DamageMix(minimal=120, partial=60, complete=35),
        priority_districts=districts,
        coverage=CoverageMetrics(
            coverage_pct=9.0,
            observed_total=420,
            expected_total=600.0,
            blind_spot_district_ids=["div:Al Khor"],
            cells=[],
        ),
        hotspots=HotspotMetrics(hot_cell_count=12, cold_cell_count=4, significance_z=1.96),
        infra=InfraMetrics(items=[], by_type={"utility": 7, "transport": 3}),
        impact=ImpactEstimates(
            affected_buildings=95,
            displaced_low=285,
            displaced_high=570,
            population_available=False,
            economic_available=False,
            economic_low_usd=None,
            economic_high_usd=None,
            notes=[],
        ),
    )


def _no_ai_clients() -> AIClients:
    return AIClients(text=None, embedding=None)


# --- §2 fallback ---------------------------------------------------------


async def test_build_narrative_fallback_is_grounded() -> None:
    """With no text client, the fallback Narrative is built from the figures."""
    metrics = _metrics()
    narrative = await build_narrative(_no_ai_clients(), metrics)

    # BLUF cites real figures: report count, the displaced range, the top
    # district, and the convenience-sample caveat. It must NOT cite building-stock
    # coverage (per the report owner's request).
    assert "420 reports" in narrative.bottom_line
    assert "285-570 people affected" in narrative.bottom_line
    assert "Al-Daayen" in narrative.bottom_line
    assert "convenience sample" in narrative.bottom_line
    assert "coverage" not in narrative.bottom_line.lower()
    # economic_available is False on this fixture, so no dollar figure appears.
    assert "$" not in narrative.bottom_line

    # 3-5 imperative actions, tied to the right flags.
    assert 3 <= len(narrative.recommended_actions) <= 5
    joined = " ".join(narrative.recommended_actions)
    assert "Clear access into Al-Daayen" in joined  # severe + blocked
    assert "Al Khor" in joined  # blind spot → recon
    assert "utility" in joined  # top damaged infra


async def test_build_narrative_fallback_includes_economic_loss() -> None:
    """When LitPop is loaded the BLUF carries the dollar-loss range too."""
    metrics = _metrics()
    valued = dataclasses.replace(
        metrics,
        impact=dataclasses.replace(
            metrics.impact,
            economic_available=True,
            economic_low_usd=2_400_000.0,
            economic_high_usd=9_800_000.0,
        ),
    )
    narrative = await build_narrative(_no_ai_clients(), valued)
    assert "285-570 people affected" in narrative.bottom_line
    assert "$2.4M-$9.8M in building asset damage" in narrative.bottom_line
    assert "coverage" not in narrative.bottom_line.lower()


async def test_build_narrative_fallback_no_districts() -> None:
    metrics = _metrics()
    bare = ReportMetrics(
        meta=metrics.meta,
        damage=metrics.damage,
        priority_districts=[],
        coverage=metrics.coverage,
        hotspots=metrics.hotspots,
        infra=InfraMetrics(items=[], by_type={}),
        impact=metrics.impact,
    )
    narrative = await build_narrative(_no_ai_clients(), bare)
    assert narrative.bottom_line
    assert 1 <= len(narrative.recommended_actions) <= 5


# --- §2 JSON parsing -----------------------------------------------------


def test_parse_narrative_json_plain() -> None:
    raw = (
        '{"bottom_line": "Severe damage in Al-Daayen.", '
        '"recommended_actions": ["Dispatch teams.", "Clear routes."], '
        '"section_prose": {"coverage": "Only 9% covered."}}'
    )
    parsed = _parse_narrative_json(raw)
    assert parsed is not None
    assert parsed.bottom_line == "Severe damage in Al-Daayen."
    assert parsed.recommended_actions == ["Dispatch teams.", "Clear routes."]
    assert parsed.section_prose == {"coverage": "Only 9% covered."}


def test_parse_narrative_json_fenced_and_wrapped() -> None:
    """Handles a ```json code fence with prose around it."""
    raw = (
        "Sure, here is the report:\n```json\n"
        '{"bottom_line": "BLUF.", "recommended_actions": ["Act now."]}\n'
        "```\nLet me know if you need more."
    )
    parsed = _parse_narrative_json(raw)
    assert parsed is not None
    assert parsed.bottom_line == "BLUF."
    assert parsed.recommended_actions == ["Act now."]
    assert parsed.section_prose == {}


def test_parse_narrative_json_rejects_garbage() -> None:
    assert _parse_narrative_json(None) is None
    assert _parse_narrative_json("not json at all") is None
    # missing required fields
    assert _parse_narrative_json('{"bottom_line": "x"}') is None
    assert _parse_narrative_json('{"recommended_actions": []}') is None
    # empty bottom_line / actions
    assert _parse_narrative_json('{"bottom_line": "  ", "recommended_actions": ["a"]}') is None
    assert _parse_narrative_json('{"bottom_line": "x", "recommended_actions": [""]}') is None


# --- §7 sampled-summary helpers ------------------------------------------


def test_crisis_digest_carries_only_computed_figures() -> None:
    digest = _crisis_digest(_metrics())
    assert digest["report_count"] == 420
    assert digest["damage_mix"] == {"minimal": 120, "partial": 60, "complete": 35}
    assert digest["coverage_pct"] == "9.0%"
    # the top-3 district names lead the digest (the Part B spotlight set)
    assert digest["top_districts"] == ["Al-Daayen", "Doha", "Al Khor"]


def test_top_districts_skips_geomless_and_caps_at_three() -> None:
    metrics = _metrics()  # 3 districts, all official; the fixture gives them no geom
    # geom is None on the fixture districts, so none are eligible for the deep dive.
    assert _top_districts(metrics) == []

    # give the top two a polygon: only those (ranked) become eligible, capped at 3.
    poly: dict[str, Any] = {"type": "Polygon", "coordinates": [[[0, 0], [1, 0], [1, 1], [0, 0]]]}
    districts = list(metrics.priority_districts)
    districts[0] = dataclasses.replace(districts[0], geom=poly)
    districts[1] = dataclasses.replace(districts[1], geom=poly)
    enriched = dataclasses.replace(metrics, priority_districts=districts)
    top = _top_districts(enriched)
    assert [d.rank for d in top] == [1, 2]


def test_seed_for_is_deterministic_and_in_range() -> None:
    a = _seed_for("crisis-x", "district-1")
    b = _seed_for("crisis-x", "district-1")
    c = _seed_for("crisis-x", "district-2")
    assert a == b  # reproducible
    assert a != c  # varies by parts
    assert -1.0 <= a < 1.0  # valid Postgres setseed range
