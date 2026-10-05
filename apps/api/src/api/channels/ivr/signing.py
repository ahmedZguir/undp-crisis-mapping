# pyright: reportMissingTypeStubs=false, reportUnknownMemberType=false, reportUnknownVariableType=false, reportUnknownArgumentType=false
"""Typed wrapper over Twilio's unstubbed RequestValidator so routes.py stays strict-clean."""

from __future__ import annotations

from twilio.request_validator import RequestValidator


class TwilioSignatureValidator:
    def __init__(self, auth_token: str) -> None:
        self._validator = RequestValidator(auth_token)

    def validate(self, url: str, params: dict[str, str], signature: str) -> bool:
        return bool(self._validator.validate(url, params, signature))
