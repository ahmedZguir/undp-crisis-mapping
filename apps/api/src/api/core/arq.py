"""Enqueue Arq jobs from the API process.

Handlers enqueue by job name and never import job functions.
"""

from __future__ import annotations

import asyncio
import uuid
from typing import Protocol

from arq import create_pool
from arq.connections import ArqRedis, RedisSettings


class ArqPool:
    """One Redis pool per process, connected on first use so startup needs no Redis."""

    def __init__(self, redis_dsn: str) -> None:
        self._redis_dsn = redis_dsn
        self._pool: ArqRedis | None = None
        self._lock = asyncio.Lock()

    async def get(self) -> ArqRedis:
        async with self._lock:
            if self._pool is None:
                self._pool = await create_pool(RedisSettings.from_dsn(self._redis_dsn))
            return self._pool

    async def aclose(self) -> None:
        if self._pool is not None:
            await self._pool.aclose()
            self._pool = None


class JobEnqueuer(Protocol):
    """Seam over Arq so routes and services are testable without Redis."""

    async def enqueue(self, entity_id: uuid.UUID, /) -> str: ...


class ArqEnqueuer:
    """Enqueues one named job whose only argument is an id."""

    def __init__(self, pool: ArqPool, job_name: str) -> None:
        self._pool = pool
        self._job_name = job_name

    async def enqueue(self, entity_id: uuid.UUID) -> str:
        pool = await self._pool.get()
        job = await pool.enqueue_job(self._job_name, str(entity_id))
        if job is None:  # Arq returns None only when the job id is deduplicated.
            return ""
        return job.job_id
