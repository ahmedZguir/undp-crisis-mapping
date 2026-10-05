"""OpenAI-compatible client bundle, built once per process.

Unconfigured roles are None on the bundle; primitives raise
`AIClientUnavailableError` when they need one.
"""

from __future__ import annotations

from dataclasses import dataclass

from openai import AsyncOpenAI

from api.ai.disastervl import CaptioningPipeline, build_disastervl_pipeline
from api.ai.embeddings import EmbeddingClient, aclose_embedding_client, build_embedding_client
from api.ai.errors import AIClientUnavailableError
from api.core.config import Settings

# AIClientUnavailableError is re-exported for existing import paths.
__all__ = [
    "AIClientUnavailableError",
    "AIClients",
    "ConfiguredClient",
    "aclose_ai_clients",
    "build_ai_clients",
]

# Per-call backstop for background enrichment; Arq's job_timeout is the outer bound.
_REQUEST_TIMEOUT_SECONDS = 60.0
# Transient transport errors only; Arq max_tries handles the rest.
_MAX_RETRIES = 2

# Interactive: a citizen is waiting on the form, so fail fast and let the UI
# fall back to manual selection.
_CLASSIFIER_TIMEOUT_SECONDS = 10.0
_CLASSIFIER_MAX_RETRIES = 0

# Interactive too; on failure the UI falls back to typing.
_TRANSCRIPTION_TIMEOUT_SECONDS = 45.0
_TRANSCRIPTION_MAX_RETRIES = 0


@dataclass(frozen=True, slots=True)
class ConfiguredClient:
    client: AsyncOpenAI
    model: str


@dataclass(frozen=True, slots=True)
class AIClients:
    """Clients per model role; several may point at the same endpoint.

    `text` serves translation and relevance, `disastervl` serves captioning.
    """

    text: ConfiguredClient | None
    embedding: EmbeddingClient | None
    disastervl: CaptioningPipeline | None = None
    # Small fast model behind POST /ai/classify-damage.
    classifier: ConfiguredClient | None = None
    # ASR model behind POST /ai/transcribe, called via /v1/audio/transcriptions.
    transcription: ConfiguredClient | None = None

    def require_text(self) -> ConfiguredClient:
        if self.text is None:
            raise AIClientUnavailableError(
                "text client unavailable: AI_LLM_BASE_URL / AI_LLM_MODEL are unset"
            )
        return self.text

    def require_disastervl(self) -> CaptioningPipeline:
        if self.disastervl is None:
            raise AIClientUnavailableError(
                "captioning client unavailable: "
                "AI_VISION_BASE_URL / AI_VISION_DISASTER_MODEL are unset"
            )
        return self.disastervl

    def require_classifier(self) -> ConfiguredClient:
        if self.classifier is None:
            raise AIClientUnavailableError(
                "classifier client unavailable: "
                "AI_CLASSIFIER_BASE_URL / AI_CLASSIFIER_MODEL are unset"
            )
        return self.classifier

    def require_transcription(self) -> ConfiguredClient:
        if self.transcription is None:
            raise AIClientUnavailableError(
                "transcription client unavailable: "
                "AI_TRANSCRIPTION_BASE_URL / AI_TRANSCRIPTION_MODEL are unset"
            )
        return self.transcription


def _build_one(
    base_url: str,
    api_key: str,
    model: str,
    *,
    timeout: float = _REQUEST_TIMEOUT_SECONDS,
    max_retries: int = _MAX_RETRIES,
) -> ConfiguredClient | None:
    """Return None unless base_url and model are set.

    The SDK requires a non-empty api_key, so self-hosted vLLM gets a dummy one.
    """
    if not base_url or not model:
        return None
    client = AsyncOpenAI(
        base_url=base_url,
        api_key=api_key or "unused",
        timeout=timeout,
        max_retries=max_retries,
    )
    return ConfiguredClient(client=client, model=model)


def build_ai_clients(settings: Settings) -> AIClients:
    return AIClients(
        text=_build_one(settings.ai_llm_base_url, settings.ai_llm_api_key, settings.ai_llm_model),
        embedding=build_embedding_client(settings),
        disastervl=build_disastervl_pipeline(settings),
        classifier=_build_one(
            settings.ai_classifier_base_url,
            settings.ai_classifier_api_key,
            settings.ai_classifier_model,
            timeout=_CLASSIFIER_TIMEOUT_SECONDS,
            max_retries=_CLASSIFIER_MAX_RETRIES,
        ),
        transcription=_build_one(
            settings.ai_transcription_base_url,
            settings.ai_transcription_api_key,
            settings.ai_transcription_model,
            timeout=_TRANSCRIPTION_TIMEOUT_SECONDS,
            max_retries=_TRANSCRIPTION_MAX_RETRIES,
        ),
    )


async def aclose_ai_clients(clients: AIClients) -> None:
    """The disastervl client holds no persistent session, so it is not closed."""
    if clients.text is not None:
        await clients.text.client.close()
    if clients.classifier is not None:
        await clients.classifier.client.close()
    if clients.transcription is not None:
        await clients.transcription.client.close()
    await aclose_embedding_client(clients.embedding)
