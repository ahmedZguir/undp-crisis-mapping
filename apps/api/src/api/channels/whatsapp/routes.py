"""WhatsApp webhooks (Twilio, Meta) and the WhatsApp Flow data-exchange endpoint.

Unset credentials return 503 so a misconfigured deploy is loud rather than
silently dropping messages.
"""

from __future__ import annotations

import dataclasses
import json
import logging
from collections.abc import Iterator
from functools import lru_cache
from typing import TYPE_CHECKING, Annotated, Any, cast

from fastapi import APIRouter, BackgroundTasks, Depends, Header, HTTPException, Request, Response

from api.ai.client import AIClients
from api.channels.dedup import SeenIds
from api.channels.sessions import InMemorySessionStore, LanguagePrefs, SessionStore
from api.channels.submitter import ServiceReportSubmitter
from api.channels.whatsapp.adapter import MetaCloudAdapter, Provider, TwilioAdapter
from api.channels.whatsapp.flow import ConversationFlow, InboundMessage
from api.channels.whatsapp.flow_crypto import (
    FlowEndpointException,
    decrypt_request,
    encrypt_response,
    load_private_key,
)
from api.channels.whatsapp.flow_endpoint import get_next_screen
from api.channels.whatsapp.llm import AzureOpenAILLMClient, LLMClient
from api.channels.whatsapp.messages import strings_for
from api.channels.whatsapp.template_flow import TemplateConversationFlow
from api.core.app_state import AppState, get_app_state
from api.core.config import Settings, get_settings
from api.crises.routes import get_crisis_service
from api.crises.service import CrisisService

if TYPE_CHECKING:
    from cryptography.hazmat.primitives.asymmetric.rsa import RSAPrivateKey

# Both expose ``async def handle(InboundMessage)``.
MetaHandler = ConversationFlow | TemplateConversationFlow

_logger = logging.getLogger(__name__)
router = APIRouter(prefix="/whatsapp")


@lru_cache(maxsize=1)
def _get_twilio_adapter() -> TwilioAdapter:
    s = get_settings()
    return TwilioAdapter(
        account_sid=s.twilio_account_sid,
        auth_token=s.twilio_auth_token,
        from_=s.twilio_whatsapp_from,
    )


def _get_provider() -> Provider:
    return _get_twilio_adapter()


@lru_cache(maxsize=1)
def _get_meta_adapter() -> MetaCloudAdapter:
    s = get_settings()
    return MetaCloudAdapter(
        phone_number_id=s.meta_whatsapp_phone_number_id,
        access_token=s.meta_whatsapp_access_token,
        app_secret=s.meta_whatsapp_app_secret,
        verify_token=s.meta_whatsapp_verify_token,
        graph_version=s.meta_whatsapp_graph_version,
    )


@lru_cache(maxsize=1)
def _get_sessions() -> SessionStore:
    return InMemorySessionStore()


@lru_cache(maxsize=1)
def _get_lang_prefs() -> LanguagePrefs:
    return LanguagePrefs()


@lru_cache(maxsize=1)
def _get_llm() -> LLMClient:
    s = get_settings()
    return AzureOpenAILLMClient(
        api_key=s.azure_openai_api_key,
        endpoint=s.azure_openai_endpoint,
        api_version=s.azure_openai_api_version,
        deployment=s.whatsapp_llm_deployment,
    )


def _get_submitter(state: AppState) -> ServiceReportSubmitter:
    s = get_settings()
    return ServiceReportSubmitter(
        state.report_service,
        twilio_account_sid=s.twilio_account_sid,
        twilio_auth_token=s.twilio_auth_token,
    )


async def _flow_failed_text(phone_e164: str) -> str:
    """Localized generic failure; exception text can carry /reports bodies."""
    session = await _get_sessions().get(phone_e164)
    return strings_for((session.language if session else None) or "en").flow_failed


def _llm_configured(s: Settings) -> bool:
    return bool(s.azure_openai_api_key and s.azure_openai_endpoint and s.whatsapp_llm_deployment)


def _get_flow(
    settings: Annotated[Settings, Depends(get_settings)],
    crises: Annotated[CrisisService, Depends(get_crisis_service)],
    state: Annotated[AppState, Depends(get_app_state)],
) -> ConversationFlow:
    if not (
        settings.twilio_account_sid
        and settings.twilio_auth_token
        and settings.twilio_whatsapp_from
        and _llm_configured(settings)
    ):
        raise HTTPException(status_code=503, detail="whatsapp_not_configured")
    return ConversationFlow(
        provider=_get_provider(),
        sessions=_get_sessions(),
        crises=crises,
        llm=_get_llm(),
        submitter=_get_submitter(state),
    )


