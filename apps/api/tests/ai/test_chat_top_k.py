"""Unit tests for chat top-K + centroid-top-K ranking.

The ranking functions back the chat surface's first-message lock. The
NumPy refactor needs to preserve the contract from the previous
hand-rolled cosine implementation:

- Hits without embeddings sort to the tail (distance 1.0).
- Top-K against a query vector ranks by cosine similarity.
- The centroid fallback ranks against the mean of the available
  vectors.
"""

from __future__ import annotations

import uuid

from api.ai.chat import centroid_top_k, select_top_k


def test_select_top_k_ranks_by_cosine_similarity() -> None:
    near = uuid.uuid4()
    middle = uuid.uuid4()
    far = uuid.uuid4()
    query = [1.0, 0.0, 0.0]
    embeddings = {
        near: [1.0, 0.0, 0.0],
        middle: [0.7, 0.7, 0.0],
        far: [-1.0, 0.0, 0.0],
    }
    out = select_top_k([near, middle, far], embeddings, query, k=3)
    assert out == [near, middle, far]


def test_select_top_k_pushes_orphans_to_tail() -> None:
    orphan = uuid.uuid4()
    indexed = uuid.uuid4()
    out = select_top_k([orphan, indexed], {indexed: [1.0, 0.0]}, [1.0, 0.0], k=2)
    # Indexed hit ranks higher; orphan falls behind at distance 1.0.
    assert out[0] == indexed
    assert out[1] == orphan


def test_centroid_top_k_uses_mean_of_available_vectors() -> None:
    rid_a = uuid.uuid4()
    rid_b = uuid.uuid4()
    rid_c = uuid.uuid4()
    # `rid_a` and `rid_b` cluster tightly around (1,0); `rid_c` is at (-1,0).
    embeddings = {
        rid_a: [1.0, 0.0],
        rid_b: [0.9, 0.1],
        rid_c: [-1.0, 0.0],
    }
    # Take the full ranking so rid_c lands in the result and we can
    # assert "the centroid is dragged toward the dense cluster, so the
    # outlier sorts last."
    out = centroid_top_k([rid_a, rid_b, rid_c], embeddings, k=3)
    assert out[-1] == rid_c
    assert set(out[:2]) == {rid_a, rid_b}


def test_centroid_top_k_returns_prefix_when_no_vectors() -> None:
    rid_a = uuid.uuid4()
    rid_b = uuid.uuid4()
    out = centroid_top_k([rid_a, rid_b], {}, k=1)
    assert out == [rid_a]
