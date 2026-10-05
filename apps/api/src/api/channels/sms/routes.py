"""SMS Gate (sms-gate.app) inbound webhook.

Envelope:

    { "deviceId": ..., "event": "sms:received", "id": ..., "webhookId": ...,
      "payload": { "messageId": ..., "message": "...", "sender": "+974...",
                   "recipient": "+974...", "simNumber": 1, "receivedAt": "..." } }
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import logging
import time
from dataclasses import dataclass
from functools import lru_cache
from typing import Annotated, Any, cast

from fastapi import APIRouter, BackgroundTasks, Depends, Header, HTTPException, Request, Response

from api.channels.dedup import SeenIds
from api.channels.sms.adapter import SmsGatewayAdapter
from api.channels.sms.flow import SmsTemplateFlow
from api.channels.sms.sessions import get_sms_language_prefs, get_sms_session_store
from api.channels.submitter import ServiceReportSubmitter
from api.core.app_state import AppState, get_app_state
from api.core.config import Settings, get_settings
from api.crises.routes import get_crisis_service
from api.crises.service import CrisisService

_logger = logging.getLogger(__name__)
router = APIRouter(prefix="/sms")

_EVENT_SMS_RECEIVED = "sms:received"


@lru_cache(maxsize=1)
def _get_adapter() -> SmsGatewayAdapter:
    s = get_settings()
    return SmsGatewayAdapter(
        s.sms_gateway_base_url,
        s.sms_gateway_username,
        s.sms_gateway_password,
        sim_number=s.sms_gateway_sim,
    )


def _get_submitter(state: AppState) -> ServiceReportSubmitter:
    # No Twilio creds needed: SMS never carries a photo to download.
    return ServiceReportSubmitter(
        state.report_service,
        twilio_account_sid="",
        twilio_auth_token="",
        always_send_description=True,
    )


# Replay window; SMS Gate recommends 5 minutes either side.
_SIGNATURE_MAX_SKEW_SECONDS = 300


def _signature_ok(raw: bytes, signature: str | None, timestamp: str | None, secret: str) -> bool:
    """X-Signature is hex HMAC-SHA256 of raw_body + X-Timestamp (unix seconds)."""
    if not signature or not timestamp:
        return False
    try:
        ts = int(timestamp)
    except ValueError:
        return False
    if abs(time.time() - ts) > _SIGNATURE_MAX_SKEW_SECONDS:
        return False
    expected = hmac.new(
        secret.encode("utf-8"), raw + timestamp.encode("utf-8"), hashlib.sha256
    ).hexdigest()
    return hmac.compare_digest(expected, signature)


def _gateway_send_configured(s: Settings) -> bool:
    return bool(s.sms_gateway_username and s.sms_gateway_password)


def _get_flow(crises: CrisisService, state: AppState) -> SmsTemplateFlow:
    return SmsTemplateFlow(
        provider=_get_adapter(),
        sessions=get_sms_session_store(),
        crises=crises,
        submitter=_get_submitter(state),
        lang_prefs=get_sms_language_prefs(),
        privacy_policy_url=get_settings().privacy_policy_url,
    )


@lru_cache(maxsize=1)
def _get_seen_messages() -> SeenIds:
    """SMS Gate Cloud delivers each inbound SMS several times (observed 3x)."""
    return SeenIds()


class _SenderLocks:
    """Per-sender locks that make each turn's session read-modify-write atomic.

    Each inbound SMS is its own background task, so two quick messages from one
    number would otherwise both advance from the same step. Process-local;
    unlocked entries are pruned past ``cap``.
    """

    def __init__(self, cap: int = 1024) -> None:
        self._cap = cap
        self._locks: dict[str, asyncio.Lock] = {}

    def get(self, key: str) -> asyncio.Lock:
        lock = self._locks.get(key)
        if lock is None:
            if len(self._locks) >= self._cap:
                self._prune()
            lock = asyncio.Lock()
            self._locks[key] = lock
        return lock

    def _prune(self) -> None:
        for k in [k for k, lk in self._locks.items() if not lk.locked()]:
            del self._locks[k]


@lru_cache(maxsize=1)
def _get_sender_locks() -> _SenderLocks:
    return _SenderLocks()


@dataclass(frozen=True)
class InboundSms:
    from_e164: str
    body: str
    message_id: str | None
    received_at: str | None
    sim_number: int | None


def _dedup_key(msg: InboundSms) -> str | None:
    """None disables dedup.

    messageId is content-derived, so a resident sending "1" twice reuses it. Adding
    receivedAt collapses redeliveries while keeping repeated answers.
    """
    if msg.message_id is None:
        return None
    return f"{msg.message_id}:{msg.received_at or ''}"


def _parse_received(payload: dict[str, Any]) -> InboundSms | None:
    sender = payload.get("sender")
    message = payload.get("message")
    if not isinstance(sender, str) or not isinstance(message, str):
        return None

    sim_raw = payload.get("simNumber")
    sim_number = sim_raw if isinstance(sim_raw, int) else None
    message_id = payload.get("messageId")
    received_at = payload.get("receivedAt")

    return InboundSms(
        from_e164=sender,
        body=message,
        message_id=message_id if isinstance(message_id, str) else None,
        received_at=received_at if isinstance(received_at, str) else None,
        sim_number=sim_number,
    )


async def _dispatch_bot(flow: SmsTemplateFlow, from_e164: str, body: str) -> None:
    """Run a turn after the 200 is sent; a slow turn past SMS Gate's timeout triggers redelivery."""
    try:
        async with _get_sender_locks().get(from_e164):
            await flow.handle(from_e164, body)
    except Exception:
        _logger.exception("sms.webhook.flow_failed")


