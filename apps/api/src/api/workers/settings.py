"""Arq worker entrypoint: `uv run arq api.workers.settings.WorkerSettings`.

The engine and AI clients are built in `on_startup` so they bind to the loop
Arq runs jobs on; jobs read them off `ctx`. Logging is configured in
`on_startup`, and the env-dependent `WorkerSettings` fields are filled on first
access, so this module does neither at import.
"""

from __future__ import annotations

import functools
import logging
from typing import Any, ClassVar

from arq.connections import RedisSettings
from arq.cron import cron
from arq.worker import func
from sqlalchemy.ext.asyncio import AsyncEngine

from api.ai.client import AIClients, aclose_ai_clients, build_ai_clients
from api.analysis.job import generate_crisis_report
from api.areas.nominatim_client import OSMNominatimClient
from api.core.config import get_settings
from api.core.db import make_engine, make_sessionmaker
from api.core.log_filters import ArqCronTickFilter
from api.core.storage import (
    StorageClient,
)
from api.photo_export.job import export_crisis_photos
from api.workers.buildings import ingest_buildings
from api.workers.context import (
    CTX_AI_CLIENTS,
    CTX_ENGINE,
    CTX_NOMINATIM,
    CTX_SESSIONMAKER,
    CTX_STORAGE,
)
from api.workers.crises import tick_report_retention, tick_scheduled_activations
from api.workers.embed_report import embed_report
from api.workers.enrichment import (
    SUB_JOB_MAX_TRIES,
    caption_image,
    enrich_report,
    translate_field,
)
from api.workers.geocoding import geocode_report
from api.workers.scoring import score_report

logger = logging.getLogger(__name__)


def _configure_logging() -> None:
    # Arq configures only its own logger; without this, app INFO logs are dropped.
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    # The per-minute activation cron is usually a no-op; drop arq's INFO lines for it.
    logging.getLogger("arq.worker").addFilter(
        ArqCronTickFilter(tick_scheduled_activations.__name__)
    )


async def on_startup(ctx: dict[str, object]) -> None:
    """Runs once per worker on the job loop; the DB and httpx pools bind to it."""
    _configure_logging()
    settings = get_settings()
    engine = make_engine(settings.database_url)
    ctx[CTX_ENGINE] = engine
    ctx[CTX_SESSIONMAKER] = make_sessionmaker(engine)
    ctx[CTX_AI_CLIENTS] = build_ai_clients(settings)
    ctx[CTX_STORAGE] = StorageClient(settings)
    # Shared so all geocode lookups in this process go through one rate-limit lock.
    ctx[CTX_NOMINATIM] = OSMNominatimClient()


async def on_shutdown(ctx: dict[str, object]) -> None:
    ai_clients = ctx.get(CTX_AI_CLIENTS)
    if isinstance(ai_clients, AIClients):
        await aclose_ai_clients(ai_clients)
    nominatim = ctx.get(CTX_NOMINATIM)
    if isinstance(nominatim, OSMNominatimClient):
        await nominatim.aclose()
    engine = ctx.get(CTX_ENGINE)
    if isinstance(engine, AsyncEngine):
        await engine.dispose()


def _redis_settings() -> RedisSettings:
    settings = RedisSettings.from_dsn(get_settings().redis_dsn)
    # arq's 1s default lets a short Redis stall in the cron loop raise TimeoutError
    # and kill the worker.
    settings.conn_timeout = 10
    return settings


class _WorkerSettings:
    # arq has no per-enqueue max_tries, so retry budgets are set here. Jobs
    # calling vLLM or Nominatim retry on transient errors; the rest run once.
    functions: ClassVar[list[object]] = [
        ingest_buildings,
        enrich_report,
        func(translate_field, max_tries=SUB_JOB_MAX_TRIES),
        func(caption_image, max_tries=SUB_JOB_MAX_TRIES),
        func(embed_report, max_tries=SUB_JOB_MAX_TRIES),
        func(geocode_report, max_tries=SUB_JOB_MAX_TRIES),
        func(score_report, max_tries=SUB_JOB_MAX_TRIES),
        # These two retry only via an admin re-POST, which creates a fresh row.
        generate_crisis_report,
        export_crisis_photos,
    ]
    cron_jobs: ClassVar[list[object]] = [
        cron(tick_scheduled_activations, minute=set(range(0, 60))),
        cron(tick_report_retention, hour={3}, minute={0}),
    ]
    # Read env, so they are set by _worker_settings() on first access.
    redis_settings: ClassVar[RedisSettings]
    max_jobs: ClassVar[int]
    on_startup: ClassVar[Any] = on_startup
    on_shutdown: ClassVar[Any] = on_shutdown
    max_tries: ClassVar[int] = 1
    # Continent-scale building ingests take hours; this only catches a true wedge.
    job_timeout: ClassVar[int] = 48 * 60 * 60


@functools.cache
def _worker_settings() -> type[_WorkerSettings]:
    _WorkerSettings.redis_settings = _redis_settings()
    _WorkerSettings.max_jobs = max(1, get_settings().worker_max_jobs)
    return _WorkerSettings


def __getattr__(name: str) -> object:
    # arq loads `WorkerSettings` with getattr, which lands here.
    if name == "WorkerSettings":
        return _worker_settings()
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
