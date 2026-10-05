"""JWT verification against Supabase's public JWKS.

PyJWKClient re-fetches the JWKS once on a kid miss, which covers signing-key rotation.
Supabase signs with RS256 or ES256 depending on project age; HS256 is unsupported since
the API never holds the JWT secret. The kid header selects the key, so allowing both
algorithms does not weaken verification.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any, Protocol

import jwt
from jwt import PyJWKClient


class TokenVerifier(Protocol):
    def verify(self, token: str) -> dict[str, Any]: ...


class JwtVerifier:
    def __init__(
        self,
        jwks_url: str,
        audience: str,
        issuer: str,
        *,
        algorithms: Iterable[str] = ("RS256", "ES256"),
    ) -> None:
        self._jwks_client = PyJWKClient(jwks_url, cache_keys=True)
        self._audience = audience
        self._issuer = issuer
        self._algorithms = list(algorithms)

    def verify(self, token: str) -> dict[str, Any]:
        signing_key = self._jwks_client.get_signing_key_from_jwt(token).key
        return jwt.decode(
            token,
            signing_key,
            algorithms=self._algorithms,
            audience=self._audience,
            issuer=self._issuer,
            options={"require": ["exp", "iat", "aud", "iss", "sub"]},
        )


class StaticKeyJwtVerifier:
    """Verifier backed by an in-memory public key, for tests without a JWKS server."""

    def __init__(
        self,
        public_key: Any,
        audience: str,
        issuer: str,
        *,
        algorithms: Iterable[str] = ("RS256", "ES256"),
    ) -> None:
        self._public_key = public_key
        self._audience = audience
        self._issuer = issuer
        self._algorithms = list(algorithms)

    def verify(self, token: str) -> dict[str, Any]:
        return jwt.decode(
            token,
            self._public_key,
            algorithms=self._algorithms,
            audience=self._audience,
            issuer=self._issuer,
            options={"require": ["exp", "iat", "aud", "iss", "sub"]},
        )
