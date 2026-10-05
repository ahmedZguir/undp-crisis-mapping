"""Unit tests for the `halfvec` literal helpers.

These two helpers are the single source of truth for the
`halfvec(2560)` text representation; every embed site, search site, and
chat top-K site renders / parses through them. Drift here would corrupt
the embedding round-trip, so the round-trip + edge cases are covered
locally.
"""

from __future__ import annotations

import numpy as np

from api.ai import halfvec


def test_literal_round_trips_plain_list() -> None:
    vec = [0.1, -0.2, 3.0, 0.0]
    text = halfvec.literal(vec)
    assert text.startswith("[")
    assert text.endswith("]")
    parsed = halfvec.parse(text)
    assert parsed == [0.1, -0.2, 3.0, 0.0]


def test_literal_accepts_numpy_scalars() -> None:
    arr = np.asarray([0.5, 0.25], dtype=np.float32)
    text = halfvec.literal(list(arr))
    parsed = halfvec.parse(text)
    assert len(parsed) == 2
    assert abs(parsed[0] - 0.5) < 1e-6
    assert abs(parsed[1] - 0.25) < 1e-6


def test_parse_handles_empty_brackets() -> None:
    assert halfvec.parse("[]") == []
    assert halfvec.parse("  []  ") == []


def test_parse_tolerates_unbracketed_body() -> None:
    # pgvector always brackets, but be defensive in case a future test
    # fixture hands us the bare body — the helper should still produce
    # a list rather than raising.
    assert halfvec.parse("0.1, 0.2") == [0.1, 0.2]
