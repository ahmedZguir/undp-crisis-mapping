# pyright: reportMissingTypeStubs=false, reportUnknownMemberType=false, reportUnknownVariableType=false, reportUnknownArgumentType=false
"""Voice-note validation and transcode to the WAV format the ASR server accepts.

Browsers record WebM/Opus or MP4/AAC, which the ASR server cannot decode, so uploads
are converted to 16 kHz mono 16-bit WAV with PyAV (bundled ffmpeg). No disk or network I/O.
"""

from __future__ import annotations

import asyncio
import io
from typing import cast

import av
import av.error

# Validated on the base type (codec parameters stripped). audio/aac comes from the
# native app's recorder. The transcode sniffs the real container anyway.
ALLOWED_AUDIO_MIME_TYPES: frozenset[str] = frozenset(
    {"audio/webm", "audio/ogg", "audio/mp4", "audio/mpeg", "audio/wav", "audio/x-wav", "audio/aac"}
)
# Pre-decode sanity limit; the duration cap is the real bound.
MAX_AUDIO_BYTES: int = 5 * 1024 * 1024  # 5 MB

# Bounds synchronous transcription latency; the client also stops recording at 60s.
MAX_AUDIO_DURATION_SECONDS: float = 60.0

_TARGET_SAMPLE_RATE = 16_000
_TARGET_LAYOUT = "mono"
_TARGET_SAMPLE_FORMAT = "s16"


class AudioValidationError(Exception):
    """Base class for audio validation failures."""


class AudioTooLargeError(AudioValidationError):
    pass


class UnsupportedAudioTypeError(AudioValidationError):
    pass


class AudioDecodeError(AudioValidationError):
    """The upload could not be decoded as audio (corrupt / not audio / empty)."""


class AudioTooLongError(AudioValidationError):
    """The clip exceeds the duration cap."""


def _base_mime(content_type: str) -> str:
    """Strip codec/charset parameters: `audio/webm;codecs=opus` -> `audio/webm`."""
    return content_type.split(";", 1)[0].strip().lower()


def validate_audio(content_type: str, size: int) -> None:
    """Reject unsupported MIME types and oversized uploads (pre-decode)."""
    if _base_mime(content_type) not in ALLOWED_AUDIO_MIME_TYPES:
        raise UnsupportedAudioTypeError(f"Unsupported audio content type: {content_type!r}")
    if size > MAX_AUDIO_BYTES:
        raise AudioTooLargeError(f"Audio size {size} exceeds {MAX_AUDIO_BYTES} bytes")


def transcode_to_wav(
    audio_bytes: bytes,
    *,
    max_duration_seconds: float = MAX_AUDIO_DURATION_SECONDS,
) -> bytes:
    """Decode browser audio to 16 kHz mono 16-bit WAV bytes.

    The duration cap counts decoded samples because browser WebM often has no
    container duration. Raises AudioDecodeError or AudioTooLongError.
    """
    resampler = av.AudioResampler(
        format=_TARGET_SAMPLE_FORMAT, layout=_TARGET_LAYOUT, rate=_TARGET_SAMPLE_RATE
    )
    max_samples = int(max_duration_seconds * _TARGET_SAMPLE_RATE)
    out_buf = io.BytesIO()
    total_samples = 0
    produced = False

    try:
        with (
            av.open(io.BytesIO(audio_bytes), mode="r") as in_container,
            av.open(out_buf, mode="w", format="wav") as out_container,
        ):
            in_stream = next(
                (s for s in in_container.streams if s.type == "audio"),
                None,
            )
            if in_stream is None:
                raise AudioDecodeError("upload has no audio stream")

            out_stream = out_container.add_stream(
                "pcm_s16le", rate=_TARGET_SAMPLE_RATE, layout=_TARGET_LAYOUT
            )

            def _mux(frame: av.AudioFrame | None) -> None:
                # Encode + mux one resampled frame (or flush when None).
                for packet in out_stream.encode(frame):
                    out_container.mux(packet)

            for frame in in_container.decode(in_stream):
                for rframe in resampler.resample(cast(av.AudioFrame, frame)):
                    total_samples += rframe.samples
                    if total_samples > max_samples:
                        raise AudioTooLongError(f"audio exceeds {max_duration_seconds:g}s cap")
                    # The WAV (PCM) muxer wants monotonic timestamps off our own
                    # clock, not the source container's.
                    rframe.pts = None
                    produced = True
                    _mux(rframe)
            # Flush the resampler, then the encoder.
            for rframe in resampler.resample(None):
                rframe.pts = None
                produced = True
                _mux(rframe)
            _mux(None)
    except AudioValidationError:
        raise
    except (av.error.FFmpegError, ValueError, MemoryError) as exc:
        # PyAV surfaces decode failures as FFmpegError subclasses; a malformed
        # container can also trip a ValueError before any frame is read.
        raise AudioDecodeError(f"could not decode audio: {exc}") from exc

    if not produced:
        raise AudioDecodeError("audio decoded to no samples")
    return out_buf.getvalue()


async def transcode_to_wav_async(
    audio_bytes: bytes,
    *,
    max_duration_seconds: float = MAX_AUDIO_DURATION_SECONDS,
) -> bytes:
    """``transcode_to_wav`` on a worker thread; async callers must use this, it is CPU-bound."""
    return await asyncio.to_thread(
        transcode_to_wav, audio_bytes, max_duration_seconds=max_duration_seconds
    )
