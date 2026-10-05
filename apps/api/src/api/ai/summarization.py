"""Report-set summarisation for the dashboard stream and the batch report.

Small sets get one prose pass. Larger sets are split into themes with
spherical k-means (HDBSCAN marked this data as all noise), labelled in one
cross-cluster call, summarised per cluster, then synthesised into an overview.
"""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any, cast

import numpy as np
from sklearn.cluster import KMeans  # pyright: ignore[reportMissingTypeStubs]

from api.ai import prompts
from api.ai.client import AIClients, AIClientUnavailableError
from api.ai.llm import complete_text
from api.ai.report_text import report_detail_lines
from api.ai.vectors import l2_normalize

logger = logging.getLogger(__name__)

# Below this many reports, skip clustering and do one prose pass.
SINGLE_PASS_THRESHOLD = 30

# Concurrent LLM calls per fan-out, to avoid stampeding the vLLM endpoint.
_REPORT_CONCURRENCY = 8

# Several seedings avoid a bad local optimum; a fixed seed keeps the partition
# deterministic, matching the seeded sampling upstream.
_KMEANS_N_INIT = 4
_KMEANS_RANDOM_STATE = 0

# One theme per this many sampled reports, so each theme is well populated.
_REPORTS_PER_CLUSTER = 25

# The embeddings are topically homogeneous, so more clusters tend to split one
# theme in two and produce near-duplicate labels.
THEME_COUNT_CAP = 6


