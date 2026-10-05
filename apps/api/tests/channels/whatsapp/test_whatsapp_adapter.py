"""Twilio signature validation, checked against a hand-computed expected value."""

from __future__ import annotations

import base64
import hashlib
import hmac

from api.channels.whatsapp.adapter import TwilioAdapter


def _expected_sig(token: str, url: str, params: dict[str, str]) -> str:
    payload = url
    for key in sorted(params.keys()):
        payload += key + params[key]
    digest = hmac.new(token.encode(), payload.encode(), hashlib.sha1).digest()
    return base64.b64encode(digest).decode()


def test_validate_signature_accepts_correct() -> None:
    adapter = TwilioAdapter(account_sid="ACtest", auth_token="secret", from_="whatsapp:+1")
    url = "https://example.test/whatsapp/webhook"
    params = {"From": "whatsapp:+9665", "Body": "hi", "NumMedia": "0"}
    sig = _expected_sig("secret", url, params)
    assert adapter.validate_signature(url, params, sig) is True


def test_validate_signature_rejects_tampered() -> None:
    adapter = TwilioAdapter(account_sid="ACtest", auth_token="secret", from_="whatsapp:+1")
    url = "https://example.test/whatsapp/webhook"
    params = {"From": "whatsapp:+9665", "Body": "hi"}
    sig = _expected_sig("secret", url, params)
    # Tamper with the body — signature must no longer match.
    params["Body"] = "hi attacker"
    assert adapter.validate_signature(url, params, sig) is False


def test_validate_signature_rejects_wrong_token() -> None:
    adapter = TwilioAdapter(account_sid="ACtest", auth_token="secret", from_="whatsapp:+1")
    url = "https://example.test/whatsapp/webhook"
    params = {"Body": "hi"}
    sig_with_wrong_token = _expected_sig("not-the-secret", url, params)
    assert adapter.validate_signature(url, params, sig_with_wrong_token) is False
