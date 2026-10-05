"""Typed accessors for the resources `on_startup` puts on the Arq ctx.

Kept apart from `workers.settings` so job modules can import them at module
level; `settings` imports every job, so importing it from a job is a cycle.
"""

from __future__ import annotations

from typing import cast

from arq.connections import ArqRedis
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from api.ai.client import AIClients
from api.areas.nominatim_client import OSMNominatimClient
from api.core.storage import StorageClient

CTX_ENGINE = "db_engine"
CTX_SESSIONMAKER = "db_sessionmaker"
CTX_AI_CLIENTS = "ai_clients"
CTX_STORAGE = "storage_client"
CTX_NOMINATIM = "nominatim_client"


def sessionmaker_from_ctx(ctx: dict[str, object]) -> async_sessionmaker[AsyncSession]:
    sm = ctx.get(CTX_SESSIONMAKER)
    if sm is None:
        raise RuntimeError("worker sessionmaker missing; did on_startup run?")
    return cast("async_sessionmaker[AsyncSession]", sm)


def ai_clients_from_ctx(ctx: dict[str, object]) -> AIClients:
    bundle = ctx.get(CTX_AI_CLIENTS)
    if not isinstance(bundle, AIClients):
        raise RuntimeError("worker AI clients missing; did on_startup run?")
    return bundle


def storage_from_ctx(ctx: dict[str, object]) -> StorageClient:
    storage = ctx.get(CTX_STORAGE)
    if storage is None:
        raise RuntimeError("worker storage client missing; did on_startup run?")
    return cast("StorageClient", storage)


def nominatim_from_ctx(ctx: dict[str, object]) -> OSMNominatimClient:
    client = ctx.get(CTX_NOMINATIM)
    if not isinstance(client, OSMNominatimClient):
        raise RuntimeError("worker Nominatim client missing; did on_startup run?")
    return client


def arq_pool_from_ctx(ctx: dict[str, object]) -> ArqRedis:
    """Arq puts its own Redis pool on ctx under "redis"."""
    pool = ctx.get("redis")
    if pool is None:
        raise RuntimeError("arq redis pool missing from ctx")
    return cast(ArqRedis, pool)
