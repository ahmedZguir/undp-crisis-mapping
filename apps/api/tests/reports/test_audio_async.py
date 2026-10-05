"""``transcode_to_wav_async`` keeps the CPU-bound transcode off the event loop."""

from __future__ import annotations

import threading

import pytest

from api.reports import audio


@pytest.mark.asyncio
async def test_transcode_runs_on_a_worker_thread(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict[str, object] = {}

    def fake_transcode(audio_bytes: bytes, *, max_duration_seconds: float) -> bytes:
        seen["thread"] = threading.current_thread()
        seen["max"] = max_duration_seconds
        return b"wav:" + audio_bytes

    monkeypatch.setattr(audio, "transcode_to_wav", fake_transcode)

    out = await audio.transcode_to_wav_async(b"clip", max_duration_seconds=12.0)

    assert out == b"wav:clip"
    assert seen["max"] == 12.0
    assert seen["thread"] is not threading.main_thread()
