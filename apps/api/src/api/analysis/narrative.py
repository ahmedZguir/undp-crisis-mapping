"""LLM prose for the analysis report, grounded on the precomputed metrics.

The model only sees a digest of computed figures and never does arithmetic.
Without a text client both entry points fall back to deterministic output.
"""

from __future__ import annotations

import asyncio
import dataclasses
import hashlib
import json
import logging
import uuid
from typing import Any, cast

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from api.admin.search_queries import fetch_hits_by_ids
from api.ai import halfvec
from api.ai.client import AIClients, AIClientUnavailableError
from api.ai.llm import LLMOutputError, complete_text, parse_json
from api.ai.summarization import THEME_COUNT_CAP, SectionSummary, summarise_section
from api.analysis.format import fmt_coverage_pct, fmt_usd
from api.analysis.metrics import (
    FLAG_BLIND_SPOT,
    FLAG_BLOCKED,
    FLAG_SEVERE,
    CommunitySummary,
    CommunityTheme,
    CrisisHeadline,
    District,
    DistrictThemes,
    Narrative,
    ReportMetrics,
)

logger = logging.getLogger(__name__)

# Priority districts included in the digest; the BLUF only needs the top.
_DIGEST_TOP_DISTRICTS = 6

_MAX_OUTPUT_TOKENS = 1024

# Wall-clock budget for the community summary fan-out; sections finished by the
# deadline are kept.
_COMMUNITY_SUMMARY_TIMEOUT_S = 300.0

# Sample caps for the crisis-wide and per-district sections, bounded by embedding I/O.
_CRISIS_SAMPLE_SIZE = 5000
_DISTRICT_SAMPLE_SIZE = 2500

# Priority districts that get a per-district deep dive.
_DISTRICT_DEEP_DIVE_COUNT = 3

# Shared with the dashboard summary so theme granularity matches across surfaces.
_CRISIS_TOP_THEMES = THEME_COUNT_CAP
_DISTRICT_TOP_THEMES = THEME_COUNT_CAP


# Glyphs for the fallback prose only; the model digest uses bare flag strings.
_FLAG_LABEL = {
    FLAG_SEVERE: "severe",
    FLAG_BLOCKED: "blocked access",
    FLAG_BLIND_SPOT: "blind spot",
}


# Narrative


_NARRATIVE_SYSTEM = """\
You are a RASID crisis-analysis officer writing the executive summary of a
situation report from a community damage-reporting platform.

You are given a JSON digest of figures that were COMPUTED by a deterministic
pipeline. Write decision-oriented prose ON TOP of those figures.

HARD RULES:
- Every number you write MUST appear verbatim in the digest. You are FORBIDDEN
  from inventing, rounding, extrapolating, or estimating any figure not present.
  For dollar amounts, use the economic_low_label / economic_high_label strings.
- Treat the data as a convenience sample, not a census: never imply totals. Do
  NOT cite the building-stock coverage percentage anywhere in the bottom_line.
- The bottom_line MUST state the estimated people affected (the displaced
  low-to-high range) and, when economic figures are available, the estimated
  economic loss (the economic low-to-high dollar range).
- Phrase recommended actions in the imperative, tied to specific districts and
  their flags. A blocked district: clear access before sending teams. A blind
  spot: send recon / do not assume safe. A severe district: dispatch a team.
- Be concrete and brief. No filler, no restating the rules.

Return ONLY a JSON object, no prose around it, with this exact shape:
{
  "bottom_line": "2-4 sentences, the BLUF",
  "recommended_actions": ["imperative action", "..."],   // 3 to 5 items
  "section_prose": {"coverage": "...", "priority": "..."}  // optional, may be {}
}
"""


