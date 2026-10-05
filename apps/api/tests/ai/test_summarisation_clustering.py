"""Unit tests for `api.ai.summarization._kmeans_clusters`.

The clustering function is the core of the large-N summary pipeline. We feed it
deterministic synthetic embeddings (well-separated gaussian blobs) and check
that:

- k-means recovers the blobs as separate clusters.
- Every embedded hit lands in exactly one cluster — there is no noise residual
  (the property that fixed the all-noise collapse on broad crisis data).
- Hits without an embedding (embed-queue lag) are excluded from clustering, not
  silently mixed into a theme.
- `k` is clamped to the number of points, so a small set never errors.
"""

from __future__ import annotations

import asyncio
import uuid
from types import SimpleNamespace
from typing import Any

import numpy as np

from api.ai.summarization import (
    _centroid_ranked_members,  # pyright: ignore[reportPrivateUsage]
    _contrastive_labels,  # pyright: ignore[reportPrivateUsage]
    _kmeans_clusters,  # pyright: ignore[reportPrivateUsage]
    _parse_contrastive_labels,  # pyright: ignore[reportPrivateUsage]
    _RawCluster,  # pyright: ignore[reportPrivateUsage]
    target_cluster_count,
)


def test_target_cluster_count_floors_caps_and_scales() -> None:
    # clamp(n // 25, 2, cap): floors at 2, caps at `cap`, scales between.
    assert target_cluster_count(0, 12) == 2  # floor
    assert target_cluster_count(30, 12) == 2  # 30 // 25 = 1 -> floored to 2
    assert target_cluster_count(300, 12) == 12  # 300 // 25 = 12 (at cap)
    assert target_cluster_count(5000, 12) == 12  # well above cap -> cap
    assert target_cluster_count(5000, 10) == 10  # cap honoured per surface
    assert target_cluster_count(250, 12) == 10  # 250 // 25 = 10 (between)


def _make_hits(count: int, *, photo_path_prefix: str = "x") -> list[dict[str, Any]]:
    return [
        {"id": uuid.uuid4(), "photo_path": f"{photo_path_prefix}/{i}.jpg"} for i in range(count)
    ]


def _gaussian_blob(center: list[float], n: int, dim: int = 16) -> list[list[float]]:
    rng = np.random.default_rng(seed=hash(tuple(center)) & 0xFFFFFFFF)
    base = np.asarray(center, dtype=np.float32)
    points = rng.normal(loc=0.0, scale=0.05, size=(n, dim)).astype(np.float32) + base
    return [p.tolist() for p in points]


def test_separates_two_dense_blobs() -> None:
    dim = 16
    blob_a = _gaussian_blob([1.0] + [0.0] * (dim - 1), n=40)
    blob_b = _gaussian_blob([0.0] * (dim - 1) + [1.0], n=40)
    hits = _make_hits(len(blob_a) + len(blob_b))
    vectors = blob_a + blob_b
    embeddings_by_id = {hit["id"]: vec for hit, vec in zip(hits, vectors, strict=True)}

    clusters = _kmeans_clusters(hits, embeddings_by_id, k=2)

    assert len(clusters) == 2
    sizes = sorted((len(c.members) for c in clusters), reverse=True)
    # The two blobs are well separated, so k=2 recovers them roughly intact.
    assert sizes == [40, 40]


def test_every_embedded_hit_is_assigned_no_residual() -> None:
    """k-means assigns every embedded hit to a cluster — the all-noise collapse
    that motivated the switch (HDBSCAN labelled the whole sample noise) cannot
    happen here."""
    dim = 16
    blob_a = _gaussian_blob([1.0] + [0.0] * (dim - 1), n=60)
    blob_b = _gaussian_blob([0.0] * (dim - 1) + [1.0], n=60)
    hits = _make_hits(len(blob_a) + len(blob_b))
    vectors = blob_a + blob_b
    embeddings_by_id = {hit["id"]: vec for hit, vec in zip(hits, vectors, strict=True)}

    clusters = _kmeans_clusters(hits, embeddings_by_id, k=8)

    assigned = sum(len(c.members) for c in clusters)
    assert assigned == len(hits)


