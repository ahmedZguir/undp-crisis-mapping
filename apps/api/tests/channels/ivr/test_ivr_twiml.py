"""TwiML actions must be document-relative (``input``) so they survive Caddy's ``/api`` prefix."""

from __future__ import annotations

from api.channels.ivr.twiml import ACTION_INPUT, gather, record


def test_action_is_document_relative_not_root_relative() -> None:
    # A leading slash would make Twilio resolve against the host root, dropping
    # any path prefix. Must stay relative to the called document.
    assert not ACTION_INPUT.startswith("/")
    assert ACTION_INPUT == "input"


def test_gather_renders_relative_action_and_redirect() -> None:
    xml = gather("Press a number", "en", num_options=3)
    assert 'action="input"' in xml
    # No root-relative action anywhere in the rendered TwiML.
    assert "/ivr/input" not in xml


def test_record_renders_relative_action() -> None:
    xml = record("Describe the damage", "en")
    assert 'action="input"' in xml
    assert "/ivr/input" not in xml
