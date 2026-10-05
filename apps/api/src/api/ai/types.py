from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

# ISO 639-1 codes. 'en' needs no translation; 'und' means the language could
# not be determined and the text passes through.
Lang = str

RelevanceLabel = Literal["relevant", "irrelevant", "unclear"]

# The geocoding pipeline treats this as a hint, never a hard gate.
ToponymType = Literal["street", "building", "landmark", "area"]


@dataclass(frozen=True, slots=True)
class ToponymMention:
    """One place mention pulled from a citizen's free-text location.

    `surface_form` is verbatim. `corrected_form` is set only when the model was
    confident the raw text was wrong (typo, mangled transliteration); the
    geocoder queries `corrected_form or surface_form`.
    """

    surface_form: str
    type_hint: ToponymType
    corrected_form: str | None = None


@dataclass(frozen=True, slots=True)
class TranslationResult:
    """`text_en` is empty when lang is 'en' or 'und'; callers fall back to the raw input."""

    lang: Lang
    text_en: str


@dataclass(frozen=True, slots=True)
class CaptionResult:
    caption: str


@dataclass(frozen=True, slots=True)
class TranscriptionResult:
    """Transcript in the original language.

    The ASR endpoint returns no language or confidence, so language is detected
    later by the translation enrichment.
    """

    text: str


@dataclass(frozen=True, slots=True)
class RelevanceResult:
    """`score` is the model's confidence in [0.0, 1.0]."""

    label: RelevanceLabel
    score: float
