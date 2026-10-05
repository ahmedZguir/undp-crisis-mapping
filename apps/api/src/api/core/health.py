"""Liveness (/healthz, alias /health) and readiness (/readyz) probes."""

from __future__ import annotations

import asyncio
from typing import Annotated, Any

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from sqlalchemy import text

from api.core.app_state import AppState, get_app_state

router = APIRouter()


# No I/O: a dependency outage must not make the orchestrator restart the process.
async def _liveness() -> dict[str, str]:
    return {"status": "ok"}


router.get("/healthz", include_in_schema=False)(_liveness)
router.get("/health", include_in_schema=False)(_liveness)


@router.get("/readyz", include_in_schema=False)
async def readyz(
    state: Annotated[AppState, Depends(get_app_state)],
) -> JSONResponse:
    """Probe Postgres and Redis (1 s each); 503 lists failures as "<dep>:<ExcType>"."""
    failures: list[str] = []

    try:
        async with asyncio.timeout(1.0):
            async with state.engine.connect() as conn:
                await conn.execute(text("select 1"))
    except Exception as exc:
        failures.append(f"db:{type(exc).__name__}")

    redis_client: Any = state.redis_client
    if redis_client is None:
        failures.append("redis:NotConfigured")
    else:
        try:
            async with asyncio.timeout(1.0):
                await redis_client.ping()
        except Exception as exc:
            failures.append(f"redis:{type(exc).__name__}")

    if failures:
        return JSONResponse(
            {"status": "not_ready", "failures": failures},
            status_code=503,
        )
    return JSONResponse({"status": "ready"})