@router.post("/webhook")
async def sms_webhook(
    request: Request,
    background: BackgroundTasks,
    crises: Annotated[CrisisService, Depends(get_crisis_service)],
    state: Annotated[AppState, Depends(get_app_state)],
    x_signature: Annotated[str | None, Header(alias="X-Signature")] = None,
    x_timestamp: Annotated[str | None, Header(alias="X-Timestamp")] = None,
) -> Response:
    raw = await request.body()
    _logger.info(
        "sms.webhook.request len=%d has_sig=%s has_ts=%s",
        len(raw),
        x_signature is not None,
        x_timestamp is not None,
    )

    settings = get_settings()
    if not settings.sms_webhook_secret:
        # Fail closed: unverified inbound could forge reports.
        _logger.error("sms.webhook.rejected_no_secret (SMS_WEBHOOK_SECRET unset)")
        raise HTTPException(status_code=401, detail="webhook_secret_not_configured")
    if not _signature_ok(raw, x_signature, x_timestamp, settings.sms_webhook_secret):
        _logger.warning("sms.webhook.signature_invalid len=%d", len(raw))
        raise HTTPException(status_code=401, detail="invalid_signature")
    _logger.info("sms.webhook.signature_ok")
    try:
        parsed = json.loads(raw or b"{}")
    except json.JSONDecodeError:
        _logger.warning("sms.webhook.invalid_json len=%d", len(raw))
        return Response(status_code=200)
    if not isinstance(parsed, dict):
        return Response(status_code=200)
    envelope: dict[str, Any] = cast("dict[str, Any]", parsed)

    event = envelope.get("event")
    raw_payload = envelope.get("payload")
    payload: dict[str, Any] = (
        cast("dict[str, Any]", raw_payload) if isinstance(raw_payload, dict) else {}
    )

    if event != _EVENT_SMS_RECEIVED:
        _logger.info("sms.webhook.event_ignored event=%s", event)
        return Response(status_code=200)

    msg = _parse_received(payload)
    if msg is None:
        _logger.warning("sms.webhook.unparseable payload_keys=%s", sorted(payload.keys()))
        return Response(status_code=200)

    dedup_key = _dedup_key(msg)
    if dedup_key is not None and not _get_seen_messages().add_if_new(dedup_key):
        _logger.info("sms.webhook.dedup key=%s", dedup_key)
        return Response(status_code=200)

    _logger.info(
        "sms.webhook.received from=%s sim=%s id=%s body=%r",
        msg.from_e164,
        msg.sim_number,
        msg.message_id,
        msg.body,
    )

    if _gateway_send_configured(settings):
        _logger.info("sms.webhook.dispatch_bot from=%s", msg.from_e164)
        background.add_task(_dispatch_bot, _get_flow(crises, state), msg.from_e164, msg.body)
    else:
        _logger.warning("sms.webhook.bot_unconfigured (no gateway credentials)")

    return Response(status_code=200)