def _get_flow_meta(
    settings: Annotated[Settings, Depends(get_settings)],
    crises: Annotated[CrisisService, Depends(get_crisis_service)],
    state: Annotated[AppState, Depends(get_app_state)],
) -> MetaHandler:
    """``TemplateConversationFlow`` in ``template`` mode, else the LLM ``ConversationFlow``."""
    meta_ready = (
        settings.meta_whatsapp_phone_number_id
        and settings.meta_whatsapp_access_token
        and settings.meta_whatsapp_app_secret
        and settings.meta_whatsapp_verify_token
    )
    if not meta_ready:
        raise HTTPException(status_code=503, detail="whatsapp_meta_not_configured")

    mode = (settings.whatsapp_report_mode or "llm").lower()
    if mode == "template":
        return TemplateConversationFlow(
            provider=_get_meta_adapter(),
            sessions=_get_sessions(),
            crises=crises,
            submitter=_get_submitter(state),
            lang_prefs=_get_lang_prefs(),
            privacy_policy_url=settings.privacy_policy_url,
        )

    if not _llm_configured(settings):
        raise HTTPException(status_code=503, detail="whatsapp_meta_not_configured")
    return ConversationFlow(
        provider=_get_meta_adapter(),
        sessions=_get_sessions(),
        crises=crises,
        llm=_get_llm(),
        submitter=_get_submitter(state),
    )


def _canonical_url(request: Request, settings: Settings) -> str:
    """The URL Twilio signed; behind a proxy ``request.url`` has the internal host."""
    if settings.whatsapp_public_url:
        base = settings.whatsapp_public_url.rstrip("/")
        path = request.url.path
        query = request.url.query
        return f"{base}{path}{('?' + query) if query else ''}"
    return str(request.url)


def _parse_inbound(form: dict[str, str]) -> InboundMessage:
    def _f(key: str) -> str | None:
        v = form.get(key)
        return v if v else None

    def _ff(key: str) -> float | None:
        v = form.get(key)
        try:
            return float(v) if v else None
        except ValueError:
            return None

    try:
        num_media = int(form.get("NumMedia", "0") or "0")
    except ValueError:
        num_media = 0

    # Strip the "whatsapp:" prefix so sessions are keyed by bare E.164.
    raw_from = form.get("From", "")
    from_e164 = raw_from.split(":", 1)[1] if ":" in raw_from else raw_from

    return InboundMessage(
        from_e164=from_e164,
        body=form.get("Body", "") or "",
        num_media=num_media,
        media_url_0=_f("MediaUrl0"),
        media_type_0=_f("MediaContentType0"),
        latitude=_ff("Latitude"),
        longitude=_ff("Longitude"),
        # Only production numbers send these; the sandbox puts the tapped title in Body.
        button_payload=_f("ButtonPayload"),
        list_id=_f("ListId"),
    )


@router.post("/webhook")
async def whatsapp_webhook(
    request: Request,
    settings: Annotated[Settings, Depends(get_settings)],
    crises: Annotated[CrisisService, Depends(get_crisis_service)],
    state: Annotated[AppState, Depends(get_app_state)],
    x_twilio_signature: Annotated[str | None, Header(alias="X-Twilio-Signature")] = None,
) -> Response:
    raw = await request.body()
    # Flatten to str-only pairs; Twilio signs the sorted form params.
    form_pairs = await request.form()
    form: dict[str, str] = {k: v for k, v in form_pairs.multi_items() if isinstance(v, str)}

    if x_twilio_signature is None:
        raise HTTPException(status_code=401, detail="missing_signature")

    url = _canonical_url(request, settings)
    if not _get_twilio_adapter().validate_signature(url, form, x_twilio_signature):
        _logger.warning(
            "whatsapp.webhook.signature_invalid",
            extra={"url": url, "len": len(raw)},
        )
        raise HTTPException(status_code=401, detail="invalid_signature")

    msg = _parse_inbound(form)

    if settings.whatsapp_stub_reply:
        try:
            await _get_twilio_adapter().send_text(
                msg.from_e164,
                f"✅ Received: “{msg.body}”\n(stub reply — bot setup in progress)",
            )
        except Exception:
            _logger.exception("whatsapp.webhook.stub_send_failed")
        return Response(status_code=200)

    # Built lazily so echo mode needs no Azure config.
    flow = _get_flow(settings, crises, state)
    try:
        await flow.handle(msg)
    except Exception:
        _logger.exception("whatsapp.webhook.flow_failed")
        try:
            await _get_provider().send_text(msg.from_e164, await _flow_failed_text(msg.from_e164))
        except Exception:
            _logger.exception("whatsapp.webhook.error_send_failed")
    return Response(status_code=200)


