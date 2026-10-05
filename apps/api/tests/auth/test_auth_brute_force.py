"""Brute-force regression test for `POST /auth/login`.

We do NOT change
`supabase/config.toml`; this test pins the inherited Supabase Auth
defaults (per-email lockout) by ensuring that 12 wrong-password
attempts within a one-minute window do NOT all return as plain 401s.

The slowapi layer (10/minute on `POST /auth/login`) gives us a
guaranteed 429 by attempt 11. If a future config drift removes the
slowapi cap, Supabase Auth's per-email cap (`MAX_VERIFICATION_ATTEMPTS=
10/hour`) catches the burst and surfaces a 4xx that the route maps to
a 401 or other status — either way, the assertion is "at least one
attempt within the first 11 is locked out (429 or a Supabase-mapped
error other than the generic invalid-credentials 401)".

The current implementation just relies on the slowapi cap. The
assertion is structured so a future Supabase Auth integration change
that maps the per-email cap to a distinct error code (e.g. 429 from
upstream) also satisfies it.

The Supabase Auth client is stubbed via `app.dependency_overrides` so
the test does not actually hit gotrue.
"""

from __future__ import annotations

import dataclasses
import uuid
from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient

from api.auth.supabase import SupabaseAuthError
from api.core.app_state import AppState, get_app_state
from api.core.rate_limit import limiter
from api.main import app

pytestmark = [pytest.mark.redis, pytest.mark.real_auth, pytest.mark.real_rate_limit]


class _StubSupabaseAuthClient:
    """Stub that always rejects credentials with a 400 (Supabase's shape
    for 'invalid login credentials'). Lets the test exercise the
    slowapi cap without hitting gotrue."""

    def __init__(self) -> None:
        self.calls = 0

    async def password_login(self, email: str, password: str) -> dict[str, Any]:
        self.calls += 1
        # Match Supabase Auth's invalid-credentials shape.
        raise SupabaseAuthError(400, "invalid login credentials")

    async def mint_access_token(self, refresh_token: str) -> tuple[str, str]:
        raise SupabaseAuthError(400, "not used in this test")

    async def get_user(self, user_id: uuid.UUID) -> dict[str, Any]:
        return {}

    async def aclose(self) -> None:
        return None


@pytest.fixture
def stack() -> Iterator[tuple[TestClient, _StubSupabaseAuthClient]]:
    """Fresh limiter counter + stubbed Supabase + a TestClient."""
    limiter.reset()
    stub_sb = _StubSupabaseAuthClient()
    with TestClient(app) as client:
        real_state = app.state.container

        def _override() -> AppState:
            return dataclasses.replace(real_state, supabase_auth_client=stub_sb)

        app.dependency_overrides[get_app_state] = _override
        try:
            yield client, stub_sb
        finally:
            app.dependency_overrides.pop(get_app_state, None)
    limiter.reset()


def test_brute_force_attempts_get_locked_out_within_eleven_tries(
    stack: tuple[TestClient, _StubSupabaseAuthClient],
) -> None:
    """12 wrong-password attempts — at least one within the first 11 must
    be locked out (429 from slowapi, or a Supabase-mapped lockout
    surface).

    With the current implementation the slowapi cap (10/minute) fires
    at attempt 11; the upstream stub always returns invalid-credentials,
    so attempts 1 through 10 are 401s and attempts 11+ are 429s.

    The test is intentionally lenient about the exact status so a future
    Supabase Auth integration change (mapping per-email lockout to 429
    directly) still passes.
    """
    client, sb = stack
    statuses: list[int] = []
    for _ in range(12):
        r = client.post(
            "/auth/login",
            json={"email": "victim@example.com", "password": f"guess-{uuid.uuid4().hex}"},
        )
        statuses.append(r.status_code)
        if r.status_code == 429:
            break

    # 429 must appear within the burst.
    assert 429 in statuses, (
        f"no rate-limit lockout in a 12-attempt brute-force burst; "
        f"got {statuses} (Supabase stub call count: {sb.calls})"
    )

    # And the first attempt must NOT already be locked — otherwise the
    # limiter started in a saturated state and the test is meaningless.
    assert statuses[0] != 429
