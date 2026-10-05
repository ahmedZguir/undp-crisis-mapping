"""Transport-agnostic model-call primitives.

Must not import FastAPI, Starlette, api.workers, api.routers, or DB-session helpers.
"""

from api.ai import halfvec
from api.ai.captioning import caption_image
from api.ai.client import (
    AIClients,
    AIClientUnavailableError,
    build_ai_clients,
)
from api.ai.damage_classifier import DamageClassificationError, classify_damage
from api.ai.embeddings import (
    EMBEDDING_VERSION,
    QWEN3_EMBEDDING_DIM,
    embed_passage,
    embed_query,
)
from api.ai.relevance import score_relevance
from api.ai.toponyms import extract_toponyms
from api.ai.transcription import transcribe_audio
from api.ai.translation import detect_and_translate
from api.ai.types import ToponymMention

__all__ = [
    "EMBEDDING_VERSION",
    "QWEN3_EMBEDDING_DIM",
    "AIClientUnavailableError",
    "AIClients",
    "DamageClassificationError",
    "ToponymMention",
    "build_ai_clients",
    "caption_image",
    "classify_damage",
    "detect_and_translate",
    "embed_passage",
    "embed_query",
    "extract_toponyms",
    "halfvec",
    "score_relevance",
    "transcribe_audio",
]
