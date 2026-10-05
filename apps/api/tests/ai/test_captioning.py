"""Unit tests for the `caption_image` primitive.

The disaster captioning client (`UNDP_disasterVL_v3.DisasterVLRemoteVLLM`)
is replaced by a fake satisfying the `CaptioningPipeline` protocol, so no
real model is invoked. Covers the happy path (caption stripped), the blank
reply, the bytes->PIL hand-off, and the unconfigured-client error.
"""

from __future__ import annotations

import io

import pytest
from PIL import Image

from api.ai.captioning import caption_image
from api.ai.client import AIClients
from api.ai.errors import AIClientUnavailableError


class _FakePipeline:
    """Stands in for `DisasterVLRemoteVLLM` — records the image it got."""

    def __init__(self, reply: str) -> None:
        self._reply = reply
        self.calls: list[Image.Image] = []

    def caption(self, image: Image.Image, query: str = "") -> str:
        self.calls.append(image)
        return self._reply


def _png_bytes() -> bytes:
    """A real (tiny) PNG so `Image.open` can identify the format."""
    buf = io.BytesIO()
    Image.new("RGB", (2, 2), (120, 120, 120)).save(buf, format="PNG")
    return buf.getvalue()


def _clients(pipeline: _FakePipeline | None) -> AIClients:
    return AIClients(text=None, embedding=None, disastervl=pipeline)


async def test_caption_image_returns_stripped_caption() -> None:
    fake = _FakePipeline("  A collapsed three-storey concrete building.\n")
    result = await caption_image(_png_bytes(), clients=_clients(fake))
    assert result.caption == "A collapsed three-storey concrete building."
    # The primitive decoded the bytes to a PIL image before handing off.
    assert len(fake.calls) == 1
    assert isinstance(fake.calls[0], Image.Image)


async def test_caption_image_blank_reply_yields_empty_caption() -> None:
    fake = _FakePipeline("   ")
    result = await caption_image(_png_bytes(), clients=_clients(fake))
    # Worker treats an empty caption as a deterministic failure; the
    # primitive's job is just to surface it as "".
    assert result.caption == ""


async def test_caption_image_raises_when_unconfigured() -> None:
    with pytest.raises(AIClientUnavailableError):
        await caption_image(_png_bytes(), clients=_clients(None))