async def build_narrative(clients: AIClients, metrics: ReportMetrics) -> Narrative:
    """Write the BLUF and recommended actions from a digest of `metrics`.

    Falls back to a template narrative when no text client is configured or the
    model output can't be parsed.
    """
    try:
        cfg = clients.require_text()
    except AIClientUnavailableError as exc:
        logger.info("narrative.fallback: text client unavailable: %s", exc)
        return _fallback_narrative(metrics)

    digest = _build_digest(metrics)
    try:
        content = await complete_text(
            cfg,
            _NARRATIVE_SYSTEM,
            json.dumps(digest, separators=(",", ":")),
            max_tokens=_MAX_OUTPUT_TOKENS,
            temperature=0.2,
            json_mode=True,
        )
    except Exception as exc:
        logger.warning("narrative.llm_error: %s", exc)
        return _fallback_narrative(metrics)

    parsed = _parse_narrative_json(content)
    if parsed is None:
        logger.warning("narrative.parse_failed; using deterministic fallback")
        return _fallback_narrative(metrics)
    return parsed


def _build_digest(metrics: ReportMetrics) -> dict[str, Any]:
    """Build the LLM's only input: the figures the narrative is allowed to cite."""
    meta = metrics.meta
    damage = metrics.damage
    digest: dict[str, Any] = {
        "crisis": {
            "name": meta.crisis_name,
            "type": meta.crisis_type,
            "countries": meta.countries,
            "as_of": meta.as_of.isoformat(),
        },
        "coverage": {
            "report_count": meta.report_count,
            "reporting_devices": meta.device_count,
            "building_stock": meta.building_count,
            "buildings_reported": meta.reported_building_count,
            "coverage_pct": fmt_coverage_pct(meta.coverage_pct),
        },
        "damage_mix": {
            "minimal": damage.minimal,
            "partial": damage.partial,
            "complete": damage.complete,
            "severe_share_pct": round(100.0 * damage.severe_share, 1),
        },
        "priority_districts": [
            _district_digest(d) for d in metrics.priority_districts[:_DIGEST_TOP_DISTRICTS]
        ],
        "blind_spot_districts": [
            d.name for d in metrics.priority_districts if FLAG_BLIND_SPOT in d.flags
        ],
        "hotspots": {
            "hot_cells": metrics.hotspots.hot_cell_count,
            "cold_cells": metrics.hotspots.cold_cell_count,
        },
        "critical_infra_by_type": metrics.infra.by_type,
        "impact": {
            "affected_buildings": metrics.impact.affected_buildings,
            "displaced_low": metrics.impact.displaced_low,
            "displaced_high": metrics.impact.displaced_high,
            "economic_available": metrics.impact.economic_available,
            "economic_low_usd": metrics.impact.economic_low_usd,
            "economic_high_usd": metrics.impact.economic_high_usd,
            # Formatted labels the BLUF can cite verbatim.
            "economic_low_label": (
                fmt_usd(metrics.impact.economic_low_usd)
                if metrics.impact.economic_available
                else None
            ),
            "economic_high_label": (
                fmt_usd(metrics.impact.economic_high_usd)
                if metrics.impact.economic_available
                else None
            ),
        },
    }
    return digest


def _district_digest(d: District) -> dict[str, Any]:
    return {
        "name": d.name,
        "official": d.official,
        "rank": d.rank,
        "flags": d.flags,
        "red_components": d.red_components,
        "report_count": d.report_count,
        "damage": {
            "minimal": d.damage.minimal,
            "partial": d.damage.partial,
            "complete": d.damage.complete,
        },
        "services_hit": d.services_hit,
        "debris_blocked_pct": round(100.0 * d.debris_blocked_share, 1),
    }


def _parse_narrative_json(content: str | None) -> Narrative | None:
    """Parse the model's JSON into a `Narrative`, or None if nothing usable came back.

    The model sometimes wraps the object in prose or a code fence even when asked not to.
    """
    if not content:
        return None
    try:
        loaded: object = parse_json(content)
    except LLMOutputError:
        return None
    if not isinstance(loaded, dict):
        return None
    data = cast("dict[str, Any]", loaded)

    bottom_line = data.get("bottom_line")
    actions_raw = data.get("recommended_actions")
    if not isinstance(bottom_line, str) or not bottom_line.strip():
        return None
    if not isinstance(actions_raw, list):
        return None
    actions = [str(a).strip() for a in cast("list[Any]", actions_raw) if str(a).strip()]
    if not actions:
        return None

    section_prose: dict[str, str] = {}
    prose_raw = data.get("section_prose")
    if isinstance(prose_raw, dict):
        for key, value in cast("dict[Any, Any]", prose_raw).items():
            if isinstance(key, str) and isinstance(value, str) and value.strip():
                section_prose[key] = value.strip()

    return Narrative(
        bottom_line=bottom_line.strip(),
        recommended_actions=actions,
        section_prose=section_prose,
    )


