"""Integration tests for the router-level `require_coordinator` dependency.

The gate is wired on `apps/api/src/api/admin/__init__.py` so every
`/admin/*` route inherits it. We verify the four boundary cases:
missing header, garbage token, valid coordinator, non-coordinator role.

We stub the JWT verifier via `app.dependency_overrides` rather than
standing up a real Supabase JWKS — the verifier is exercised in
`test_auth_jwt_verifier.py`; here we only assert the *gate behaviour*
on the admin router.
"""

from __future__ import annotations

import dataclasses
import time
from collections.abc import Iterator
from typing import Any

import jwt as pyjwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
from fastapi.testclient import TestClient

from api.auth.jwt import StaticKeyJwtVerifier
from api.core.app_state import AppState, get_app_state
from api.main import app

pytestmark = pytest.mark.real_auth


class _StubVerifier:
    def __init__(self, claims: dict[str, Any] | None) -> None:
        self._claims = claims

    def verify(self, token: str) -> dict[str, Any]:
        if self._claims is None:
            import jwt as _jwt

            raise _jwt.InvalidSignatureError("stub: invalid")
        return self._claims


def _claims_for(role: str) -> dict[str, Any]:
    return {
        "sub": "11111111-1111-4111-8111-111111111111",
        "aud": "authenticated",
        "iss": "test-issuer",
        "exp": 9_999_999_999,
        "iat": 1,
        "email": "coord@example.com",
        "app_metadata": {"role": role},
    }


def _override_state(real: AppState, verifier: _StubVerifier) -> AppState:
    return dataclasses.replace(real, jwt_verifier=verifier)


@pytest.fixture
def client_with_verifier() -> Iterator[tuple[TestClient, dict[str, Any]]]:
    """Yields (client, mutable_dict) — mutate `verifier_state["claims"]` per
    test before each request to control what the stub verifier returns.
    """
    # `None` claims => raise on verify; otherwise return the dict.
    verifier_state: dict[str, Any] = {"claims": None}

    with TestClient(app) as client:
        real_state = app.state.container

        def _stub_get_state() -> AppState:
            return _override_state(real_state, _StubVerifier(verifier_state["claims"]))

        app.dependency_overrides[get_app_state] = _stub_get_state
        try:
            yield client, verifier_state
        finally:
            app.dependency_overrides.pop(get_app_state, None)


def test_admin_route_without_authorization_header_returns_401(
    client_with_verifier: tuple[TestClient, dict[str, Any]],
) -> None:
    client, _ = client_with_verifier
    res = client.get("/admin/crises")
    assert res.status_code == 401, res.text


def test_admin_route_with_non_bearer_authorization_returns_401(
    client_with_verifier: tuple[TestClient, dict[str, Any]],
) -> None:
    client, _ = client_with_verifier
    res = client.get(
        "/admin/crises",
        headers={"Authorization": "Basic dXNlcjpwYXNz"},
    )
    assert res.status_code == 401


def test_admin_route_with_garbage_bearer_returns_401(
    client_with_verifier: tuple[TestClient, dict[str, Any]],
) -> None:
    client, state = client_with_verifier
    # Verifier stub raises on `None` claims, simulating a bad signature.
    state["claims"] = None
    res = client.get(
        "/admin/crises",
        headers={"Authorization": "Bearer not-a-real-jwt"},
    )
    assert res.status_code == 401


def test_admin_route_with_valid_coordinator_token_passes_gate(
    client_with_verifier: tuple[TestClient, dict[str, Any]],
) -> None:
    client, state = client_with_verifier
    state["claims"] = _claims_for("coordinator")
    res = client.get(
        "/admin/crises",
        headers={"Authorization": "Bearer good-token"},
    )
    # The gate accepts the token; the underlying route may still 500 if
    # local Supabase is offline. The only assertion here is that it does
    # NOT return 401/403 — i.e. the gate let the request through.
    assert res.status_code not in (401, 403), res.text


def test_admin_route_with_admin_role_passes_gate(
    client_with_verifier: tuple[TestClient, dict[str, Any]],
) -> None:
    client, state = client_with_verifier
    state["claims"] = _claims_for("admin")
    res = client.get(
        "/admin/crises",
        headers={"Authorization": "Bearer good-token"},
    )
    assert res.status_code not in (401, 403)


def test_admin_route_with_non_coordinator_role_returns_403(
    client_with_verifier: tuple[TestClient, dict[str, Any]],
) -> None:
    client, state = client_with_verifier
    state["claims"] = _claims_for("citizen")
    res = client.get(
        "/admin/crises",
        headers={"Authorization": "Bearer good-token"},
    )
    assert res.status_code == 403


def test_admin_route_accepts_es256_token_with_app_metadata_role() -> None:
    """Regression: local Supabase signs with ES256 and puts the role under
    `app_metadata.role`. A token with that exact shape, verified by the real
    `StaticKeyJwtVerifier`, must clear `require_coordinator` end-to-end.

    This is the bug-1 regression: an ES256 token previously failed the
    `RS256`-only allowlist with `InvalidAlgorithmError` → 401 invalid token.
    """
    audience = "authenticated"
    issuer = "http://127.0.0.1:54321/auth/v1"
    private = ec.generate_private_key(ec.SECP256R1())
    public = private.public_key()
    verifier = StaticKeyJwtVerifier(public, audience, issuer)

    now = int(time.time())
    pem = private.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    token = pyjwt.encode(
        {
            "iat": now,
            "exp": now + 900,
            "aud": audience,
            "iss": issuer,
            "sub": "44444444-4444-4444-8444-444444444444",
            "email": "coord@example.com",
            # The shape Supabase emits: role lives inside app_metadata, not
            # at the top level. (Top-level `role` is Supabase's PostgREST
            # role like "authenticated"; not what `require_coordinator`
            # gates on.)
            "app_metadata": {"role": "admin", "provider": "email"},
            "role": "authenticated",
        },
        pem,
        algorithm="ES256",
    )

    with TestClient(app) as client:
        real_state = app.state.container

        def _stub_get_state() -> AppState:
            return dataclasses.replace(real_state, jwt_verifier=verifier)

        app.dependency_overrides[get_app_state] = _stub_get_state
        try:
            res = client.get(
                "/admin/crises",
                headers={"Authorization": f"Bearer {token}"},
            )
        finally:
            app.dependency_overrides.pop(get_app_state, None)
    assert res.status_code not in (401, 403), res.text


def test_admin_route_with_missing_subject_returns_401(
    client_with_verifier: tuple[TestClient, dict[str, Any]],
) -> None:
    client, state = client_with_verifier
    state["claims"] = {
        "aud": "authenticated",
        "iss": "test-issuer",
        "exp": 9_999_999_999,
        "iat": 1,
        "app_metadata": {"role": "coordinator"},
    }
    res = client.get(
        "/admin/crises",
        headers={"Authorization": "Bearer good-token"},
    )
    assert res.status_code == 401
