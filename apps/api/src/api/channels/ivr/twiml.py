# pyright: reportMissingTypeStubs=false, reportUnknownMemberType=false, reportUnknownVariableType=false, reportUnknownArgumentType=false
"""Typed TwiML builders. twilio is unstubbed, so its calls stay confined to this file.

Every Gather/Record posts to the input action; a trailing Redirect re-prompts on no input.
"""

from __future__ import annotations

from typing import Any, cast

from twilio.twiml.voice_response import VoiceResponse

# Relative so Twilio resolves it against the called URL; "/ivr/input" would drop
# the /api prefix behind Caddy.
ACTION_INPUT = "input"

_GATHER_TIMEOUT = 6  # seconds to wait for a keypress
_RECORD_MAX_LENGTH = 90  # seconds, hard cap per recording
_RECORD_SILENCE_TIMEOUT = 4  # seconds of silence that ends a recording
# Menus up to this many options take one keypress; longer ones need FINISH_KEY.
_MAX_SINGLE_KEY_OPTIONS = 9
# Ends a multi-digit pick or a recording ("press pound" in the spoken prompts).
FINISH_KEY = "#"


def gather(text: str, say_language: str, num_options: int, *, intro: str | None = None) -> str:
    """``intro`` is spoken before the menu (e.g. a greeting or the review read-back)."""
    return gather_segments(
        [(text, say_language)],
        num_options,
        intro=[(intro, say_language)] if intro else None,
    )


def gather_segments(
    segments: list[tuple[str, str]],
    num_options: int,
    *,
    intro: list[tuple[str, str]] | None = None,
) -> str:
    """Each segment is its own ``<Say>``, so the language picker reads each option in its voice."""
    vr = VoiceResponse()
    for text, say_language in intro or []:
        vr.say(text, language=say_language)
    key_kw: dict[str, Any] = (
        {"num_digits": 1}
        if num_options <= _MAX_SINGLE_KEY_OPTIONS
        else {"finish_on_key": FINISH_KEY}
    )
    g = cast(
        Any,
        vr.gather(
            action=ACTION_INPUT,
            method="POST",
            timeout=_GATHER_TIMEOUT,
            input="dtmf",
            **key_kw,
        ),
    )
    for text, say_language in segments:
        g.say(text, language=say_language)
    vr.redirect(ACTION_INPUT, method="POST")
    return str(vr)


def record(text: str, say_language: str) -> str:
    vr = VoiceResponse()
    vr.say(text, language=say_language)
    vr.record(
        action=ACTION_INPUT,
        method="POST",
        max_length=_RECORD_MAX_LENGTH,
        timeout=_RECORD_SILENCE_TIMEOUT,
        finish_on_key=FINISH_KEY,
        play_beep=True,
    )
    vr.redirect(ACTION_INPUT, method="POST")
    return str(vr)


def say_and_hangup(text: str, say_language: str) -> str:
    vr = VoiceResponse()
    vr.say(text, language=say_language)
    vr.hangup()
    return str(vr)