def test_clusters_returned_largest_first() -> None:
    dim = 16
    # An imbalanced split so ordering is observable.
    big = _gaussian_blob([1.0] + [0.0] * (dim - 1), n=80)
    small = _gaussian_blob([0.0] * (dim - 1) + [1.0], n=20)
    hits = _make_hits(len(big) + len(small))
    vectors = big + small
    embeddings_by_id = {hit["id"]: vec for hit, vec in zip(hits, vectors, strict=True)}

    clusters = _kmeans_clusters(hits, embeddings_by_id, k=2)

    sizes = [len(c.members) for c in clusters]
    assert sizes == sorted(sizes, reverse=True)


def test_orphans_are_excluded() -> None:
    dim = 16
    blob = _gaussian_blob([1.0] + [0.0] * (dim - 1), n=40)
    hits = _make_hits(len(blob) + 5)  # 5 orphans without embeddings
    embeddings_by_id = {hit["id"]: vec for hit, vec in zip(hits[: len(blob)], blob, strict=True)}

    clusters = _kmeans_clusters(hits, embeddings_by_id, k=3)

    clustered_ids = {m["id"] for c in clusters for m in c.members}
    orphan_ids = {h["id"] for h in hits[len(blob) :]}
    assert clustered_ids == {h["id"] for h in hits[: len(blob)]}
    assert clustered_ids.isdisjoint(orphan_ids)


def test_k_clamped_to_sample_size() -> None:
    """Requesting more clusters than points must not raise."""
    dim = 16
    blob = _gaussian_blob([1.0] + [0.0] * (dim - 1), n=3)
    hits = _make_hits(len(blob))
    embeddings_by_id = {hit["id"]: vec for hit, vec in zip(hits, blob, strict=True)}

    clusters = _kmeans_clusters(hits, embeddings_by_id, k=12)

    assert 1 <= len(clusters) <= 3
    assert sum(len(c.members) for c in clusters) == 3


def test_empty_input_returns_no_clusters() -> None:
    assert _kmeans_clusters([], {}, k=12) == []
    # All-orphan input (no embeddings) also yields nothing to cluster.
    hits = _make_hits(4)
    assert _kmeans_clusters(hits, {}, k=12) == []


def test_centroid_ranked_members_orders_closest_first() -> None:
    # Centroid is the unit x-axis; members at varying angles away from it. The
    # one aligned with the centroid must rank first, the most orthogonal last.
    centroid = [1.0, 0.0]
    members = [
        {"id": "far"},
        {"id": "near"},
        {"id": "mid"},
    ]
    embeddings = [
        [0.0, 1.0],  # orthogonal -> farthest
        [1.0, 0.0],  # aligned -> closest
        [1.0, 1.0],  # 45 deg -> middle
    ]
    cluster = _RawCluster(centroid=centroid, members=members, member_embeddings=embeddings)

    ranked = _centroid_ranked_members(cluster)

    assert [m["id"] for m in ranked] == ["near", "mid", "far"]


def test_centroid_ranked_members_falls_back_to_original_order() -> None:
    members = [{"id": "a"}, {"id": "b"}]
    # No centroid / no embeddings (e.g. all-orphan cluster) -> original order.
    cluster = _RawCluster(centroid=[], members=members, member_embeddings=[])
    assert _centroid_ranked_members(cluster) == members


# --- Contrastive labelling ----------------------------------------------


def test_parse_contrastive_labels_valid() -> None:
    raw = '{"labels": ["Collapsed Walls", "Blocked Roads"]}'
    assert _parse_contrastive_labels(raw, expected=2) == ["Collapsed Walls", "Blocked Roads"]


def test_parse_contrastive_labels_tolerates_fences_and_prose() -> None:
    raw = 'Sure, here you go:\n```json\n{"labels": ["A B", "C D"]}\n```\nDone.'
    assert _parse_contrastive_labels(raw, expected=2) == ["A B", "C D"]


