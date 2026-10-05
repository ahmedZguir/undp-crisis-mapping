"""Disaster-photo captioning via the DisasterVL library client.

The library owns the prompt, LoRA routing and image preprocessing. Its client
is synchronous, so the call runs in a thread.
"""

from __future__ import annotations

import asyncio
import io

from PIL import Image

from api.ai.client import AIClients
from api.ai.types import CaptionResult


async def caption_image(
    image_bytes: bytes,
    *,
    clients: AIClients,
) -> CaptionResult:
    """Pillow detects the image format from the bytes."""
    pipeline = clients.require_disastervl()
    image = Image.open(io.BytesIO(image_bytes))
    caption = await asyncio.to_thread(pipeline.caption, image)
    return CaptionResult(caption=(caption or "").strip())