def target_cluster_count(n: int, cap: int = THEME_COUNT_CAP) -> int:
    return max(2, min(cap, n // _REPORTS_PER_CLUSTER))


# Per-cluster exemplar count for the citation tail of each section.
_EXEMPLARS_PER_CLUSTER = 10

# Centroid-closest members fed to each label/summary prompt.
_PROMPT_SAMPLE_SIZE = 8

# Smaller than _PROMPT_SAMPLE_SIZE because the label prompt holds every cluster.
_LABEL_PROMPT_SAMPLE_SIZE = 6

# Label prompt only; summary prompts keep full text.
_LABEL_TEXT_MAXLEN = 200

_MAX_OUTPUT_TOKENS = 1024


@dataclass(frozen=True, slots=True)
class ClusterSummary:
    label: str
    count: int
    sentences: str
    exemplars: list[dict[str, Any]]


async def summarise(
    hits: list[dict[str, Any]],
    embeddings_by_id: dict[uuid.UUID, list[float]],
    *,
    clients: AIClients,
    stats_digest: dict[str, Any],
    total: int | None = None,
) -> AsyncIterator[dict[str, Any]]:
    """Yield SSE-ready summary frames for a sampled report set.

    `total` is the size of the filtered set the sample came from; `stats_digest`
    holds the dashboard figures the meta summary is grounded in. Frame shapes:

      {"kind": "prose", "text": "..."}                                  (small-N)
      {"kind": "scope", "sampled": N, "total": M, "other_count": K}     (large-N header)
      {"kind": "cluster", "label": "...", "count": N,
       "sentences": "...", "exemplars": [...]}                          (large-N)
      {"kind": "meta", "text": "..."}                                   (large-N tail)
      {"kind": "error", "message": "..."}                               (graceful failure)
    """
    if not hits:
        yield {"kind": "prose", "text": "No reports in the current view."}
        return

    try:
        clients.require_text()
    except AIClientUnavailableError as exc:
        yield {"kind": "error", "message": str(exc)}
        return

    if len(hits) <= SINGLE_PASS_THRESHOLD:
        async for frame in _summarise_single_pass(hits, clients=clients):
            yield frame
        return

    # KMeans is CPU-bound; keep it off the event loop.
    clusters = await asyncio.to_thread(
        _kmeans_clusters,
        hits,
        embeddings_by_id,
        k=target_cluster_count(len(hits), THEME_COUNT_CAP),
    )

    # Tells the coordinator this is a sample, not a census. other_count is the
    # hits with no embedding yet.
    kept = sum(len(c.members) for c in clusters)
    yield {
        "kind": "scope",
        "sampled": len(hits),
        "total": total if total is not None else len(hits),
        "other_count": max(len(hits) - kept, 0),
    }

    # Runs alongside the summaries and outside the semaphore so it can't starve.
    label_task = asyncio.ensure_future(_contrastive_labels(clusters, clients=clients))
    sem = asyncio.Semaphore(_REPORT_CONCURRENCY)

    async def _summ(idx: int, c: _RawCluster) -> tuple[int, str]:
        async with sem:
            return idx, await _summarise_one(c, clients=clients)

    tasks = [asyncio.ensure_future(_summ(i, c)) for i, c in enumerate(clusters)]
    labels = await label_task
    cluster_summaries: list[ClusterSummary] = []
    for fut in asyncio.as_completed(tasks):
        idx, sentences = await fut
        c = clusters[idx]
        summary = ClusterSummary(
            label=labels[idx],
            count=len(c.members),
            sentences=sentences,
            exemplars=_pick_exemplars(c),
        )
        cluster_summaries.append(summary)
        yield {
            "kind": "cluster",
            "label": summary.label,
            "count": summary.count,
            "sentences": summary.sentences,
            "exemplars": summary.exemplars,
        }

    meta = await _meta_summary(cluster_summaries, clients=clients, stats_digest=stats_digest)
    yield {"kind": "meta", "text": meta}


async def _summarise_single_pass(
    hits: list[dict[str, Any]], *, clients: AIClients
) -> AsyncIterator[dict[str, Any]]:
    cfg = clients.require_text()
    user_payload = "\n\n".join(_format_hit_for_prompt(h) for h in hits)
    try:
        text = await complete_text(
            cfg,
            prompts.SUMMARY_SINGLE_PASS_SYSTEM,
            user_payload,
            max_tokens=_MAX_OUTPUT_TOKENS,
            temperature=0.2,
        )
    except Exception as exc:
        logger.warning("summarise.single_pass.error: %s", exc)
        yield {"kind": "error", "message": "summary failed; try again"}
        return
    yield {"kind": "prose", "text": text}


@dataclass
class _RawCluster:
    centroid: list[float]
    members: list[dict[str, Any]]
    member_embeddings: list[list[float]]


def _kmeans_clusters(
    hits: list[dict[str, Any]],
    embeddings_by_id: dict[uuid.UUID, list[float]],
    *,
    k: int,
) -> list[_RawCluster]:
    """Spherical k-means into up to `k` clusters, largest first.

    Hits without an embedding are left out; callers count them as `other_count`.
    """
    indexed_hits: list[dict[str, Any]] = []
    indexed_vectors: list[list[float]] = []
    for hit in hits:
        vec = embeddings_by_id.get(hit["id"])
        if vec is not None:
            indexed_hits.append(hit)
            indexed_vectors.append(vec)

    if not indexed_vectors:
        return []

    # k-means needs k <= n_samples.
    effective_k = max(1, min(k, len(indexed_vectors)))
    matrix = l2_normalize(np.asarray(indexed_vectors, dtype=np.float32))
    if effective_k == 1:
        labels: list[Any] = [0] * len(indexed_vectors)
    else:
        clusterer = KMeans(
            n_clusters=effective_k,
            n_init=_KMEANS_N_INIT,  # pyright: ignore[reportArgumentType]  # stub types n_init as str-only
            random_state=_KMEANS_RANDOM_STATE,
        )
        labels_raw: Any = clusterer.fit_predict(matrix)  # pyright: ignore[reportUnknownMemberType, reportUnknownVariableType]
        labels = list(labels_raw.tolist())  # pyright: ignore[reportUnknownArgumentType, reportUnknownMemberType]

    by_label: dict[int, list[int]] = {}
    for idx, label in enumerate(labels):
        by_label.setdefault(int(label), []).append(idx)

    clusters: list[_RawCluster] = []
    for indices in by_label.values():
        members = [indexed_hits[i] for i in indices]
        vectors = [indexed_vectors[i] for i in indices]
        centroid = np.mean(np.asarray(vectors, dtype=np.float32), axis=0).tolist()
        clusters.append(_RawCluster(centroid=centroid, members=members, member_embeddings=vectors))
    clusters.sort(key=lambda c: -len(c.members))
    return clusters


def _cosine_distance(a: list[float], b: list[float]) -> float:
    if not a or not b:
        return 1.0
    av = np.asarray(a, dtype=np.float32)
    bv = np.asarray(b, dtype=np.float32)
    na = float(np.linalg.norm(av))
    nb = float(np.linalg.norm(bv))
    if na == 0.0 or nb == 0.0:
        return 1.0
    return 1.0 - float(np.dot(av, bv)) / (na * nb)


def _centroid_ranked_members(cluster: _RawCluster) -> list[dict[str, Any]]:
    """Members closest to the centroid first; original order if there is no centroid."""
    if not cluster.centroid or not cluster.member_embeddings:
        return list(cluster.members)
    centroid = cluster.centroid
    ranked = sorted(
        zip(cluster.members, cluster.member_embeddings, strict=False),
        key=lambda pair: _cosine_distance(pair[1], centroid) if pair[1] else 1.0,
    )
    return [m for m, _ in ranked]


# Labels come from one call that sees every cluster, since clusters labelled in
# isolation collide on near-identical generic names.


async def _summarise_one(cluster: _RawCluster, *, clients: AIClients) -> str:
    cfg = clients.require_text()
    sample = "\n\n".join(
        _format_hit_for_prompt(m) for m in _centroid_ranked_members(cluster)[:_PROMPT_SAMPLE_SIZE]
    )
    try:
        return await complete_text(
            cfg, prompts.CLUSTER_SUMMARY_SYSTEM, sample, max_tokens=256, temperature=0.2
        )
    except Exception as exc:
        logger.warning("summarise.cluster.summary_error: %s", exc)
        return f"{len(cluster.members)} reports tagged in this cluster."


async def _label_one(cluster: _RawCluster, *, clients: AIClients) -> str:
    cfg = clients.require_text()
    sample = "\n\n".join(
        _format_hit_for_prompt(m) for m in _centroid_ranked_members(cluster)[:_PROMPT_SAMPLE_SIZE]
    )
    try:
        label = await complete_text(
            cfg, prompts.CLUSTER_LABEL_SYSTEM, sample, max_tokens=24, temperature=0.0
        )
        return (label or "topic").strip('"')
    except Exception as exc:
        logger.warning("summarise.cluster.label_error: %s", exc)
        return "topic"


async def _contrastive_labels(clusters: list[_RawCluster], *, clients: AIClients) -> list[str]:
    """Labels aligned to `clusters`; falls back to per-cluster labels on any failure."""
    if not clusters:
        return []
    cfg = clients.require_text()
    blocks: list[str] = []
    for i, c in enumerate(clusters):
        members = _centroid_ranked_members(c)[:_LABEL_PROMPT_SAMPLE_SIZE]
        body = "\n".join(_format_hit_for_label(m) for m in members)
        blocks.append(f"## Cluster {i + 1} ({len(c.members)} reports)\n{body}")
    payload = f"There are {len(clusters)} clusters. Label each.\n\n" + "\n\n".join(blocks)

    try:
        raw = await complete_text(
            cfg, prompts.CONTRASTIVE_LABEL_SYSTEM, payload, max_tokens=256, temperature=0.0
        )
        labels = _parse_contrastive_labels(raw, expected=len(clusters))
        if labels is not None:
            return labels
        logger.warning("summarise.contrastive_labels.parse_failed: %r", raw[:200])
    except Exception as exc:
        logger.warning("summarise.contrastive_labels.error: %s", exc)

    return list(await asyncio.gather(*(_label_one(c, clients=clients) for c in clusters)))


def _parse_contrastive_labels(raw: str, *, expected: int) -> list[str] | None:
    """Return None unless the reply holds `{"labels": [...]}` with `expected` non-empty strings."""
    text = raw.strip()
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end < start:
        return None
    try:
        parsed = json.loads(text[start : end + 1])
    except (ValueError, TypeError):
        return None
    if not isinstance(parsed, dict):
        return None
    labels = cast("dict[str, Any]", parsed).get("labels")
    if not isinstance(labels, list):
        return None
    items = cast("list[Any]", labels)
    if len(items) != expected:
        return None
    cleaned: list[str] = []
    for item in items:
        if not isinstance(item, str):
            return None
        s = item.strip().strip('"').strip()
        if not s:
            return None
        cleaned.append(s)
    return cleaned


def _format_hit_for_label(hit: dict[str, Any]) -> str:
    """One truncated line per hit, without the report id so the model doesn't cite."""
    parts = report_detail_lines(hit, maxlen=_LABEL_TEXT_MAXLEN, include_debris=False)
    return " | ".join(parts) if parts else "(no detail)"


def _pick_exemplars(cluster: _RawCluster) -> list[dict[str, Any]]:
    """Centroid-closest members, photo-backed first on ties."""

    def _snapshot(m: dict[str, Any]) -> dict[str, Any]:
        return {
            "id": str(m["id"]),
            "damage_class": m.get("damage_class"),
            "description": m.get("description") or m.get("description_en"),
            "building_name": m.get("building_name"),
        }

    if not cluster.centroid or not cluster.member_embeddings:
        return [_snapshot(m) for m in cluster.members[:_EXEMPLARS_PER_CLUSTER]]
    centroid = cluster.centroid
    ranked = sorted(
        zip(cluster.members, cluster.member_embeddings, strict=False),
        key=lambda pair: (
            _cosine_distance(pair[1], centroid) if pair[1] else 1.0,
            0 if pair[0].get("photo_path") else 1,
        ),
    )
    return [_snapshot(m) for m, _ in ranked[:_EXEMPLARS_PER_CLUSTER]]


async def _meta_summary(
    clusters: list[ClusterSummary],
    *,
    clients: AIClients,
    stats_digest: dict[str, Any],
) -> str:
    """Overview paragraph, grounded in `stats_digest`."""
    works = [
        _ThemeWork(label=c.label, count=c.count, quote=None, sentences=c.sentences)
        for c in clusters
    ]
    return await _synthesise_section(
        "headline", stats_digest=stats_digest, works=works, clients=clients
    )


# Batch report sections: crisis-wide headline and per-district write-ups.


@dataclass(frozen=True, slots=True)
class SummaryTheme:
    label: str
    count: int
    quote: str | None


@dataclass(frozen=True, slots=True)
class SectionSummary:
    prose: str  # synthesized paragraph, or the small-N single-pass prose
    themes: list[SummaryTheme]
    sampled: int  # reports actually fed to the summariser
    other_count: int  # reports with no embedding yet


@dataclass(slots=True)
class _ThemeWork:
    """`sentences` feeds the section synthesis and is not displayed per theme."""

    label: str
    count: int
    quote: str | None
    sentences: str


_SYNTH_SYSTEM = {
    "headline": prompts.HEADLINE_SYNTH_SYSTEM,
    "district": prompts.DISTRICT_SYNTH_SYSTEM,
}


async def summarise_section(
    hits: list[dict[str, Any]],
    embeddings_by_id: dict[uuid.UUID, list[float]],
    *,
    clients: AIClients,
    top_k: int,
    stats_digest: dict[str, Any],
    synthesis_kind: str,
) -> SectionSummary:
    """Summarise one sampled set into up to `top_k` themes plus a synthesised paragraph.

    `synthesis_kind` picks the headline or district prompt. Returns an empty
    section instead of raising when there is no client, so the report always renders.
    """
    n = len(hits)
    if n == 0:
        return SectionSummary(prose="", themes=[], sampled=0, other_count=0)
    try:
        clients.require_text()
    except AIClientUnavailableError as exc:
        logger.info("summarise_section.no_client: %s", exc)
        return SectionSummary(prose="", themes=[], sampled=n, other_count=0)

    if n < SINGLE_PASS_THRESHOLD:
        prose = await _single_pass_prose(hits, clients=clients)
        return SectionSummary(prose=prose, themes=[], sampled=n, other_count=0)

    clusters = await asyncio.to_thread(
        _kmeans_clusters, hits, embeddings_by_id, k=target_cluster_count(n, top_k)
    )

    # Same fan-out as `summarise`.
    label_task = asyncio.ensure_future(_contrastive_labels(clusters, clients=clients))
    sem = asyncio.Semaphore(_REPORT_CONCURRENCY)

    async def _summ(c: _RawCluster) -> str:
        async with sem:
            return await _summarise_one(c, clients=clients)

    summaries = await asyncio.gather(*(_summ(c) for c in clusters))
    labels = await label_task
    works = [
        _ThemeWork(
            label=labels[i],
            count=len(c.members),
            quote=_pick_quote(c),
            sentences=summaries[i],
        )
        for i, c in enumerate(clusters)
    ]
    other_count = n - sum(w.count for w in works)

    prose = await _synthesise_section(
        synthesis_kind, stats_digest=stats_digest, works=works, clients=clients
    )
    themes = [SummaryTheme(label=w.label, count=w.count, quote=w.quote) for w in works]
    return SectionSummary(prose=prose, themes=themes, sampled=n, other_count=max(other_count, 0))


async def _single_pass_prose(hits: list[dict[str, Any]], *, clients: AIClients) -> str:
    cfg = clients.require_text()
    user_payload = "\n\n".join(_format_hit_for_prompt(h) for h in hits)
    try:
        return await complete_text(
            cfg,
            prompts.SUMMARY_SINGLE_PASS_SYSTEM,
            user_payload,
            max_tokens=_MAX_OUTPUT_TOKENS,
            temperature=0.2,
        )
    except Exception as exc:
        logger.warning("summarise_section.single_pass_error: %s", exc)
        return ""


def _pick_quote(cluster: _RawCluster) -> str | None:
    """Description of the centroid-closest member with any text, English when available."""
    for m in _centroid_ranked_members(cluster):
        text = (m.get("description_en") or m.get("description") or "").strip()
        if text:
            return text
    return None


async def _synthesise_section(
    kind: str,
    *,
    stats_digest: dict[str, Any],
    works: list[_ThemeWork],
    clients: AIClients,
) -> str:
    cfg = clients.require_text()
    system = _SYNTH_SYSTEM.get(kind, prompts.HEADLINE_SYNTH_SYSTEM)
    payload = json.dumps(
        {
            "stats": stats_digest,
            "themes": [{"label": w.label, "count": w.count, "summary": w.sentences} for w in works],
        },
        separators=(",", ":"),
        default=str,
    )
    try:
        return await complete_text(cfg, system, payload, max_tokens=320, temperature=0.2)
    except Exception as exc:
        logger.warning("summarise_section.synth_error: %s", exc)
        return ""


def _format_hit_for_prompt(hit: dict[str, Any]) -> str:
    """Mirrors the document the embed job builds, so the LLM sees what the index saw."""
    return "\n".join([*report_detail_lines(hit), f"report_id: {hit['id']}"])


__all__ = [
    "THEME_COUNT_CAP",
    "SectionSummary",
    "summarise",
    "summarise_section",
]
