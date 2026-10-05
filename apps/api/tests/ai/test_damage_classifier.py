"""Unit tests for the `classify_damage` primitive.

The OpenAI-compatible classifier client is replaced by a fake that records
the request and returns a canned completion, so no real model is invoked.
Covers the happy path, output normalisation (strip + lowercase), the
`guided_choice` + image-data-URL wiring, malformed output, and the
unconfigured-client error.
"""

from __future__ import annotations

import base64
from types import SimpleNamespace
from typing import Any, cast

import pytest
from openai import AsyncOpenAI

from api.ai.client import AIClients, ConfiguredClient
from api.ai.damage_classifier import DamageClassificationError, classify_damage
from api.ai.errors import AIClientUnavailableError

_IMAGE = b"\x89PNG\r\n\x1a\n-not-a-real-png-but-bytes-are-all-we-encode"


class _FakeCompletions:
    """Records the create() kwargs and returns a canned completion."""

    def __init__(self, reply: str | None, *, no_choices: bool = False) -> None:
        self._reply = reply
        self._no_choices = no_choices
        self.calls: list[dict[str, Any]] = []

    async def create(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        if self._no_choices:
            return SimpleNamespace(choices=[])
        message = SimpleNamespace(content=self._reply)
        return SimpleNamespace(choices=[SimpleNamespace(message=message)])


def _clients(completions: _FakeCompletions | None) -> AIClients:
    """Build an `AIClients` whose classifier wraps the fake (or None)."""
    if completions is None:
        return AIClients(text=None, embedding=None, classifier=None)
    fake_client = SimpleNamespace(chat=SimpleNamespace(completions=completions))
    classifier = ConfiguredClient(client=cast(AsyncOpenAI, fake_client), model="test-classifier")
    return AIClients(text=None, embedding=None, classifier=classifier)


async def test_classify_damage_returns_label() -> None:
    completions = _FakeCompletions("partial")
    result = await classify_damage(_IMAGE, content_type="image/png", clients=_clients(completions))
    assert result == "partial"


async def test_classify_damage_strips_and_lowercases() -> None:
    completions = _FakeCompletions("  Complete\n")
    result = await classify_damage(_IMAGE, content_type="image/png", clients=_clients(completions))
    assert result == "complete"


async def test_classify_damage_sends_image_data_url_and_guided_choice() -> None:
    completions = _FakeCompletions("minimal")
    await classify_damage(_IMAGE, content_type="image/webp", clients=_clients(completions))

    assert len(completions.calls) == 1
    kwargs = completions.calls[0]
    assert kwargs["model"] == "test-classifier"
    # Guided decoding constrains output to exactly the three labels, in order.
    assert kwargs["extra_body"] == {"guided_choice": ["minimal", "partial", "complete"]}
    # The image rides as a base64 data URL carrying the exact bytes + MIME.
    image_part = kwargs["messages"][1]["content"][1]
    expected_b64 = base64.b64encode(_IMAGE).decode("ascii")
    assert image_part["image_url"]["url"] == f"data:image/webp;base64,{expected_b64}"


async def test_classify_damage_rejects_unknown_label() -> None:
    completions = _FakeCompletions("demolished")
    with pytest.raises(DamageClassificationError):
        await classify_damage(_IMAGE, content_type="image/png", clients=_clients(completions))


async def test_classify_damage_rejects_empty_choices() -> None:
    completions = _FakeCompletions(None, no_choices=True)
    with pytest.raises(DamageClassificationError):
        await classify_damage(_IMAGE, content_type="image/png", clients=_clients(completions))


async def test_classify_damage_raises_when_unconfigured() -> None:
    with pytest.raises(AIClientUnavailableError):
        await classify_damage(_IMAGE, content_type="image/png", clients=_clients(None))
