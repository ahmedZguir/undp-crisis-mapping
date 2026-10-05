"""Unit tests for chat citation validation + heuristics.

The chat surface validates `[report_id=…]` citations against the locked
K set on the server — invalid ids are stripped, and a response that
ends up empty after stripping is rewritten to the abstention string.
These tests cover that contract directly.
"""

from __future__ import annotations

import uuid

from api.ai.chat import (
    _validate_citations,  # pyright: ignore[reportPrivateUsage]
    is_generic_first_message,
)


def test_valid_citation_passes_through() -> None:
    rid = uuid.uuid4()
    text = f"The school is cracked [report_id={rid}]."
    cleaned, cited = _validate_citations(text, {rid})
    assert cleaned == text
    assert cited == [rid]


def test_invalid_citation_is_stripped() -> None:
    valid = uuid.uuid4()
    invalid = uuid.uuid4()
    text = f"Maybe a fire [report_id={invalid}]. Confirmed [report_id={valid}]."
    cleaned, cited = _validate_citations(text, {valid})
    assert f"[report_id={invalid}]" not in cleaned
    assert f"[report_id={valid}]" in cleaned
    assert cited == [valid]


def test_all_invalid_citations_collapse_to_abstention() -> None:
    invalid = uuid.uuid4()
    text = f"[report_id={invalid}]"
    cleaned, cited = _validate_citations(text, set())
    assert cleaned == "Insufficient information in the current view."
    assert cited == []


def test_generic_first_message_detection() -> None:
    assert is_generic_first_message("hi")
    assert is_generic_first_message("tell me about this")
    assert is_generic_first_message("what's going on?")
    assert not is_generic_first_message(
        "Which neighbourhoods reported structural damage in the last six hours?"
    )
