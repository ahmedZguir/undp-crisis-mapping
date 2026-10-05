"""Parser-only tests for `api.ai.toponyms._parse_response`.

These pin the array contract and the tolerant-reader behaviour without
touching the LLM — the prompt's job is covered by eval, not unit tests.
"""

from __future__ import annotations

import pytest

from api.ai.llm import LLMOutputError
from api.ai.toponyms import (
    _parse_response,  # pyright: ignore[reportPrivateUsage]
)


def test_contract_object_parses_in_order() -> None:
    raw = (
        '{"toponyms": ['
        '{"surface_form": "Yeni Cami", "type_hint": "landmark", "corrected_form": null}, '
        '{"surface_form": "Antakya", "type_hint": "area", "corrected_form": null}]}'
    )
    out = _parse_response(raw)
    assert [m.surface_form for m in out] == ["Yeni Cami", "Antakya"]
    assert [m.type_hint for m in out] == ["landmark", "area"]
    assert all(m.corrected_form is None for m in out)


def test_empty_object_array_is_valid() -> None:
    assert _parse_response('{"toponyms": []}') == []


def test_bare_array_fallback_still_parses() -> None:
    """A model that ignores the object wrapper and returns a bare array."""
    raw = '[{"surface_form": "Defne", "type_hint": "area", "corrected_form": null}]'
    assert [m.surface_form for m in _parse_response(raw)] == ["Defne"]


def test_reasoning_preamble_before_object_is_stripped() -> None:
    """The configured text model is a reasoning model that prepends prose;
    JSON mode plus the object-span extractor recovers the payload."""
    raw = (
        "Thinking Process:\n1. Find the places.\n\n"
        '{"toponyms": [{"surface_form": "Antakya", "type_hint": "area", "corrected_form": null}]}'
    )
    assert [m.surface_form for m in _parse_response(raw)] == ["Antakya"]


def test_object_without_toponyms_key_raises() -> None:
    with pytest.raises(LLMOutputError):
        _parse_response('{"places": []}')


def test_corrected_form_is_carried() -> None:
    raw = (
        '[{"surface_form": "Ataturk Cd.", "type_hint": "street", '
        '"corrected_form": "Atatürk Caddesi"}]'
    )
    out = _parse_response(raw)
    assert len(out) == 1
    assert out[0].corrected_form == "Atatürk Caddesi"


def test_markdown_fenced_array_is_recovered() -> None:
    raw = '```json\n[{"surface_form": "Defne", "type_hint": "area", "corrected_form": null}]\n```'
    out = _parse_response(raw)
    assert [m.surface_form for m in out] == ["Defne"]


def test_unknown_type_hint_falls_back_to_area() -> None:
    raw = '[{"surface_form": "Somewhere", "type_hint": "planet", "corrected_form": null}]'
    out = _parse_response(raw)
    assert out[0].type_hint == "area"


def test_missing_type_hint_falls_back_to_area() -> None:
    out = _parse_response('[{"surface_form": "Somewhere"}]')
    assert out[0].type_hint == "area"


def test_blank_corrected_form_becomes_none() -> None:
    raw = '[{"surface_form": "X", "type_hint": "area", "corrected_form": "   "}]'
    assert _parse_response(raw)[0].corrected_form is None


def test_individually_malformed_elements_are_dropped() -> None:
    raw = (
        '[{"surface_form": "Keep", "type_hint": "area", "corrected_form": null}, '
        '{"type_hint": "area"}, '  # no surface_form → dropped
        '"a bare string", '  # not an object → dropped
        '{"surface_form": "", "type_hint": "area"}]'  # empty surface → dropped
    )
    out = _parse_response(raw)
    assert [m.surface_form for m in out] == ["Keep"]


def test_single_bare_mention_object_parses() -> None:
    """Observed live: the reasoning model returns a single bare mention object
    (no `toponyms` wrapper) for a one-place input."""
    raw = '{"surface_form": "Cumhuriyet Caddesi", "type_hint": "street", "corrected_form": null}'
    out = _parse_response(raw)
    assert [m.surface_form for m in out] == ["Cumhuriyet Caddesi"]
    assert out[0].type_hint == "street"


def test_single_mention_under_toponyms_key_parses() -> None:
    raw = '{"toponyms": {"surface_form": "Defne", "type_hint": "area", "corrected_form": null}}'
    assert [m.surface_form for m in _parse_response(raw)] == ["Defne"]


def test_empty_object_means_no_places() -> None:
    assert _parse_response("{}") == []


def test_unparseable_text_raises() -> None:
    with pytest.raises(LLMOutputError):
        _parse_response("I could not find any places, sorry!")
