"""HTTP-side singletons, built and torn down by the FastAPI lifespan.

asyncpg binds connections to an event loop, so the engine is built per lifespan
rather than cached at import.

In-process state (caches, session stores, concurrency caps) assumes a single
API worker; with more workers each keeps its own copy.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Annotated, Any

from fastapi import Depends, FastAPI, Request
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from api.ai import AIClients, build_ai_clients
from api.ai.chat import SessionStore
from api.ai.client import aclose_ai_clients
from api.areas import OSMAreaSearcher, OSMNominatimClient

# Import auth submodules directly: api.auth.__init__ imports this module.
from api.auth.bootstrap import ensure_env_var_admin
from api.auth.jwt import JwtVerifier, TokenVerifier
from api.auth.refresh import RefreshTokenStore
from api.auth.supabase import SupabaseAuthClient, SupabaseAuthClientLike
from api.core.arq import ArqEnqueuer, ArqPool
from api.core.config import Settings, get_settings
from api.core.db import make_engine, make_sessionmaker
from api.core.lru import LRUCache
from api.core.storage import StorageClient
from api.crises.cache import CrisisRowCache
from api.crises.lookup import SqlAlchemyCrisisLookup
from api.crises.public_buildings_service import PublicBuildingsService
from api.crises.public_reports_service import PublicReportsService
from api.crises.service import CrisisService
from api.heatmap.stats import HeatmapStatsService
from api.heatmap.tiles import HeatmapTileService
from api.reports.deletion import CitizenReportDeletionService
from api.reports.history import CitizenReportsHistoryService
from api.reports.service import ReportSubmissionService


@dataclass(frozen=True, slots=True)
class AppState:
    """Eagerly built singletons; lazily built ones keep their own lru_cache."""

    engine: AsyncEngine
    sessionmaker: async_sessionmaker[AsyncSession]
    crisis_service: CrisisService
    # Exposed so the admin PATCH route can invalidate it on status changes.
    crisis_row_cache: CrisisRowCache
    report_service: ReportSubmissionService
    reports_history_service: CitizenReportsHistoryService
    report_deletion_service: CitizenReportDeletionService
    heatmap_tile_service: HeatmapTileService
    heatmap_stats_service: HeatmapStatsService
    public_buildings_service: PublicBuildingsService
    public_reports_service: PublicReportsService
    osm_area_searcher: OSMAreaSearcher
    # Concrete client, so the lifespan can aclose() it.
    osm_nominatim_client: OSMNominatimClient
    # Coordinator auth
    jwt_verifier: TokenVerifier
    supabase_auth_client: SupabaseAuthClientLike
    # Lifecycle handle for aclose(), not for routes.
    supabase_auth_client_concrete_: SupabaseAuthClient | None
    refresh_store: RefreshTokenStore
    redis_client: Any | None
    # Shared by every Arq enqueuer; connects on first enqueue.
    arq_pool: ArqPool
    storage: StorageClient
    # Shared httpx pools; the arq worker builds its own bundle.
    ai_clients: AIClients
    # Admin chat conversations, keyed by session id.
    chat_sessions: SessionStore
    # Resolved public crisis detail, keyed by (crisis_id, locale, form_version).
    crisis_detail_cache: LRUCache[tuple[str, str, int], dict[str, Any]]
    # Admin map query embeddings, keyed by "<embedding version>:<query>".
    map_query_vectors: LRUCache[str, list[float]]
    # Caps in-flight admin map aggregates; client debounce is no guarantee.
    map_concurrency: asyncio.Semaphore


def build_app_state(settings: Settings) -> AppState:
    """No I/O: connection pools stay cold until first use."""
    engine = make_engine(settings.database_url)
    sessionmaker = make_sessionmaker(engine)
    crisis_row_cache = CrisisRowCache()
    crisis_service = CrisisService(
        lookup=SqlAlchemyCrisisLookup(sessionmaker, cache=crisis_row_cache),
        reserved_name=settings.reserved_crisis_name,
    )
    storage = StorageClient(settings)
    arq_pool = ArqPool(settings.redis_dsn)
    report_service = ReportSubmissionService(
        storage=storage,
        crisis_service=crisis_service,
        sessionmaker=sessionmaker,
        enrichment_enqueuer=ArqEnqueuer(arq_pool, "enrich_report"),
    )
    reports_history_service = CitizenReportsHistoryService(
        sessionmaker=sessionmaker,
        url_signer=storage,
    )
    report_deletion_service = CitizenReportDeletionService(
        sessionmaker=sessionmaker,
        storage=storage,
    )
    heatmap_tile_service = HeatmapTileService(
        sessionmaker=sessionmaker, crisis_service=crisis_service
    )
    heatmap_stats_service = HeatmapStatsService(
        sessionmaker=sessionmaker, crisis_service=crisis_service
    )
    public_buildings_service = PublicBuildingsService(
        sessionmaker=sessionmaker, crisis_service=crisis_service
    )
    public_reports_service = PublicReportsService(
        sessionmaker=sessionmaker, crisis_service=crisis_service
    )
    osm_nominatim_client = OSMNominatimClient()

    # JWKS is fetched server-to-server on the internal URL, but Supabase Auth
    # stamps iss with the browser-facing host, so the issuer uses the public URL.
    jwks_url = f"{settings.supabase_url}/auth/v1/.well-known/jwks.json"
    issuer = settings.supabase_jwt_issuer or f"{settings.supabase_public_url}/auth/v1"
    jwt_verifier: TokenVerifier = JwtVerifier(
        jwks_url=jwks_url,
        audience=settings.supabase_jwt_audience,
        issuer=issuer,
    )
    supabase_auth_client_concrete = SupabaseAuthClient(settings)

    # redis.asyncio is poorly typed upstream, hence Any.
    import redis.asyncio as redis_asyncio

    redis_client: Any = redis_asyncio.from_url(  # pyright: ignore[reportUnknownMemberType]
        settings.redis_dsn, decode_responses=True
    )
    refresh_store = RefreshTokenStore(redis_client)

    ai_clients = build_ai_clients(settings)

    return AppState(
        engine=engine,
        sessionmaker=sessionmaker,
        crisis_service=crisis_service,
        crisis_row_cache=crisis_row_cache,
        report_service=report_service,
        reports_history_service=reports_history_service,
        report_deletion_service=report_deletion_service,
        heatmap_tile_service=heatmap_tile_service,
        heatmap_stats_service=heatmap_stats_service,
        public_buildings_service=public_buildings_service,
        public_reports_service=public_reports_service,
        osm_area_searcher=osm_nominatim_client,
        osm_nominatim_client=osm_nominatim_client,
        jwt_verifier=jwt_verifier,
        supabase_auth_client=supabase_auth_client_concrete,
        supabase_auth_client_concrete_=supabase_auth_client_concrete,
        refresh_store=refresh_store,
        redis_client=redis_client,
        arq_pool=arq_pool,
        storage=storage,
        ai_clients=ai_clients,
        chat_sessions=SessionStore(),
        crisis_detail_cache=LRUCache(512),
        map_query_vectors=LRUCache(256),
        map_concurrency=asyncio.Semaphore(8),
    )


_lifespan_logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None]:
    settings = get_settings()
    state = build_app_state(settings)
    app.state.container = state

    if settings.uses_demo_keys():
        _lifespan_logger.warning(
            "Supabase demo keys in use; run infra/gen_secrets.py before going live"
        )

    # Admin bootstrap failure must not block startup.
    try:
        await ensure_env_var_admin(
            settings=settings,
            supabase_client=state.supabase_auth_client,
            redis_client=state.redis_client,
            refresh_store=state.refresh_store,
        )
    except Exception:
        _lifespan_logger.exception("env-var admin bootstrap raised unexpectedly")

    try:
        yield
    finally:
        await state.osm_nominatim_client.aclose()
        if state.supabase_auth_client_concrete_ is not None:
            await state.supabase_auth_client_concrete_.aclose()
        if state.redis_client is not None:
            await state.redis_client.aclose()
        await state.arq_pool.aclose()
        await aclose_ai_clients(state.ai_clients)
        await state.engine.dispose()


def get_app_state(request: Request) -> AppState:
    container = getattr(request.app.state, "container", None)
    if not isinstance(container, AppState):
        raise RuntimeError(
            "AppState is not initialised; lifespan did not run. "
            "Wrap TestClient usage in `with TestClient(app) as client:`."
        )
    return container


def get_storage(state: Annotated[AppState, Depends(get_app_state)]) -> StorageClient:
    return state.storage