def test_parse_contrastive_labels_strips_quotes() -> None:
    assert _parse_contrastive_labels('{"labels": ["\\"Quoted\\"", "Plain"]}', expected=2) == [
        "Quoted",
        "Plain",
    ]


def test_parse_contrastive_labels_rejects_wrong_count() -> None:
    assert _parse_contrastive_labels('{"labels": ["only one"]}', expected=2) is None


def test_parse_contrastive_labels_rejects_non_object_and_missing_key() -> None:
    assert _parse_contrastive_labels("no json at all", expected=1) is None
    assert _parse_contrastive_labels('["A", "B"]', expected=2) is None  # array, no `{`
    assert _parse_contrastive_labels('{"other": ["A"]}', expected=1) is None


def test_parse_contrastive_labels_rejects_non_string_or_empty_items() -> None:
    assert _parse_contrastive_labels('{"labels": ["A", 3]}', expected=2) is None
    assert _parse_contrastive_labels('{"labels": ["A", "   "]}', expected=2) is None


def _resp(content: str) -> Any:
    """Minimal stand-in for an OpenAI chat-completion response object."""
    return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=content))])


class _FakeClients:
    """Fake `AIClients` whose `create` is driven by a per-call `handler`.

    `handler(kwargs)` receives the `create(...)` kwargs and returns a response
    (or raises). It can branch on the system prompt to behave differently for the
    contrastive call vs the per-cluster fallback call.
    """

    def __init__(self, handler: Any) -> None:
        create = self._make_create(handler)
        completions = SimpleNamespace(create=create)
        client = SimpleNamespace(chat=SimpleNamespace(completions=completions))
        self._cfg = SimpleNamespace(client=client, model="fake-model")

    @staticmethod
    def _make_create(handler: Any) -> Any:
        async def create(**kwargs: Any) -> Any:
            return handler(kwargs)

        return create

    def require_text(self) -> Any:
        return self._cfg


def _label_cluster(desc: str, n: int = 3) -> _RawCluster:
    members = [{"id": uuid.uuid4(), "description": desc} for _ in range(n)]
    return _RawCluster(centroid=[], members=members, member_embeddings=[])


def test_contrastive_labels_returns_parsed_labels() -> None:
    clusters = [_label_cluster("collapsed wall"), _label_cluster("blocked road")]

    def handler(_: dict[str, Any]) -> Any:
        return _resp('{"labels": ["Collapsed Walls", "Blocked Roads"]}')

    labels = asyncio.run(_contrastive_labels(clusters, clients=_FakeClients(handler)))
    assert labels == ["Collapsed Walls", "Blocked Roads"]


def test_contrastive_labels_falls_back_when_call_errors() -> None:
    clusters = [_label_cluster("a"), _label_cluster("b")]

    def handler(kwargs: dict[str, Any]) -> Any:
        system = kwargs["messages"][0]["content"]
        if "numbered clusters" in system:  # the contrastive call
            raise RuntimeError("endpoint down")
        return _resp("Per-Cluster Label")  # the per-cluster fallback

    labels = asyncio.run(_contrastive_labels(clusters, clients=_FakeClients(handler)))
    assert labels == ["Per-Cluster Label", "Per-Cluster Label"]


def test_contrastive_labels_falls_back_on_bad_parse() -> None:
    clusters = [_label_cluster("a"), _label_cluster("b")]

    def handler(kwargs: dict[str, Any]) -> Any:
        system = kwargs["messages"][0]["content"]
        if "numbered clusters" in system:
            return _resp('{"labels": ["only one"]}')  # count mismatch -> reject
        return _resp("FB")

    labels = asyncio.run(_contrastive_labels(clusters, clients=_FakeClients(handler)))
    assert labels == ["FB", "FB"]


def test_contrastive_labels_empty_input() -> None:
    def handler(_: dict[str, Any]) -> Any:  # pragma: no cover - never called
        raise AssertionError("should not call the LLM for empty input")

    assert asyncio.run(_contrastive_labels([], clients=_FakeClients(handler))) == []
