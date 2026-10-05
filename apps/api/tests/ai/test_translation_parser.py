"""Parser-only tests for `api.ai.translation._parse_response`.

The parser is the trust boundary between the model's JSON output and the
TranslationResult contract the worker depends on. These tests exercise
every branch without going near a real model.
"""

from __future__ import annotations

import pytest

from api.ai.llm import LLMOutputError
from api.ai.translation import (
    _parse_response,  # pyright: ignore[reportPrivateUsage]
)


def test_english_noop_parses_to_empty_text_en() -> None:
    result = _parse_response('{"lang": "en", "text_en": ""}')
    assert result.lang == "en"
    assert result.text_en == ""


def test_und_passthrough_parses() -> None:
    result = _parse_response('{"lang": "und", "text_en": ""}')
    assert result.lang == "und"
    assert result.text_en == ""


def test_arabic_translation_parses() -> None:
    result = _parse_response('{"lang": "ar", "text_en": "The wall is cracked."}')
    assert result.lang == "ar"
    assert result.text_en == "The wall is cracked."


def test_invalid_json_raises_parse_error() -> None:
    with pytest.raises(LLMOutputError):
        _parse_response("not json at all")


def test_non_object_top_level_raises() -> None:
    with pytest.raises(LLMOutputError):
        _parse_response("[1, 2, 3]")


def test_missing_lang_raises() -> None:
    with pytest.raises(LLMOutputError):
        _parse_response('{"text_en": "hi"}')


def test_empty_lang_raises() -> None:
    with pytest.raises(LLMOutputError):
        _parse_response('{"lang": "", "text_en": ""}')


def test_non_string_text_en_raises() -> None:
    with pytest.raises(LLMOutputError):
        _parse_response('{"lang": "ar", "text_en": 42}')
