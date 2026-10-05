"""Unit tests for `translate_to_all_locales`. The text-client is mocked
so no real LLM is invoked. Covers the happy path, transport failure,
invalid JSON, and partial response (some locales missing)."""

from __future__ import annotations

import asyncio
import json
from typing import Any

from api.ai.client import AIClients, ConfiguredClient
from api.ai.translation import translate_to_all_locales


class _StubMessage:
    def __init__(self, content: str) -> None:
        self.content = content


class _StubChoice:
    def __init__(self, content: str) -> None:
        self.message = _StubMessage(content)


class _StubResponse:
    def __init__(self, content: str) -> None:
        self.choices = [_StubChoice(content)]


class _StubChat:
    def __init__(self, response: Any) -> None:
        self._response = response
        self.calls: list[dict[str, Any]] = []

    @property
    def completions(self) -> _StubChat:
        return self

    async def create(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        if isinstance(self._response, Exception):
            raise self._response
        return self._response


class _StubClient:
    def __init__(self, response: Any) -> None:
        self.chat = _StubChat(response)


def _clients(response: Any) -> tuple[AIClients, _StubClient]:
    stub = _StubClient(response)
    text_cfg = ConfiguredClient(client=stub, model="stub-model")  # type: ignore[arg-type]
    return AIClients(text=text_cfg, embedding=None), stub


def test_translates_into_every_requested_locale() -> None:
    locales = ["en", "ar", "fr"]
    payload = {
        "en": "Most pressing needs",
        "ar": "أكثر الاحتياجات إلحاحًا",
        "fr": "Besoins les plus urgents",
    }
    clients, _ = _clients(_StubResponse(json.dumps(payload)))
    out = asyncio.run(translate_to_all_locales("Most pressing needs", locales, clients=clients))
    assert out == payload


def test_partial_response_falls_back_to_source_for_missing_locales() -> None:
    locales = ["en", "ar", "fr"]
    payload = {"en": "Hello", "fr": "Bonjour"}
    clients, _ = _clients(_StubResponse(json.dumps(payload)))
    out = asyncio.run(translate_to_all_locales("Hello", locales, clients=clients))
    assert out["en"] == "Hello"
    assert out["fr"] == "Bonjour"
    assert out["ar"] == "Hello"  # fallback to source


def test_invalid_json_returns_full_source_fallback() -> None:
    locales = ["en", "ar"]
    clients, _ = _clients(_StubResponse("not-json"))
    out = asyncio.run(translate_to_all_locales("Hi", locales, clients=clients))
    assert out == {"en": "Hi", "ar": "Hi"}


def test_transport_error_returns_full_source_fallback() -> None:
    locales = ["en", "ar"]
    clients, _ = _clients(RuntimeError("network down"))
    out = asyncio.run(translate_to_all_locales("Hi", locales, clients=clients))
    assert out == {"en": "Hi", "ar": "Hi"}


def test_unconfigured_client_returns_full_source_fallback() -> None:
    # AIClients(text=None) -> require_text() raises -> fallback.
    clients = AIClients(text=None, embedding=None)
    out = asyncio.run(translate_to_all_locales("Hi", ["en", "ar"], clients=clients))
    assert out == {"en": "Hi", "ar": "Hi"}
