"""Regression test for the JWT verifier's issuer wiring.

This is the test that would have caught the InvalidIssuerError bug shipped
in the original coordinator-auth-model fix.

Background. Supabase Auth stamps `iss` into every access token based on
the **public** URL the auth request arrived on (the browser-facing URL).
In a Docker Compose setup the API talks to Supabase Auth over a private
network using a different hostname (`supabase_kong_undp:8000`), so the
API's `supabase_url` setting differs from `supabase_public_url`. The
original `build_app_state` derived the expected issuer from
`supabase_url`, which meant every admin call failed verification with
`InvalidIssuerError` in any Compose-based stack.

The fix derives the verifier's issuer from `supabase_public_url`. This
test pins that wiring so a future refactor cannot silently regress it.
"""

from __future__ import annotations

import time
from typing import Any
from unittest.mock import patch

import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec

from api.core.app_state import build_app_state
from api.core.config import Settings

pytestmark = pytest.mark.real_auth


def _settings(*, supabase_url: str, supabase_public_url: str) -> Settings:
    """Construct a `Settings` instance bypassing the env-file loader.

    Direct construction is intentional: we want the test to exercise the
    code that reads off `Settings`, not the dotenv loader.
    """
    return Settings(  # type: ignore[call-arg]
        database_url="postgresql+asyncpg://test/x",
        supabase_url=supabase_url,
        supabase_public_url=supabase_public_url,
        supabase_service_role_key="test-service-role",
        supabase_storage_bucket="test-bucket",
        reserved_crisis_name="Other / Unspecified",
        redis_dsn="redis://localhost:6379/0",
    )


def _sign_ec_token(*, iss: str, aud: str = "authenticated") -> tuple[str, Any]:
    """Mint an ES256-signed token (matches what local Supabase produces)."""
    private = ec.generate_private_key(ec.SECP256R1())
    public = private.public_key()
    pem = private.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    now = int(time.time())
    token = jwt.encode(
        {
            "iat": now,
            "exp": now + 600,
            "aud": aud,
            "iss": iss,
            "sub": "11111111-1111-4111-8111-111111111111",
            "app_metadata": {"role": "admin"},
        },
        pem,
        algorithm="ES256",
    )
    return token, public


@pytest.fixture(autouse=True)
def _stub_redis() -> Any:  # pyright: ignore[reportUnusedFunction]
    """`build_app_state` constructs a redis client eagerly via
    `redis.asyncio.from_url`. Stub it so this test does not require a
    running Redis. The JWKS path inside `JwtVerifier.__init__` only
    constructs a `PyJWKClient` (which does not fetch eagerly); the actual
    JWKS URL is never hit in this test because we read `_issuer`
    off the constructed verifier rather than calling `verify`.
    """
    with patch("redis.asyncio.from_url") as redis_from_url:

        class _StubRedis:
            async def aclose(self) -> None: ...

        redis_from_url.return_value = _StubRedis()
        yield


def test_issuer_derived_from_public_url_when_internal_url_differs() -> None:
    """In Compose, `supabase_url` is the docker-internal hostname (used
    for server-to-server calls) and `supabase_public_url` is the
    browser-facing hostname (the host Supabase Auth stamps into `iss`).
    The verifier's issuer must come from the public URL.
    """
    settings = _settings(
        supabase_url="http://supabase_kong_undp:8000",
        supabase_public_url="http://127.0.0.1:54321",
    )

    state = build_app_state(settings)

    # The concrete `JwtVerifier` exposes `_issuer` as the source of truth.
    # We assert against it directly — this is the contract the bug broke.
    assert state.jwt_verifier._issuer == "http://127.0.0.1:54321/auth/v1"  # type: ignore[attr-defined]


def test_issuer_explicit_override_wins() -> None:
    """Operators can pin `supabase_jwt_issuer` to handle exotic setups
    (custom domains, fronting proxies). That override must take
    precedence over both URL settings.
    """
    settings = _settings(
        supabase_url="http://supabase_kong_undp:8000",
        supabase_public_url="http://127.0.0.1:54321",
    )
    # `model_copy(update=...)` is the supported way to mutate Settings
    settings = settings.model_copy(
        update={"supabase_jwt_issuer": "https://auth.example.com/auth/v1"}
    )

    state = build_app_state(settings)

    assert state.jwt_verifier._issuer == "https://auth.example.com/auth/v1"  # type: ignore[attr-defined]


def test_real_supabase_token_passes_verification_when_issuer_matches_public_url() -> None:
    """End-to-end check: an ES256-signed token whose `iss` matches the
    *public* URL must verify successfully, even when `supabase_url`
    (server-side) is a completely different hostname.

    This is the scenario that broke in production-equivalent Compose dev:
    the token says `iss=http://127.0.0.1:54321/auth/v1`, the API's
    `supabase_url` is `http://supabase_kong_undp:8000`, and the fix is
    to derive the verifier's issuer from `supabase_public_url`.
    """
    from api.auth.jwt import StaticKeyJwtVerifier

    public_url = "http://127.0.0.1:54321"
    token, public_key = _sign_ec_token(iss=f"{public_url}/auth/v1")

    # Mirror what build_app_state does, but with a static-key verifier so
    # we do not have to stand up a JWKS server.
    verifier = StaticKeyJwtVerifier(
        public_key,
        audience="authenticated",
        issuer=f"{public_url}/auth/v1",  # ← what the fix derives
        algorithms=("RS256", "ES256"),
    )

    claims = verifier.verify(token)
    assert claims["app_metadata"]["role"] == "admin"


def test_real_supabase_token_fails_when_issuer_derived_from_internal_url() -> None:
    """This is the **negative** that the broken wiring produced. Pin it
    so the bug cannot silently come back: if a future refactor flips
    `app_state` back to deriving the issuer from `supabase_url`, this
    test demonstrates why it breaks.
    """
    from api.auth.jwt import StaticKeyJwtVerifier

    public_url = "http://127.0.0.1:54321"
    internal_url = "http://supabase_kong_undp:8000"
    token, public_key = _sign_ec_token(iss=f"{public_url}/auth/v1")

    verifier = StaticKeyJwtVerifier(
        public_key,
        audience="authenticated",
        issuer=f"{internal_url}/auth/v1",  # ← what the broken code did
        algorithms=("RS256", "ES256"),
    )

    with pytest.raises(jwt.InvalidIssuerError):
        verifier.verify(token)
