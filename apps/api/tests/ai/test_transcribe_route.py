# pyright: reportMissingTypeStubs=false, reportUnknownMemberType=false, reportUnknownVariableType=false, reportUnknownArgumentType=false
"""Route tests for `POST /ai/transcribe`.

Exercised through the real FastAPI app with `get_app_state` overridden to a
fake `AIClients`, so no DB, Redis-backed state, or model server is touched.
The transcode step is REAL (PyAV), so the inputs are genuine browser-style
clips synthesised in-memory (WebM/Opus, MP4/AAC) — this is what proves the
endpoint decodes what `MediaRecorder` actually emits before forwarding the
faked-out transcription. Covers the happy path + body shape, the unconfigured
(503) and upstream-error (503) degradations, and upload validation
(415 / 413 / 400 / 413-too-long).
"""

from __future__ import annotations

import io
import math
from collections.abc import Iterator
from types import SimpleNamespace
from typing import Any, cast

import av
import httpx
import numpy as np
import pytest
from av.audio.stream import AudioStream
from fastapi.testclient import TestClient
from openai import APITimeoutError, AsyncOpenAI

from api.ai.client import AIClients, ConfiguredClient
from api.core.app_state import get_app_state
from api.main import app


def _synth(seconds: float, *, fmt: str, codec: str, tone: bool = True, rate: int = 48_000) -> bytes:
    """Encode a short clip in a real browser container/codec, in memory."""
    n = int(seconds * rate)
    if tone:
        t = np.arange(n, dtype=np.float32) / rate
        samples = (0.2 * np.sin(2 * math.pi * 440 * t) * 32767).astype(np.int16)
    else:
        samples = np.zeros(n, dtype=np.int16)
    frame = av.AudioFrame.from_ndarray(samples.reshape(1, -1), format="s16", layout="mono")
    frame.sample_rate = rate
    buf = io.BytesIO()
    with av.open(buf, mode="w", format=fmt) as container:
        stream = cast(AudioStream, container.add_stream(codec, rate=rate))
        for packet in stream.encode(frame):
            container.mux(packet)
        for packet in stream.encode(None):
            container.mux(packet)
    return buf.getvalue()


_WEBM = _synth(0.3, fmt="webm", codec="libopus")
_MP4 = _synth(0.3, fmt="mp4", codec="aac")
# Raw ADTS AAC, what the native shell's capture plugin emits as `audio/aac`.
_AAC = _synth(0.3, fmt="adts", codec="aac")


class _FakeTranscriptions:
    def __init__(self, text: str | None = None, *, raises: Exception | None = None) -> None:
        self._text = text
        self._raises = raises
        self.called = False

    async def create(self, **_: Any) -> Any:
        self.called = True
        if self._raises is not None:
            raise self._raises
        return SimpleNamespace(text=self._text)


def _clients(transcriptions: _FakeTranscriptions | None) -> AIClients:
    if transcriptions is None:
        return AIClients(text=None, embedding=None, transcription=None)
    fake_client = SimpleNamespace(audio=SimpleNamespace(transcriptions=transcriptions))
    cfg = ConfiguredClient(client=cast(AsyncOpenAI, fake_client), model="test-asr")
    return AIClients(text=None, embedding=None, transcription=cfg)


def _use_clients(clients: AIClients) -> None:
    app.dependency_overrides[get_app_state] = lambda: SimpleNamespace(ai_clients=clients)


@pytest.fixture(autouse=True)
def _clear_overrides() -> Iterator[None]:  # pyright: ignore[reportUnusedFunction]
    yield
    app.dependency_overrides.pop(get_app_state, None)


def test_transcribe_happy_path_webm() -> None:
    fake = _FakeTranscriptions("the wall on the north side collapsed")
    _use_clients(_clients(fake))
    client = TestClient(app)
    res = client.post("/ai/transcribe", files={"audio": ("note.webm", _WEBM, "audio/webm")})
    assert res.status_code == 200, res.text
    assert res.json() == {"text": "the wall on the north side collapsed"}
    # The real transcode ran and the (faked) ASR client was reached.
    assert fake.called


def test_transcribe_happy_path_mp4_with_codec_param() -> None:
    # Safari/iOS emit a parameterised content type; the base type is allowed.
    fake = _FakeTranscriptions("transcript")
    _use_clients(_clients(fake))
    client = TestClient(app)
    res = client.post(
        "/ai/transcribe", files={"audio": ("note.mp4", _MP4, "audio/mp4;codecs=mp4a.40.2")}
    )
    assert res.status_code == 200, res.text
    assert res.json() == {"text": "transcript"}


def test_transcribe_happy_path_aac_from_native() -> None:
    # The native shell (`capacitor-voice-recorder`) uploads `audio/aac`; the
    # endpoint allowlists it and transcodes the raw AAC before forwarding.
    fake = _FakeTranscriptions("the roof is gone")
    _use_clients(_clients(fake))
    client = TestClient(app)
    res = client.post("/ai/transcribe", files={"audio": ("note.aac", _AAC, "audio/aac")})
    assert res.status_code == 200, res.text
    assert res.json() == {"text": "the roof is gone"}
    assert fake.called


def test_transcribe_unconfigured_returns_503() -> None:
    _use_clients(_clients(None))
    client = TestClient(app)
    res = client.post("/ai/transcribe", files={"audio": ("note.webm", _WEBM, "audio/webm")})
    assert res.status_code == 503, res.text
    assert res.json()["detail"] == "transcription_unavailable"


def test_transcribe_upstream_timeout_returns_503() -> None:
    timeout = APITimeoutError(request=httpx.Request("POST", "http://asr"))
    _use_clients(_clients(_FakeTranscriptions(raises=timeout)))
    client = TestClient(app)
    res = client.post("/ai/transcribe", files={"audio": ("note.webm", _WEBM, "audio/webm")})
    assert res.status_code == 503, res.text
    assert res.json()["detail"] == "transcription_unavailable"


def test_transcribe_rejects_unsupported_type_415() -> None:
    _use_clients(_clients(_FakeTranscriptions("x")))
    client = TestClient(app)
    res = client.post("/ai/transcribe", files={"audio": ("note.txt", b"hello", "text/plain")})
    assert res.status_code == 415, res.text


def test_transcribe_rejects_oversize_413() -> None:
    _use_clients(_clients(_FakeTranscriptions("x")))
    client = TestClient(app)
    big = b"\x00" * (5 * 1024 * 1024 + 1)
    res = client.post("/ai/transcribe", files={"audio": ("note.wav", big, "audio/wav")})
    assert res.status_code == 413, res.text


def test_transcribe_rejects_undecodable_400() -> None:
    _use_clients(_clients(_FakeTranscriptions("x")))
    client = TestClient(app)
    res = client.post(
        "/ai/transcribe", files={"audio": ("note.webm", b"not real audio", "audio/webm")}
    )
    assert res.status_code == 400, res.text
    assert res.json()["detail"] == "audio_undecodable"


def test_transcribe_rejects_too_long_413() -> None:
    # 61s of (silent) audio is under the 5 MB byte cap but over the 60s duration
    # cap — must be rejected by the transcode step, not the size check.
    long_clip = _synth(61.0, fmt="webm", codec="libopus", tone=False)
    assert len(long_clip) <= 5 * 1024 * 1024
    _use_clients(_clients(_FakeTranscriptions("x")))
    client = TestClient(app)
    res = client.post("/ai/transcribe", files={"audio": ("note.webm", long_clip, "audio/webm")})
    assert res.status_code == 413, res.text
    assert res.json()["detail"] == "audio_too_long"