# Meta WhatsApp Cloud API


def _iter_meta_first_messages(payload: dict[str, Any]) -> Iterator[dict[str, Any]]:
    """``messages[0]`` of each ``entry[].changes[].value``; ``statuses[]`` updates are skipped."""
    entries = cast(list[Any], payload.get("entry") or [])
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        entry_d: dict[str, Any] = cast(dict[str, Any], entry)
        changes = cast(list[Any], entry_d.get("changes") or [])
        for change in changes:
            if not isinstance(change, dict):
                continue
            change_d: dict[str, Any] = cast(dict[str, Any], change)
            value = change_d.get("value")
            if not isinstance(value, dict):
                continue
            value_d: dict[str, Any] = cast(dict[str, Any], value)
            messages = cast(list[Any], value_d.get("messages") or [])
            if not messages:
                continue
            raw_m = messages[0]
            if not isinstance(raw_m, dict):
                continue
            yield cast(dict[str, Any], raw_m)


def _parse_meta_interactive(
    inter_d: dict[str, Any],
) -> tuple[str | None, str | None, dict[str, Any] | None]:
    """Return ``(button_payload, list_id, flow_response)`` for an interactive message."""
    button_payload: str | None = None
    list_id: str | None = None
    flow_response: dict[str, Any] | None = None
    sub = inter_d.get("type")
    if sub == "button_reply":
        br = inter_d.get("button_reply")
        if isinstance(br, dict):
            br_d: dict[str, Any] = cast(dict[str, Any], br)
            # Keep only the id: a title left in `body` would be mistaken for typed text.
            button_payload = str(br_d.get("id", "") or "") or None
    elif sub == "list_reply":
        lr = inter_d.get("list_reply")
        if isinstance(lr, dict):
            lr_d: dict[str, Any] = cast(dict[str, Any], lr)
            list_id = str(lr_d.get("id", "") or "") or None
    elif sub == "nfm_reply":
        # WhatsApp Flow completion: `response_json` is a JSON string, decoded once here.
        nfm = inter_d.get("nfm_reply")
        if isinstance(nfm, dict):
            nfm_d: dict[str, Any] = cast(dict[str, Any], nfm)
            raw_resp = nfm_d.get("response_json")
            if isinstance(raw_resp, str):
                try:
                    parsed = json.loads(raw_resp)
                except json.JSONDecodeError:
                    _logger.warning("whatsapp.webhook.meta_nfm_reply_bad_json")
                    parsed = None
                if isinstance(parsed, dict):
                    flow_response = cast(dict[str, Any], parsed)
    return button_payload, list_id, flow_response


def _parse_inbound_meta(
    payload: dict[str, Any],
    media_bytes: bytes | None,
    media_mime: str | None,
) -> InboundMessage | None:
    """The first inbound message, or ``None`` if the envelope has none."""
    for m in _iter_meta_first_messages(payload):
        from_e164 = "+" + str(m.get("from", "")).lstrip("+")
        mtype = str(m.get("type", ""))
        body = ""
        num_media = 0
        media_type_0: str | None = None
        latitude: float | None = None
        longitude: float | None = None
        button_payload: str | None = None
        list_id: str | None = None
        flow_response: dict[str, Any] | None = None

        if mtype == "text":
            text = m.get("text")
            if isinstance(text, dict):
                text_d: dict[str, Any] = cast(dict[str, Any], text)
                body = str(text_d.get("body", "") or "")
        elif mtype in ("image", "video", "audio", "document"):
            media = m.get(mtype)
            if isinstance(media, dict):
                media_d: dict[str, Any] = cast(dict[str, Any], media)
                num_media = 1
                mime = media_d.get("mime_type")
                if isinstance(mime, str):
                    media_type_0 = mime
                caption = media_d.get("caption")
                if isinstance(caption, str):
                    body = caption
        elif mtype == "location":
            loc = m.get("location")
            if isinstance(loc, dict):
                loc_d: dict[str, Any] = cast(dict[str, Any], loc)
                lat_raw = loc_d.get("latitude")
                lng_raw = loc_d.get("longitude")
                try:
                    if lat_raw is not None and lng_raw is not None:
                        latitude = float(lat_raw)
                        longitude = float(lng_raw)
                except (TypeError, ValueError):
                    pass
        elif mtype == "interactive":
            interactive = m.get("interactive")
            if isinstance(interactive, dict):
                button_payload, list_id, flow_response = _parse_meta_interactive(
                    cast(dict[str, Any], interactive)
                )

        return InboundMessage(
            from_e164=from_e164,
            body=body,
            num_media=num_media,
            media_url_0=None,
            media_type_0=media_type_0 or media_mime,
            latitude=latitude,
            longitude=longitude,
            button_payload=button_payload,
            list_id=list_id,
            media_bytes=media_bytes,
            media_mime=media_mime,
            flow_response=cast("dict[str, object] | None", flow_response),
        )
    return None


