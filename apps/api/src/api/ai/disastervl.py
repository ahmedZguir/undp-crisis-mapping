# Typed boundary to the untyped UNDP_disasterVL_v3 library: unknown-type
# reports are relaxed here only and the rest of the package sees a Protocol.
# pyright: reportMissingTypeStubs=false, reportUnknownMemberType=false, reportUnknownVariableType=false, reportUnknownArgumentType=false
"""Adapter to the DisasterVL LoRA served by infra/ai_scripts/serve_disastervl.sh.

Only `DisasterVLRemoteVLLM.caption()` is used.
"""

from __future__ import annotations

from typing import Protocol

from PIL import Image

from api.core.config import Settings

__all__ = ["CaptioningPipeline", "build_disastervl_pipeline"]

# Fallback when AI_VISION_MODEL is unset; matches the serve script's default.
_DEFAULT_BASE_MODEL = "Qwen/Qwen3.5-9B"
# Matches the library's own default.
_CAPTION_MAX_TOKENS = 256
# The caption is a multi-aspect paragraph, so allow more than the OpenAI
# clients' 60s. Arq's job_timeout is the outer bound.
_CAPTION_TIMEOUT_SECONDS = 120


class CaptioningPipeline(Protocol):
    """Structurally satisfied by `UNDP_disasterVL_v3.DisasterVLRemoteVLLM`."""

    def caption(self, image: Image.Image, query: str = "") -> str: ...


def build_disastervl_pipeline(settings: Settings) -> CaptioningPipeline | None:
    """Return None if unconfigured. The library import is local so it stays optional."""
    if not settings.ai_vision_base_url or not settings.ai_vision_disaster_model:
        return None

    from UNDP_disasterVL_v3 import DisasterVLRemoteVLLM, DisasterVLRemoteVLLMConfig

    config = DisasterVLRemoteVLLMConfig(
        api_base=settings.ai_vision_base_url,
        api_key=settings.ai_vision_api_key or "EMPTY",
        base_model=settings.ai_vision_model or _DEFAULT_BASE_MODEL,
        disaster_model=settings.ai_vision_disaster_model,
        default_max_tokens=_CAPTION_MAX_TOKENS,
        timeout=_CAPTION_TIMEOUT_SECONDS,
    )
    pipeline: CaptioningPipeline = DisasterVLRemoteVLLM(config)
    return pipeline
