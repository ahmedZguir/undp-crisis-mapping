"""Shapes for the citizen /ai helpers."""

from __future__ import annotations

from pydantic import BaseModel

from api.schemas.common import DamageClass


class DamageSuggestionResponse(BaseModel):
    damage_class: DamageClass


class TranscriptionResponse(BaseModel):
    text: str
