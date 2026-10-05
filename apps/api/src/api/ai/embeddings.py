"""Embedding client over an OpenAI-compatible /v1/embeddings endpoint.

`embed_passage` and `embed_query` are identical today (no instruct template)
but kept separate so an asymmetric query template is a one-place change.
"""

from __future__ import annotations

from dataclasses import dataclass

from openai import AsyncOpenAI

from api.ai.errors import AIClientUnavailableError
from api.core.config import Settings

# Backstop against a server restart mid-request; warm calls take well under a second.
_REQUEST_TIMEOUT_SECONDS = 30.0
_MAX_RETRIES = 2

# Checked at insert time so a model with a different dimension fails loudly
# instead of writing into the halfvec(2560) column.
QWEN3_EMBEDDING_DIM = 2560

# Stored on each report_embeddings row; bump when re-embedding with a new
# model so ANN queries can filter to the current generation.
EMBEDDING_VERSION = 1


@dataclass(frozen=True, slots=True)
class EmbeddingClient:
    client: AsyncOpenAI
    model: str


@dataclass(frozen=True, slots=True)
class EmbeddingResult:
    vector: list[float]
    model: str
    dimension: int


def build_embedding_client(settings: Settings) -> EmbeddingClient | None:
    """Return None when the URL or model is unset."""
    base_url = settings.ai_embedding_base_url
    model = settings.ai_embedding_model
    if not base_url or not model:
        return None
    api_key = settings.ai_embedding_api_key or "unused"
    client = AsyncOpenAI(
        base_url=base_url,
        api_key=api_key,
        timeout=_REQUEST_TIMEOUT_SECONDS,
        max_retries=_MAX_RETRIES,
    )
    return EmbeddingClient(client=client, model=model)


async def aclose_embedding_client(client: EmbeddingClient | None) -> None:
    if client is not None:
        await client.client.close()


async def embed_passage(text: str, *, client: EmbeddingClient | None) -> EmbeddingResult:
    return await _embed(text, client=client)


async def embed_query(text: str, *, client: EmbeddingClient | None) -> EmbeddingResult:
    return await _embed(text, client=client)


async def _embed(text: str, *, client: EmbeddingClient | None) -> EmbeddingResult:
    if client is None:
        raise AIClientUnavailableError(
            "embedding client unavailable: AI_EMBEDDING_BASE_URL / AI_EMBEDDING_MODEL are unset"
        )
    response = await client.client.embeddings.create(model=client.model, input=text)
    if not response.data:
        raise RuntimeError("embedding endpoint returned no data")
    vector = list(response.data[0].embedding)
    return EmbeddingResult(vector=vector, model=client.model, dimension=len(vector))
