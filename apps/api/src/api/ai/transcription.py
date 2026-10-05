"""Speech-to-text for citizen voice notes via vLLM's /v1/audio/transcriptions.

Interactive, so it uses a fail-fast client (short timeout, no retries).
"""

from __future__ import annotations

from typing import cast

from openai import omit
from openai.types.audio import Transcription

from api.ai.client import AIClients
from api.ai.types import TranscriptionResult

__all__ = ["transcribe_audio"]


async def transcribe_audio(
    audio_bytes: bytes,
    *,
    content_type: str,
    clients: AIClients,
    language_hint: str | None = None,
) -> TranscriptionResult:
    """`audio_bytes` must be a format the ASR server decodes (Ogg/Opus, WAV, FLAC).

    The server rejects WebM/MP4, so the route transcodes first. The effect of
    `language_hint` on output is unconfirmed.
    """
    cfg = clients.require_transcription()
    response = await cfg.client.audio.transcriptions.create(
        model=cfg.model,
        # The filename is cosmetic; content_type tells the server how to decode.
        file=("audio.wav", audio_bytes, content_type),
        response_format="json",  # the model rejects verbose_json
        # `omit` is the SDK's "param absent" sentinel.
        language=language_hint if language_hint else omit,
    )
    # `response_format="json"` resolves to a `Transcription`, but the SDK types
    # `create` as a union over every response format; narrow it back.
    text = cast(Transcription, response).text
    return TranscriptionResult(text=(text or "").strip())
