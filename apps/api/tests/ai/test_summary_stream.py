"""Unit tests for the streaming dashboard summary (`api.ai.summarization.summarise`)
and the route-level sampling helpers.

`summarise` is exercised with a fake OpenAI-compatible client so no network is
touched: we assert the frame contract (a leading `scope` provenance frame, a
cluster frame per dense blob, a terminal stats-grounded `meta` frame) and that
the meta synthesis is fed the stats digest. The pure helpers
(`seed_from_signature`, `_summary_stats_digest`) are tested directly.
"""

from __future__ import annotations

import uuid
from typing import Any

import numpy as np

from api.admin.search_queries import seed_from_signature
from api.admin.search_routes import _summary_stats_digest  # pyright: ignore[reportPrivateUsage]
from api.ai.client import AIClients, ConfiguredClient
from api.ai.summarization import summarise
from api.schemas.admin_search import SearchStats

# --- Fake OpenAI-compatible text client ---------------------------------


class _FakeMessage:
    def __init__(self, content: str) -> None:
        self.content = content


class _FakeChoice:
    def __init__(self, content: str) -> None:
        self.message = _FakeMessage(content)


class _FakeResponse:
    def __init__(self, content: str) -> None:
        self.choices = [_FakeChoice(content)]


class _FakeCompletions:
    def __init__(self, sink: list[dict[str, Any]]) -> None:
        self._sink = sink

    async def create(self, **kwargs: Any) -> _FakeResponse:
        self._sink.append(kwargs)
        return _FakeResponse("canned summary text")


class _FakeChat:
    def __init__(self, sink: list[dict[str, Any]]) -> None:
        self.completions = _FakeCompletions(sink)


class _FakeClient:
    def __init__(self, sink: list[dict[str, Any]]) -> None:
        self.chat = _FakeChat(sink)


def _fake_clients(sink: list[dict[str, Any]]) -> AIClients:
    cfg = ConfiguredClient(client=_FakeClient(sink), model="fake-model")  # pyright: ignore[reportArgumentType]
    return AIClients(text=cfg, embedding=None)


# --- Synthetic data ------------------------------------------------------


def _gaussian_blob(center: list[float], n: int, dim: int = 16) -> list[list[float]]:
    rng = np.random.default_rng(seed=hash(tuple(center)) & 0xFFFFFFFF)
    base = np.asarray(center, dtype=np.float32)
    points = rng.normal(loc=0.0, scale=0.05, size=(n, dim)).astype(np.float32) + base
    return [p.tolist() for p in points]


def _make_hits(count: int) -> list[dict[str, Any]]:
    return [
        {
            "id": uuid.uuid4(),
            "description": f"report {i}",
            "description_en": f"report {i}",
            "photo_path": f"x/{i}.jpg",
            "damage_class": "partial",
        }
        for i in range(count)
    ]


def _two_blob_set() -> tuple[list[dict[str, Any]], dict[uuid.UUID, list[float]]]:
    dim = 16
    blob_a = _gaussian_blob([1.0] + [0.0] * (dim - 1), n=40)
    blob_b = _gaussian_blob([0.0] * (dim - 1) + [1.0], n=40)
    vectors = blob_a + blob_b
    hits = _make_hits(len(vectors))
    embeddings = {h["id"]: v for h, v in zip(hits, vectors, strict=True)}
    return hits, embeddings


# --- Tests ---------------------------------------------------------------


async def test_large_n_emits_scope_then_clusters_then_meta() -> None:
    hits, embeddings = _two_blob_set()
    sink: list[dict[str, Any]] = []
    frames = [
        f
        async for f in summarise(
            hits,
            embeddings,
            clients=_fake_clients(sink),
            total=5000,
            stats_digest={"total_matched": 5000, "damage_mix": {"partial": 5000}},
        )
    ]

    # Leading provenance frame: sampled = the set we fed, total = the full set.
    assert frames[0]["kind"] == "scope"
    assert frames[0]["sampled"] == len(hits)
    assert frames[0]["total"] == 5000
    assert frames[0]["other_count"] >= 0
    assert frames[0]["sampled"] - frames[0]["other_count"] >= 0

    # At least the two dense blobs surface as cluster frames.
    cluster_frames = [f for f in frames if f["kind"] == "cluster"]
    assert len(cluster_frames) >= 2
    assert all(c["count"] > 0 for c in cluster_frames)

    # Terminal meta frame.
    assert frames[-1]["kind"] == "meta"


