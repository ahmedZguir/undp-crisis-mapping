# pyright: reportMissingTypeStubs=false, reportUnknownMemberType=false, reportUnknownVariableType=false, reportUnknownArgumentType=false
"""Twilio voice-webhook signature validation (``TwilioSignatureValidator``)."""

from __future__ import annotations

from twilio.request_validator import RequestValidator

from api.channels.ivr.signing import TwilioSignatureValidator

TOKEN = "test-auth-token"  # gitleaks:allow — dummy fixture, not a real key
URL = "https://example.test/ivr/input"
PARAMS = {"CallSid": "CA123", "Digits": "1", "From": "+15551234567"}


def _sign(token: str, url: str, params: dict[str, str]) -> str:
    return str(RequestValidator(token).compute_signature(url, params))


def test_valid_signature_passes() -> None:
    sig = _sign(TOKEN, URL, PARAMS)
    assert TwilioSignatureValidator(TOKEN).validate(URL, PARAMS, sig) is True


def test_wrong_token_fails() -> None:
    sig = _sign("a-different-token", URL, PARAMS)
    assert TwilioSignatureValidator(TOKEN).validate(URL, PARAMS, sig) is False


def test_tampered_params_fail() -> None:
    sig = _sign(TOKEN, URL, PARAMS)
    tampered = {**PARAMS, "Digits": "9"}
    assert TwilioSignatureValidator(TOKEN).validate(URL, tampered, sig) is False


def test_wrong_url_fails() -> None:
    sig = _sign(TOKEN, URL, PARAMS)
    assert TwilioSignatureValidator(TOKEN).validate(URL + "x", PARAMS, sig) is False
