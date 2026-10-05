"""In-memory session store shared by the WhatsApp, SMS and IVR channels.

Process-local until a ``wa_sessions`` table lands: sessions don't survive a restart
and a multi-worker deploy would shard them, so run with ``--workers 1``.
"""

from __future__ import annotations

import uuid
from collections import OrderedDict
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any, Literal, Protocol

State = Literal["active", "done"]

SESSION_TTL = timedelta(hours=1)
MAX_HISTORY_MSGS = 20


def _now() -> datetime:
    return datetime.now(UTC)


def _empty_scratch() -> dict[str, Any]:
    return {}


def _empty_crisis_choices() -> dict[str, str]:
    return {}


def _empty_generic_answers() -> dict[str, str | list[str]]:
    return {}


@dataclass
class HistoryMsg:
    role: Literal["user", "assistant"]
    content: str


def _empty_history() -> list[HistoryMsg]:
    return []


@dataclass
class Session:
    phone_e164: str
    state: State = "active"
    crisis_id: uuid.UUID | None = None
    crisis_name: str | None = None
    language: str | None = None  # falls back to crisis default at use site
    location: tuple[float, float] | None = None  # (lat, lng)
    # The other half of the location-OR-route gate.
    route_description: str | None = None
    photo_media_url: str | None = None  # used by the Twilio path
    # Meta media needs a Bearer token the submitter doesn't have, so the route
    # downloads it up front; when set, the submitter skips the URL download.
    photo_bytes: bytes | None = None
    photo_mime: str | None = None
    damage_class: Literal["minimal", "partial", "complete"] | None = None
    infra_description: str | None = None
    debris: Literal["yes", "no", "unknown"] | None = None
    infra_type: list[str] | None = None
    infra_type_other: str | None = None
    crisis_nature: str | None = None
    crisis_nature_other: str | None = None
    # Pinned when the crisis is selected so an admin publish mid-conversation
    # doesn't change it; raw schema with locale maps intact.
    form_schema: dict[str, Any] | None = None
    form_version: int | None = None
    # Keyed by question label; posted to /reports verbatim.
    generic_answers: dict[str, str | list[str]] = field(default_factory=_empty_generic_answers)
    # Idempotency key: per session, never derived from the phone.
    client_submission_id: uuid.UUID = field(default_factory=uuid.uuid4)
    created_at: datetime = field(default_factory=_now)
    updated_at: datetime = field(default_factory=_now)
    expires_at: datetime = field(default_factory=lambda: _now() + SESSION_TTL)
    history: list[HistoryMsg] = field(default_factory=_empty_history)
    # name -> UUID snapshot at session start, so the LLM can never mint a crisis id it didn't see.
    active_crisis_choices: dict[str, str] = field(default_factory=_empty_crisis_choices)
    scratch: dict[str, Any] = field(default_factory=_empty_scratch)

    @property
    def has_photo(self) -> bool:
        return self.photo_media_url is not None or self.photo_bytes is not None

    @property
    def has_description(self) -> bool:
        return bool(self.infra_description and self.infra_description.strip())

    def touch(self) -> None:
        self.updated_at = _now()
        self.expires_at = self.updated_at + SESSION_TTL

    def is_expired(self) -> bool:
        return _now() >= self.expires_at

    def append_history(self, msg: HistoryMsg) -> None:
        self.history.append(msg)
        if len(self.history) > MAX_HISTORY_MSGS:
            self.history = self.history[-MAX_HISTORY_MSGS:]


class SessionStore(Protocol):
    async def get(self, phone_e164: str) -> Session | None: ...

    async def upsert(self, session: Session) -> None: ...

    async def delete(self, phone_e164: str) -> None: ...

    async def gc(self) -> int: ...


class InMemorySessionStore:
    def __init__(self) -> None:
        self._rows: dict[str, Session] = {}

    async def get(self, phone_e164: str) -> Session | None:
        row = self._rows.get(phone_e164)
        if row is None:
            return None
        if row.is_expired():
            del self._rows[phone_e164]
            return None
        return row

    async def upsert(self, session: Session) -> None:
        session.touch()
        self._rows[session.phone_e164] = session

    async def delete(self, phone_e164: str) -> None:
        self._rows.pop(phone_e164, None)

    async def gc(self) -> int:
        before = len(self._rows)
        self._rows = {k: v for k, v in self._rows.items() if not v.is_expired()}
        return before - len(self._rows)


class LanguagePrefs:
    """Per-phone language choice that outlives sessions; LRU-bounded at ``cap``.

    Flows are rebuilt per webhook, so hold one per channel for the process and inject it.
    """

    def __init__(self, cap: int = 10_000) -> None:
        self._cap = cap
        self._rows: OrderedDict[str, str] = OrderedDict()

    def get(self, phone_e164: str) -> str | None:
        return self._rows.get(phone_e164)

    def set(self, phone_e164: str, lang: str) -> None:
        self._rows[phone_e164] = lang
        self._rows.move_to_end(phone_e164)
        while len(self._rows) > self._cap:
            self._rows.popitem(last=False)