# Deterministic fallback


def _fallback_narrative(metrics: ReportMetrics) -> Narrative:
    """Template narrative built from the figures, used when the LLM is unavailable."""
    meta = metrics.meta
    impact = metrics.impact
    top = metrics.priority_districts[:3]

    people = f"{impact.displaced_low:,}-{impact.displaced_high:,}"
    econ = ""
    if (
        impact.economic_available
        and impact.economic_low_usd is not None
        and impact.economic_high_usd is not None
    ):
        econ = (
            f", and an estimated {fmt_usd(impact.economic_low_usd)}-"
            f"{fmt_usd(impact.economic_high_usd)} in building asset damage"
        )
    impact_clause = f"an estimated {people} people affected{econ}"

    if top:
        lead = "; ".join(_district_phrase(d) for d in top)
        bottom_line = (
            f"{meta.report_count} reports flag {metrics.damage.complete} confirmed-destroyed "
            f"and {metrics.damage.partial} partially-damaged structures, with {impact_clause}. "
            f"Priority areas: {lead}. Treat as a convenience sample, not a census."
        )
    else:
        bottom_line = (
            f"{meta.report_count} reports flag {metrics.damage.complete} confirmed-destroyed "
            f"and {metrics.damage.partial} partially-damaged structures, with {impact_clause}; "
            "no area cleared a priority threshold. Treat as a convenience sample, not a census."
        )

    actions = _fallback_actions(metrics)
    return Narrative(
        bottom_line=bottom_line,
        recommended_actions=actions,
        section_prose={},
    )


def _district_phrase(d: District) -> str:
    flag_text = ", ".join(_FLAG_LABEL.get(f, f) for f in d.flags) or "no hard flag"
    return f"{d.name} ({flag_text}; {d.damage.complete} destroyed, {d.damage.partial} partial)"


def _fallback_actions(metrics: ReportMetrics) -> list[str]:
    actions: list[str] = []
    for d in metrics.priority_districts:
        if len(actions) >= 5:
            break
        if FLAG_BLOCKED in d.flags and FLAG_SEVERE in d.flags:
            actions.append(
                f"Clear access into {d.name} before sending teams: it is both severely "
                f"damaged ({d.damage.complete} destroyed) and reporting blocked routes "
                f"({round(100.0 * d.debris_blocked_share)}% debris)."
            )
        elif FLAG_SEVERE in d.flags:
            actions.append(
                f"Dispatch a response team to {d.name} ({d.damage.complete} destroyed, "
                f"{d.damage.partial} partial)."
            )
        elif FLAG_BLOCKED in d.flags:
            actions.append(
                f"Clear debris/blocked access in {d.name} "
                f"({round(100.0 * d.debris_blocked_share)}% of reports flag debris)."
            )
        elif FLAG_BLIND_SPOT in d.flags:
            actions.append(
                f"Send a recon team to {d.name}: under-reported relative to its "
                "surroundings; do not assume it is safe."
            )

    blind = [d.name for d in metrics.priority_districts if FLAG_BLIND_SPOT in d.flags]
    if blind and len(actions) < 5:
        actions.append(
            "Recon the coverage blind spots ("
            + ", ".join(blind[:3])
            + "): low reporting next to confirmed damage, not confirmed safe."
        )

    if metrics.infra.by_type and len(actions) < 5:
        top_infra = max(metrics.infra.by_type.items(), key=lambda kv: kv[1])
        actions.append(
            f"Prioritise restoring {top_infra[0]} infrastructure "
            f"({top_infra[1]} damaged/non-functional reports)."
        )

    if not actions:
        coverage = fmt_coverage_pct(metrics.meta.coverage_pct)
        actions.append(
            f"Maintain monitoring: {metrics.meta.report_count} reports at "
            f"{coverage} building coverage, no priority flag yet."
        )
    return actions[:5]


