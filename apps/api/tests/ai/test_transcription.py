"""Unit tests for the `transcribe_audio` primitive.

No model server: a fake `AsyncOpenAI`-shaped client returns a canned
transcription. Covers the happy path (text stripped), the unconfigured-client
error, the text-only return shape, and the optional `language_hint` forwarding.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any, cast

import pytest
from openai import AsyncOpenAI, omit

from api.ai import AIClientUnavailableError, transcribe_audio
from api.ai.client import AIClients, ConfiguredClient
from api.ai.types import TranscriptionResult


class _FakeTranscriptions:
    def __init__(self, text: str) -> None:
        self._text = text
        self.last_kwargs: dict[str, Any] | None = None

    async def create(self, **kwargs: Any) -> Any:
        self.last_kwargs = kwargs
        return SimpleNamespace(text=self._text)


def _clients(transcriptions: _FakeTranscriptions | None) -> AIClients:
    if transcriptions is None:
        return AIClients(text=None, embedding=None, transcription=None)
    fake_client = SimpleNamespace(audio=SimpleNamespace(transcriptions=transcriptions))
    cfg = ConfiguredClient(client=cast(AsyncOpenAI, fake_client), model="test-asr")
    return AIClients(text=None, embedding=None, transcription=cfg)


async def test_transcribe_happy_path_strips_text() -> None:
    fake = _FakeTranscriptions("  hello world  ")
    result = await transcribe_audio(b"wavbytes", content_type="audio/wav", clients=_clients(fake))
    assert result == TranscriptionResult(text="hello world")
    # response_format must be json (the model rejects verbose_json).
    assert fake.last_kwargs is not None
    assert fake.last_kwargs["response_format"] == "json"
    assert fake.last_kwargs["model"] == "test-asr"
    # No hint given -> the SDK's `omit` sentinel, not a real language.
    assert fake.last_kwargs["language"] is omit


async def test_transcribe_forwards_language_hint() -> None:
    fake = _FakeTranscriptions("مرحبا")
    await transcribe_audio(
        b"wavbytes", content_type="audio/wav", clients=_clients(fake), language_hint="ar"
    )
    assert fake.last_kwargs is not None
    assert fake.last_kwargs["language"] == "ar"


async def test_transcribe_unconfigured_raises() -> None:
    with pytest.raises(AIClientUnavailableError):
        await transcribe_audio(b"wavbytes", content_type="audio/wav", clients=_clients(None))


async def test_transcribe_handles_empty_text() -> None:
    fake = _FakeTranscriptions("")
    result = await transcribe_audio(b"wavbytes", content_type="audio/wav", clients=_clients(fake))
    assert result.text == ""
