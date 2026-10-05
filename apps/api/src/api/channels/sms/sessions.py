"""SMS session store: a separate instance so SMS and WhatsApp never collide on phone keys."""

from __future__ import annotations

from functools import lru_cache

from api.channels.sessions import InMemorySessionStore, LanguagePrefs, SessionStore


@lru_cache(maxsize=1)
def get_sms_session_store() -> SessionStore:
    return InMemorySessionStore()


@lru_cache(maxsize=1)
def get_sms_language_prefs() -> LanguagePrefs:
    return LanguagePrefs()