async def test_meta_synthesis_is_grounded_in_stats_digest() -> None:
    hits, embeddings = _two_blob_set()
    sink: list[dict[str, Any]] = []
    _ = [
        f
        async for f in summarise(
            hits,
            embeddings,
            clients=_fake_clients(sink),
            total=5000,
            stats_digest={"total_matched": 5000, "damage_mix": {"partial": 5000}},
        )
    ]
    # The synthesis call carries the digest in its user payload (the JSON with a
    # top-level "stats" object), so the prose can cite the full-set figures.
    synth_calls = [
        c
        for c in sink
        if any('"stats"' in m.get("content", "") for m in c["messages"] if m["role"] == "user")
    ]
    assert synth_calls, "expected a stats-grounded synthesis call"
    assert any("5000" in m["content"] for m in synth_calls[0]["messages"] if m["role"] == "user")


async def test_small_n_is_prose_only_no_scope() -> None:
    hits = _make_hits(5)
    embeddings = {h["id"]: [0.1] * 16 for h in hits}
    sink: list[dict[str, Any]] = []
    frames = [
        f async for f in summarise(hits, embeddings, clients=_fake_clients(sink), stats_digest={})
    ]

    assert len(frames) == 1
    assert frames[0]["kind"] == "prose"
    assert frames[0]["text"] == "canned summary text"


async def test_empty_hits_yields_placeholder_prose() -> None:
    sink: list[dict[str, Any]] = []
    frames = [f async for f in summarise([], {}, clients=_fake_clients(sink), stats_digest={})]
    assert frames == [{"kind": "prose", "text": "No reports in the current view."}]
    assert sink == []  # no LLM call for an empty view


def test_summary_stats_digest_carries_full_set_figures() -> None:
    stats = SearchStats(
        total=120,
        severity={"complete": 10, "partial": 50, "minimal": 60},
        last_24h=30,
        last_hour=4,
        top_infra="water",
        top_infra_count=42,
        debris_yes=12,
        debris_known=80,
        with_building=70,
        with_gps=90,
        with_geocode=5,
        unmapped=8,
        infra_breakdown={"water": 42, "power": 18},
        unique_devices=33,
        buildings_affected=55,
        buildings_total=900,
    )
    # `total` is passed separately (the true full-set count) and wins over
    # whatever `stats.total` happens to hold.
    digest = _summary_stats_digest(stats, total=8432)
    assert digest["total_matched"] == 8432
    assert digest["damage_mix"] == {"complete": 10, "partial": 50, "minimal": 60}
    assert digest["top_infrastructure"] == {"type": "water", "reports": 42}
    assert digest["infrastructure_breakdown"] == {"water": 42, "power": 18}
    assert digest["buildings_affected"] == 55


def test_summary_stats_digest_omits_absent_infra() -> None:
    stats = SearchStats(
        total=3,
        severity={"complete": 0, "partial": 1, "minimal": 2},
        last_24h=3,
        last_hour=1,
        top_infra=None,
        top_infra_count=0,
        debris_yes=0,
        debris_known=0,
        with_building=0,
        with_gps=1,
        with_geocode=0,
        unmapped=2,
    )
    digest = _summary_stats_digest(stats, total=3)
    assert "top_infrastructure" not in digest
    assert "infrastructure_breakdown" not in digest


def test_seed_from_signature_is_deterministic_and_in_range() -> None:
    sig = "0f1e2d3c4b5a6978"
    seed = seed_from_signature(sig)
    assert seed == seed_from_signature(sig)  # deterministic
    assert -1.0 <= seed < 1.0
    # Distinct signatures generally map to distinct seeds.
    assert seed_from_signature("ffffffff00000000") != seed
