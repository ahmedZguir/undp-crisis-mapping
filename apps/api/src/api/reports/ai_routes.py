"""Unauthenticated AI helpers the PWA calls while a citizen fills the report form.

Uploaded audio is transcribed and discarded, never stored.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile
from openai import APIError

from api.ai import (
    AIClientUnavailableError,
    DamageClassificationError,
    classify_damage,
    transcribe_audio,
)
from api.core.app_state import AppState, get_app_state
from api.core.rate_limit import limit
from api.reports.audio import (
    AudioDecodeError,
    AudioTooLargeError,
    AudioTooLongError,
    UnsupportedAudioTypeError,
    transcode_to_wav_async,
    validate_audio,
)
from api.reports.photo import (
    PhotoTooLargeError,
    UnsupportedPhotoTypeError,
    validate_photo,
)
from api.schemas.ai import DamageSuggestionResponse, TranscriptionResponse

router = APIRouter(prefix="/ai", tags=["ai"])


@router.post("/classify-damage", response_model=DamageSuggestionResponse)
@limit("5/minute;30/hour")
async def classify_damage_route(
    request: Request,
    state: Annotated[AppState, Depends(get_app_state)],
    photo: Annotated[UploadFile, File()],
) -> DamageSuggestionResponse:
    content = await photo.read()
    content_type = photo.content_type or "application/octet-stream"
    try:
        validate_photo(content_type, len(content))
    except UnsupportedPhotoTypeError as exc:
        raise HTTPException(status_code=415, detail=str(exc)) from exc
    except PhotoTooLargeError as exc:
        raise HTTPException(status_code=413, detail=str(exc)) from exc

    try:
        damage_class = await classify_damage(
            content, content_type=content_type, clients=state.ai_clients
        )
    except AIClientUnavailableError as exc:
        raise HTTPException(status_code=503, detail="damage_classifier_unavailable") from exc
    except APIError as exc:
        # A suggestion only: 503 lets the UI fall back to manual selection.
        raise HTTPException(status_code=503, detail="damage_classifier_unavailable") from exc
    except DamageClassificationError as exc:
        raise HTTPException(status_code=502, detail="damage_classifier_bad_output") from exc

    return DamageSuggestionResponse(damage_class=damage_class)


@router.post("/transcribe", response_model=TranscriptionResponse)
@limit("5/minute;30/hour")
async def transcribe_route(
    request: Request,
    state: Annotated[AppState, Depends(get_app_state)],
    audio: Annotated[UploadFile, File()],
) -> TranscriptionResponse:
    content = await audio.read()
    content_type = audio.content_type or "application/octet-stream"
    try:
        validate_audio(content_type, len(content))
    except UnsupportedAudioTypeError as exc:
        raise HTTPException(status_code=415, detail=str(exc)) from exc
    except AudioTooLargeError as exc:
        raise HTTPException(status_code=413, detail=str(exc)) from exc

    # The ASR server can't decode browser WebM/MP4. A bad clip is a client error.
    try:
        wav_bytes = await transcode_to_wav_async(content)
    except AudioTooLongError as exc:
        raise HTTPException(status_code=413, detail="audio_too_long") from exc
    except AudioDecodeError as exc:
        raise HTTPException(status_code=400, detail="audio_undecodable") from exc

    try:
        result = await transcribe_audio(
            wav_bytes, content_type="audio/wav", clients=state.ai_clients
        )
    except AIClientUnavailableError as exc:
        raise HTTPException(status_code=503, detail="transcription_unavailable") from exc
    except APIError as exc:
        # 503 lets the UI fall back to typing.
        raise HTTPException(status_code=503, detail="transcription_unavailable") from exc

    return TranscriptionResponse(text=result.text)