def _extract_meta_message_id(payload: dict[str, Any]) -> str | None:
    """The ``wamid`` idempotency handle.

    Meta redelivers for up to ~7 days; after a restart wipes the in-memory
    sessions, a redelivery would otherwise look like a fresh first contact.
    """
    for m in _iter_meta_first_messages(payload):
        mid = m.get("id")
        if isinstance(mid, str) and mid:
            return mid
    return None


@lru_cache(maxsize=1)
def _get_seen_messages() -> SeenIds:
    return SeenIds()


def _extract_meta_media_id(payload: dict[str, Any]) -> str | None:
    for m in _iter_meta_first_messages(payload):
        for kind in ("image", "video", "audio", "document"):
            media = m.get(kind)
            if isinstance(media, dict):
                mid = cast(dict[str, Any], media).get("id")
                if isinstance(mid, str):
                    return mid
    return None


@router.get("/webhook/meta")
async def whatsapp_webhook_meta_verify(request: Request) -> Response:
    settings = get_settings()
    if not settings.meta_whatsapp_verify_token:
        raise HTTPException(status_code=503, detail="whatsapp_meta_not_configured")
    mode = request.query_params.get("hub.mode", "")
    token = request.query_params.get("hub.verify_token", "")
    challenge = request.query_params.get("hub.challenge", "")
    adapter = _get_meta_adapter()
    echo = adapter.verify_subscription(mode, token, challenge)
    if echo is None:
        _logger.warning("whatsapp.webhook.meta_verify_rejected mode=%s", mode)
        raise HTTPException(status_code=403, detail="verify_token_mismatch")
    return Response(content=echo, media_type="text/plain", status_code=200)


async def _transcribe_and_handle_voice_note(
    audio_bytes: bytes,
    msg: InboundMessage,
    flow: MetaHandler,
    adapter: MetaCloudAdapter,
    ai_clients: AIClients,
) -> None:
    """Handle the transcript as typed text; audio bytes are cleared so they aren't a photo."""
    # Imported lazily: api.reports.audio pulls in PyAV, which is heavy.
    from api.ai.errors import AIClientUnavailableError
    from api.ai.transcription import transcribe_audio
    from api.reports.audio import AudioValidationError, transcode_to_wav_async

    transcript: str | None = None
    try:
        wav_bytes = await transcode_to_wav_async(audio_bytes)
        result = await transcribe_audio(wav_bytes, content_type="audio/wav", clients=ai_clients)
        transcript = result.text or None
    except (AIClientUnavailableError, AudioValidationError) as exc:
        _logger.warning(
            "whatsapp.voice_note.transcription_unavailable from=%s: %s", msg.from_e164, exc
        )
    except Exception:
        _logger.exception("whatsapp.voice_note.transcription_error from=%s", msg.from_e164)

    if transcript:
        _logger.info(
            "whatsapp.voice_note.transcribed from=%s len=%d",
            msg.from_e164,
            len(transcript),
        )
        text_msg = dataclasses.replace(
            msg, body=transcript, num_media=0, media_bytes=None, media_mime=None
        )
    else:
        # The current step is unchanged, so the user can just type the answer.
        try:
            await adapter.send_text(
                msg.from_e164,
                "Sorry, I couldn't transcribe your voice note. Please type your answer.",
            )
        except Exception:
            _logger.exception("whatsapp.voice_note.fallback_send_failed from=%s", msg.from_e164)
        return

    try:
        await flow.handle(text_msg)
    except Exception:
        _logger.exception("whatsapp.voice_note.flow_failed from=%s", msg.from_e164)


