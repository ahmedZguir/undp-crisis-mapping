"""IVR session store, keyed by Twilio CallSid.

``Session.phone_e164`` holds the CallSid; the caller's number is in
``session.scratch['ivr_from']``.
"""

from __future__ import annotations

from functools import lru_cache

from api.channels.sessions import InMemorySessionStore, SessionStore


@lru_cache(maxsize=1)
def get_ivr_session_store() -> SessionStore:
    return InMemorySessionStore()
