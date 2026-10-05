"""Parser-only tests for `api.ai.relevance._parse_response`."""

from __future__ import annotations

import pytest

from api.ai.llm import LLMOutputError
from api.ai.relevance import (
    _parse_response,  # pyright: ignore[reportPrivateUsage]
)


def test_relevant_label_parses() -> None:
    result = _parse_response('{"label": "relevant", "score": 0.95}')
    assert result.label == "relevant"
    assert result.score == 0.95


def test_irrelevant_label_parses() -> None:
    result = _parse_response('{"label": "irrelevant", "score": 0.12}')
    assert result.label == "irrelevant"
    assert result.score == 0.12


def test_unclear_label_parses() -> None:
    result = _parse_response('{"label": "unclear", "score": 0.5}')
    assert result.label == "unclear"


def test_unknown_label_raises() -> None:
    with pytest.raises(LLMOutputError):
        _parse_response('{"label": "weird", "score": 0.5}')


def test_out_of_range_score_is_clamped() -> None:
    result = _parse_response('{"label": "relevant", "score": 1.5}')
    assert result.score == 1.0


def test_negative_score_is_clamped() -> None:
    result = _parse_response('{"label": "relevant", "score": -0.2}')
    assert result.score == 0.0


def test_integer_score_is_accepted() -> None:
    # Many small models emit `1` not `1.0` — both should pass.
    result = _parse_response('{"label": "relevant", "score": 1}')
    assert result.score == 1.0


def test_non_numeric_score_raises() -> None:
    with pytest.raises(LLMOutputError):
        _parse_response('{"label": "relevant", "score": "high"}')


def test_invalid_json_raises() -> None:
    with pytest.raises(LLMOutputError):
        _parse_response("not json")
