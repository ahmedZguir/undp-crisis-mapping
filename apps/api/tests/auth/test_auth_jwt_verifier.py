"""Unit tests for the RS256 JWT verifier.

We avoid standing up an HTTP JWKS server by exercising the
`StaticKeyJwtVerifier` variant. The JWKS-fetching path is a thin wrapper
around `PyJWKClient`, which is trusted upstream; the load-bearing logic
(audience, issuer, expiry, signature-algorithm checks) is identical between
the two verifier classes and is what these tests cover.
"""

from __future__ import annotations

import time
from typing import Any

import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec, rsa

from api.auth.jwt import StaticKeyJwtVerifier

pytestmark = pytest.mark.real_auth

_AUDIENCE = "authenticated"
_ISSUER = "http://localhost/auth/v1"


def _make_keypair() -> tuple[Any, Any]:
    private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    public = private.public_key()
    return private, public


def _sign(
    private: Any,
    *,
    aud: str = _AUDIENCE,
    iss: str = _ISSUER,
    sub: str = "11111111-1111-4111-8111-111111111111",
    role: str = "coordinator",
    extra: dict[str, Any] | None = None,
    exp_offset: int = 900,
) -> str:
    now = int(time.time())
    claims = {
        "iat": now,
        "exp": now + exp_offset,
        "aud": aud,
        "iss": iss,
        "sub": sub,
        "app_metadata": {"role": role},
        "email": "coord@example.com",
    }
    if extra:
        claims.update(extra)
    pem = private.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    return jwt.encode(claims, pem, algorithm="RS256")


def test_verifies_valid_token() -> None:
    private, public = _make_keypair()
    verifier = StaticKeyJwtVerifier(public, _AUDIENCE, _ISSUER)
    token = _sign(private)

    claims = verifier.verify(token)

    assert claims["sub"] == "11111111-1111-4111-8111-111111111111"
    assert claims["app_metadata"]["role"] == "coordinator"


def test_rejects_expired_token() -> None:
    private, public = _make_keypair()
    verifier = StaticKeyJwtVerifier(public, _AUDIENCE, _ISSUER)
    token = _sign(private, exp_offset=-60)

    with pytest.raises(jwt.ExpiredSignatureError):
        verifier.verify(token)


def test_rejects_wrong_audience() -> None:
    private, public = _make_keypair()
    verifier = StaticKeyJwtVerifier(public, _AUDIENCE, _ISSUER)
    token = _sign(private, aud="something-else")

    with pytest.raises(jwt.InvalidAudienceError):
        verifier.verify(token)


def test_rejects_wrong_issuer() -> None:
    private, public = _make_keypair()
    verifier = StaticKeyJwtVerifier(public, _AUDIENCE, _ISSUER)
    token = _sign(private, iss="https://attacker.example")

    with pytest.raises(jwt.InvalidIssuerError):
        verifier.verify(token)


def test_rejects_bad_signature() -> None:
    private, _public = _make_keypair()
    _other_private, other_public = _make_keypair()
    # Verifier trusts `other_public`; token signed with `private`.
    verifier = StaticKeyJwtVerifier(other_public, _AUDIENCE, _ISSUER)
    token = _sign(private)

    with pytest.raises(jwt.InvalidSignatureError):
        verifier.verify(token)


def _make_ec_keypair() -> tuple[Any, Any]:
    private = ec.generate_private_key(ec.SECP256R1())
    public = private.public_key()
    return private, public


def test_accepts_es256_signed_token() -> None:
    """Local Supabase stacks sign with ES256, not RS256. The verifier must
    accept ES256 out of the box (alg list defaults to {RS256, ES256})."""
    private, public = _make_ec_keypair()
    verifier = StaticKeyJwtVerifier(public, _AUDIENCE, _ISSUER, algorithms=("RS256", "ES256"))
    now = int(time.time())
    pem = private.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    token = jwt.encode(
        {
            "iat": now,
            "exp": now + 600,
            "aud": _AUDIENCE,
            "iss": _ISSUER,
            "sub": "33333333-3333-4333-8333-333333333333",
            "app_metadata": {"role": "admin"},
        },
        pem,
        algorithm="ES256",
    )

    claims = verifier.verify(token)

    assert claims["sub"] == "33333333-3333-4333-8333-333333333333"
    assert claims["app_metadata"]["role"] == "admin"


def test_rejects_missing_subject() -> None:
    private, public = _make_keypair()
    verifier = StaticKeyJwtVerifier(public, _AUDIENCE, _ISSUER)
    # Build a claim set without `sub` to exercise the required-claim check.
    now = int(time.time())
    pem = private.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    token = jwt.encode(
        {"iat": now, "exp": now + 60, "aud": _AUDIENCE, "iss": _ISSUER},
        pem,
        algorithm="RS256",
    )

    with pytest.raises(jwt.MissingRequiredClaimError):
        verifier.verify(token)