# Community summary


@dataclasses.dataclass(slots=True)
class _SectionInput:
    """The sampled report set for one section (crisis-wide or one district)."""

    hits: list[dict[str, Any]]
    embeddings: dict[uuid.UUID, list[float]]


class _CommunityAccumulator:
    """Filled in place so sections finished before a deadline cancellation survive."""

    def __init__(self) -> None:
        self.headline: CrisisHeadline | None = None
        self.districts: list[DistrictThemes] = []

    def to_summary(self) -> CommunitySummary:
        districts = sorted(self.districts, key=lambda d: d.rank)
        return CommunitySummary(headline=self.headline, districts=districts)


async def build_community_summary(
    session: AsyncSession,
    clients: AIClients,
    crisis_id: uuid.UUID,
    metrics: ReportMetrics,
    *,
    timeout_s: float | None = _COMMUNITY_SUMMARY_TIMEOUT_S,
) -> CommunitySummary:
    """Summarise a crisis-wide sample plus the top priority districts into themes.

    Sampling runs sequentially because the session can't be shared concurrently;
    the LLM fan-out then runs under one `timeout_s` deadline and keeps whatever
    finished. Returns an empty summary when the text client or embeddings are missing.
    """
    try:
        crisis_in = await _sample_crisis_reports(session, crisis_id)
        top3 = _top_districts(metrics)
        district_in = [(d, await _sample_district_reports(session, crisis_id, d)) for d in top3]
    except Exception as exc:
        logger.warning("community.sample_error: %s", exc)
        return CommunitySummary(headline=None, districts=[])

    acc = _CommunityAccumulator()

    async def _headline() -> None:
        if not crisis_in.hits:
            return
        section = await summarise_section(
            crisis_in.hits,
            crisis_in.embeddings,
            clients=clients,
            top_k=_CRISIS_TOP_THEMES,
            stats_digest=_crisis_digest(metrics),
            synthesis_kind="headline",
        )
        acc.headline = CrisisHeadline(
            headline=section.prose,
            themes=_to_themes(section),
            sampled=section.sampled,
            total=metrics.meta.report_count,
            other_count=section.other_count,
        )

    async def _district(d: District, inp: _SectionInput) -> None:
        if not inp.hits:
            return
        section = await summarise_section(
            inp.hits,
            inp.embeddings,
            clients=clients,
            top_k=_DISTRICT_TOP_THEMES,
            stats_digest=_district_digest(d),
            synthesis_kind="district",
        )
        acc.districts.append(
            DistrictThemes(
                district_id=d.id,
                district_name=d.name,
                rank=d.rank,
                write_up=section.prose,
                themes=_to_themes(section),
                sampled=section.sampled,
                report_count=d.report_count,
                other_count=section.other_count,
            )
        )

    async def _run() -> None:
        await asyncio.gather(
            _headline(),
            *(_district(d, inp) for d, inp in district_in),
            return_exceptions=True,
        )

    task = asyncio.ensure_future(_run())
    try:
        await asyncio.wait_for(task, timeout_s)
    except TimeoutError:
        logger.warning(
            "community.summary_deadline (%ss): kept headline=%s, %d district section(s)",
            timeout_s,
            acc.headline is not None,
            len(acc.districts),
        )
    except Exception as exc:  # the fan-out itself errored
        logger.warning("community.summary_error: %s", exc)
    return acc.to_summary()


def _to_themes(section: SectionSummary) -> list[CommunityTheme]:
    return [CommunityTheme(label=t.label, count=t.count, quote=t.quote) for t in section.themes]


def _top_districts(metrics: ReportMetrics) -> list[District]:
    """Highest-ranked districts with reports and a polygon to sample within."""
    eligible = [d for d in metrics.priority_districts if d.report_count > 0 and d.geom is not None]
    eligible.sort(key=lambda d: d.rank)
    return eligible[:_DISTRICT_DEEP_DIVE_COUNT]


