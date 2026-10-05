"""Fast single-photo damage classifier used to pre-fill a recommended class.

vLLM `guided_choice` constrains the output to one `DamageClass` label.
"""

from __future__ import annotations

import base64
from typing import cast, get_args

from api.ai import prompts
from api.ai.client import AIClients
from api.schemas.common import DamageClass

__all__ = ["DamageClassificationError", "classify_damage"]

_CHOICES: tuple[str, ...] = get_args(DamageClass)

# Bounds the reply if a backend ignores guided_choice.
_MAX_OUTPUT_TOKENS = 8


class DamageClassificationError(ValueError):
    """The model returned something outside the allowed `DamageClass` set."""


async def classify_damage(
    image_bytes: bytes,
    *,
    content_type: str,
    clients: AIClients,
) -> DamageClass:
    cfg = clients.require_classifier()
    data_url = f"data:{content_type};base64,{base64.b64encode(image_bytes).decode('ascii')}"
    response = await cfg.client.chat.completions.create(
        model=cfg.model,
        max_tokens=_MAX_OUTPUT_TOKENS,
        temperature=0.0,
        messages=[
            {"role": "system", "content": prompts.DAMAGE_CLASSIFIER_SYSTEM},
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "Classify the building damage in this photo."},
                    {"type": "image_url", "image_url": {"url": data_url}},
                ],
            },
        ],
        # vLLM-specific: constrain the completion to exactly one label.
        extra_body={"guided_choice": list(_CHOICES)},
    )
    choices = response.choices
    if not choices:
        raise DamageClassificationError("model returned no choices")
    label = (choices[0].message.content or "").strip().lower()
    if label not in _CHOICES:
        raise DamageClassificationError(f"model output is not a damage class: {label!r}")
    return cast("DamageClass", label)