@router.post("/webhook/meta")
async def whatsapp_webhook_meta(
    request: Request,
    background_tasks: BackgroundTasks,
    flow: Annotated[MetaHandler, Depends(_get_flow_meta)],
    state: Annotated[AppState, Depends(get_app_state)],
    x_hub_signature_256: Annotated[str | None, Header(alias="X-Hub-Signature-256")] = None,
) -> Response:
    raw = await request.body()
    adapter = _get_meta_adapter()
    if not adapter.validate_signature(raw, x_hub_signature_256):
        _logger.warning("whatsapp.webhook.meta_signature_invalid len=%d", len(raw))
        raise HTTPException(status_code=401, detail="invalid_signature")

    try:
        raw_payload = json.loads(raw or b"{}")
    except json.JSONDecodeError:
        _logger.warning("whatsapp.webhook.meta_invalid_json")
        return Response(status_code=200)
    if not isinstance(raw_payload, dict):
        return Response(status_code=200)
    payload: dict[str, Any] = cast(dict[str, Any], raw_payload)

    mid = _extract_meta_message_id(payload)
    if mid is not None and not _get_seen_messages().add_if_new(mid):
        _logger.info("whatsapp.webhook.meta_dedup mid=%s", mid)
        return Response(status_code=200)

    # The submitter has no Meta token, so media is downloaded here.
    media_bytes: bytes | None = None
    media_mime: str | None = None
    media_id = _extract_meta_media_id(payload)
    if media_id is not None:
        try:
            media_bytes, media_mime = await adapter.fetch_media_bytes(media_id)
        except Exception:
            _logger.exception("whatsapp.webhook.meta_media_download_failed")

    msg = _parse_inbound_meta(payload, media_bytes, media_mime)
    if msg is None:
        # Ack status updates so Meta does not retry.
        return Response(status_code=200)

    # Transcription can outlast Meta's webhook timeout, so run it in the background.
    if (
        msg.media_bytes is not None
        and msg.media_mime is not None
        and msg.media_mime.split(";", 1)[0].strip().lower().startswith("audio/")
    ):
        background_tasks.add_task(
            _transcribe_and_handle_voice_note,
            msg.media_bytes,
            msg,
            flow,
            adapter,
            state.ai_clients,
        )
        return Response(status_code=200)

    try:
        await flow.handle(msg)
    except Exception:
        _logger.exception("whatsapp.webhook.meta_flow_failed")
        try:
            await adapter.send_text(msg.from_e164, await _flow_failed_text(msg.from_e164))
        except Exception:
            _logger.exception("whatsapp.webhook.meta_error_send_failed")
    return Response(status_code=200)


@lru_cache(maxsize=1)
def _get_flow_private_key() -> RSAPrivateKey | None:
    s = get_settings()
    if not s.whatsapp_flow_private_key_pem:
        return None
    return load_private_key(
        s.whatsapp_flow_private_key_pem,
        s.whatsapp_flow_private_key_passphrase,
    )


@router.post("/flow/data-exchange")
async def whatsapp_flow_data_exchange(
    request: Request,
    x_hub_signature_256: Annotated[str | None, Header(alias="X-Hub-Signature-256")] = None,
) -> Response:
    """Meta WhatsApp Flow data_exchange endpoint.

    432 means a bad signature; 421 (decryption failed) makes Meta refresh its
    cached public key. https://github.com/WhatsApp/WhatsApp-Flows-Tools
    """
    settings = get_settings()
    if not settings.whatsapp_flow_private_key_pem:
        raise HTTPException(status_code=503, detail="whatsapp_flow_endpoint_not_configured")

    raw = await request.body()

    adapter = _get_meta_adapter()
    if settings.meta_whatsapp_app_secret and not adapter.validate_signature(
        raw, x_hub_signature_256
    ):
        _logger.warning("whatsapp.flow.endpoint_signature_invalid len=%d", len(raw))
        return Response(status_code=432)

    try:
        parsed = json.loads(raw or b"{}")
    except json.JSONDecodeError:
        return Response(status_code=400)
    if not isinstance(parsed, dict):
        return Response(status_code=400)
    envelope: dict[str, Any] = cast(dict[str, Any], parsed)

    private_key = _get_flow_private_key()
    if private_key is None:
        raise HTTPException(status_code=503, detail="whatsapp_flow_endpoint_not_configured")

    try:
        decrypted = decrypt_request(envelope, private_key)
    except FlowEndpointException as exc:
        _logger.warning("whatsapp.flow.decrypt_failed status=%d", exc.status_code)
        return Response(status_code=exc.status_code)

    _logger.info(
        "whatsapp.flow.data_exchange action=%s screen=%s",
        decrypted.body.get("action"),
        decrypted.body.get("screen"),
    )

    try:
        response_payload = get_next_screen(decrypted.body)
    except Exception:
        _logger.exception("whatsapp.flow.handler_failed")
        return Response(status_code=500)

    ciphertext = encrypt_response(response_payload, decrypted.aes_key, decrypted.iv)
    return Response(content=ciphertext, media_type="text/plain", status_code=200)