def _crisis_digest(metrics: ReportMetrics) -> dict[str, Any]:
    """Compact crisis-level stats the headline synthesis may cite (no invention)."""
    meta = metrics.meta
    dmg = metrics.damage
    return {
        "crisis_name": meta.crisis_name,
        "crisis_type": meta.crisis_type,
        "report_count": meta.report_count,
        "coverage_pct": fmt_coverage_pct(meta.coverage_pct),
        "damage_mix": {"minimal": dmg.minimal, "partial": dmg.partial, "complete": dmg.complete},
        "severe_share_pct": round(100.0 * dmg.severe_share, 1),
        "top_districts": [d.name for d in metrics.priority_districts[:_DISTRICT_DEEP_DIVE_COUNT]],
    }


async def _sample_crisis_reports(session: AsyncSession, crisis_id: uuid.UUID) -> _SectionInput:
    """Seeded sample of up to `_CRISIS_SAMPLE_SIZE` reports, with hits and embeddings."""
    seed = _seed_for(str(crisis_id), "crisis")
    ids = await _sample_ids(session, _CRISIS_SAMPLE_IDS_SQL, {"cid": str(crisis_id)}, seed=seed)
    return await _hydrate(session, ids)


async def _sample_district_reports(
    session: AsyncSession, crisis_id: uuid.UUID, district: District
) -> _SectionInput:
    """Seeded sample of reports located inside the district polygon."""
    if district.geom is None:
        return _SectionInput(hits=[], embeddings={})
    seed = _seed_for(str(crisis_id), district.id)
    ids = await _sample_ids(
        session,
        _DISTRICT_SAMPLE_IDS_SQL,
        {"cid": str(crisis_id), "geom": json.dumps(district.geom)},
        seed=seed,
    )
    return await _hydrate(session, ids)


async def _hydrate(session: AsyncSession, ids: list[uuid.UUID]) -> _SectionInput:
    if not ids:
        return _SectionInput(hits=[], embeddings={})
    hits_by_id = await fetch_hits_by_ids(session, ids)
    embeddings = await _fetch_embeddings_by_ids(session, ids)
    hits = [hits_by_id[i] for i in ids if i in hits_by_id]
    return _SectionInput(hits=hits, embeddings=embeddings)


def _seed_for(*parts: str) -> float:
    """Deterministic seed in [-1, 1) so the same crisis and district draw the same sample."""
    digest = hashlib.sha256("|".join(parts).encode()).hexdigest()
    return int(digest[:8], 16) / 0xFFFFFFFF * 2.0 - 1.0


async def _sample_ids(
    session: AsyncSession, sql: Any, params: dict[str, Any], *, seed: float
) -> list[uuid.UUID]:
    """Seed the session RNG, then sample with `order by random() limit N`.

    `setseed` only applies to this connection, so sampling must stay sequential.
    """
    await session.execute(text("select setseed(:seed)"), {"seed": seed})
    result = await session.execute(sql, params)
    return [row.id for row in result.all()]


# DB helpers


_CRISIS_SAMPLE_IDS_SQL = text(
    f"""
    select id
    from public.reports
    where crisis_id = :cid
    order by random()
    limit {_CRISIS_SAMPLE_SIZE}
    """
)


_DISTRICT_SAMPLE_IDS_SQL = text(
    f"""
    select r.id
    from public.reports r
    where r.crisis_id = :cid
      and r.map_point is not null
      and st_contains(st_geomfromgeojson(:geom), r.map_point::geometry)
    order by random()
    limit {_DISTRICT_SAMPLE_SIZE}
    """
)


_EMBEDDING_FETCH_SQL = text(
    """
    select report_id, embedding::text as v
    from public.report_embeddings
    where report_id = any(:ids)
    """
)


async def _fetch_embeddings_by_ids(
    session: AsyncSession, ids: list[uuid.UUID]
) -> dict[uuid.UUID, list[float]]:
    """Load per-report embeddings; pgvector returns `halfvec` as a text literal."""
    if not ids:
        return {}
    result = await session.execute(_EMBEDDING_FETCH_SQL, {"ids": [str(i) for i in ids]})
    out: dict[uuid.UUID, list[float]] = {}
    for row in result.all():
        parsed = halfvec.parse(row.v)
        if parsed:
            out[row.report_id] = parsed
    return out


__all__ = ["build_community_summary", "build_narrative"]
