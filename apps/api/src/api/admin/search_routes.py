"""Coordinator report search, summary (SSE) and chat over a filtered view."""

from __future__ import annotations

import json
import logging
import secrets
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from sqlalchemy import text

from api.admin.guards import require_crisis
from api.admin.report_queries import ensure_crisis_exists
from api.admin.search_aggregates import compute_search_aggregates
from api.admin.search_filters import filter_signature, preset_for
from api.admin.search_queries import (
    fetch_hits_by_ids,
    run_search,
    sample_report_ids,
    seed_from_signature,
)
from api.ai import AIClientUnavailableError, embed_query, halfvec
from api.ai.chat import (
    DEFAULT_K,
    SAMPLE_TOTAL_CEILING,
    ChatSession,
    answer_turn,
    centroid_top_k,
    is_generic_first_message,
    render_filter_block,
    render_stats_block,
    select_top_k,
)
from api.ai.summarization import summarise
from api.core.app_state import AppState, get_app_state
from api.schemas.admin_search import (
    ChatContextReport,
    ChatOpenRequest,
    ChatTurnRequest,
    ChatTurnResponse,
    SearchHit,
    SearchRequest,
    SearchResponse,
    SearchStats,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/reports/search")


@router.post("/{crisis_id}", response_model=SearchResponse)
async def search_reports(
    crisis_id: uuid.UUID,
    payload: SearchRequest,
    state: Annotated[AppState, Depends(get_app_state)],
) -> SearchResponse:
    async with state.sessionmaker() as session:
        await require_crisis(session, crisis_id)

        query_vector: list[float] | None = None
        floor, _ = preset_for(payload.strictness)
        if payload.query and payload.query.strip():
            try:
                embedding = await embed_query(
                    payload.query.strip(), client=state.ai_clients.embedding
                )
                query_vector = embedding.vector
            except AIClientUnavailableError as exc:
                # Fall back to structured-only search.
                logger.warning("search.embedding_unavailable: %s", exc)
                query_vector = None

        rows = await run_search(
            session,
            crisis_id=crisis_id,
            request=payload,
            query_vector=query_vector,
        )
        # Aggregates cover the full filtered set, so stats.total is the exact
        # match count even when rows is truncated.
        stats = await compute_search_aggregates(
            session,
            crisis_id=crisis_id,
            request=payload,
            query_vector=query_vector,
        )

    sig = filter_signature(crisis_id, payload)
    total = stats.total
    truncated = total > len(rows)
    return SearchResponse(
        rows=rows,
        truncated=truncated,
        total_match_count=total,
        similarity_floor=floor if query_vector is not None else None,
        filter_signature=sig,
        stats=stats,
    )


@router.post("/{crisis_id}/summary")
async def search_summary(
    crisis_id: uuid.UUID,
    payload: SearchRequest,
    state: Annotated[AppState, Depends(get_app_state)],
) -> StreamingResponse:
    return StreamingResponse(
        _summary_stream(crisis_id, payload, state),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# Max reports the summary reads, independent of SearchRequest.limit. Larger
# sets keep the top by similarity (with a query) or a seeded random sample.
_SUMMARY_SAMPLE_SIZE = 5000


async def _summary_stream(
    crisis_id: uuid.UUID,
    payload: SearchRequest,
    state: AppState,
) -> AsyncIterator[bytes]:
    enriched, embeddings_by_id, total, stats, error = await _fetch_summary_set(
        crisis_id, payload, state
    )
    if error is not None:
        yield _sse(error)
        return
    if not enriched:
        yield _sse({"kind": "prose", "text": "No reports in the current view."})
        return

    assert stats is not None  # set whenever error is None
    digest = _summary_stats_digest(stats, total)
    async for frame in summarise(
        enriched, embeddings_by_id, clients=state.ai_clients, total=total, stats_digest=digest
    ):
        yield _sse(frame)


def _sse(frame: dict[str, Any]) -> bytes:
    return f"data: {json.dumps(frame)}\n\n".encode()


async def _fetch_summary_set(
    crisis_id: uuid.UUID,
    payload: SearchRequest,
    state: AppState,
) -> tuple[
    list[dict[str, Any]],
    dict[uuid.UUID, list[float]],
    int,
    SearchStats | None,
    dict[str, Any] | None,
]:
    """Return (hits, embeddings_by_id, total, stats, error).

    total and stats cover the full filtered set; hits are capped at
    _SUMMARY_SAMPLE_SIZE. The random sample is seeded by the filter signature.
    """
    async with state.sessionmaker() as session:
        if not await ensure_crisis_exists(session, crisis_id):
            return [], {}, 0, None, {"kind": "error", "message": "crisis not found"}

        query_vector: list[float] | None = None
        if payload.query and payload.query.strip():
            try:
                embedding = await embed_query(
                    payload.query.strip(), client=state.ai_clients.embedding
                )
                query_vector = embedding.vector
            except AIClientUnavailableError:
                query_vector = None

        stats = await compute_search_aggregates(
            session, crisis_id=crisis_id, request=payload, query_vector=query_vector
        )
        total = stats.total
        if total == 0:
            return [], {}, 0, stats, None

        if query_vector is not None or total <= _SUMMARY_SAMPLE_SIZE:
            # Top-K by similarity, or the whole set when it fits.
            scoped = payload.model_copy(update={"limit": _SUMMARY_SAMPLE_SIZE})
            rows = await run_search(
                session, crisis_id=crisis_id, request=scoped, query_vector=query_vector
            )
            ids = [r.id for r in rows]
        else:
            seed = seed_from_signature(filter_signature(crisis_id, payload))
            ids = await sample_report_ids(
                session,
                crisis_id=crisis_id,
                request=payload,
                query_vector=None,
                sample_size=_SUMMARY_SAMPLE_SIZE,
                seed=seed,
            )

        embeddings_by_id = await _fetch_embeddings_by_ids(session, ids)
        hits_by_id = await fetch_hits_by_ids(session, ids)
        enriched = [hits_by_id[i] for i in ids if i in hits_by_id]
        return enriched, embeddings_by_id, total, stats, None


def _summary_stats_digest(stats: SearchStats, total: int) -> dict[str, Any]:
    """Full-set figures for the synthesis to cite; themes run over a sample."""
    digest: dict[str, Any] = {
        "total_matched": total,
        "damage_mix": stats.severity,
        "reports_last_24h": stats.last_24h,
        "reports_last_hour": stats.last_hour,
        "buildings_affected": stats.buildings_affected,
        "reports_with_gps": stats.with_gps,
        "reports_unmapped": stats.unmapped,
        "unique_devices": stats.unique_devices,
    }
    if stats.top_infra is not None:
        digest["top_infrastructure"] = {"type": stats.top_infra, "reports": stats.top_infra_count}
    if stats.infra_breakdown:
        digest["infrastructure_breakdown"] = stats.infra_breakdown
    return digest


# Chat


@router.post("/{crisis_id}/chat/open", response_model=ChatTurnResponse)
async def chat_open(
    crisis_id: uuid.UUID,
    payload: ChatOpenRequest,
    state: Annotated[AppState, Depends(get_app_state)],
) -> ChatTurnResponse:
    """Open a session and lock the K reports used for every later turn."""
    rows, embeddings_by_id, hits_by_id, total, error, stats = await _fetch_filtered_set(
        crisis_id, payload.filter, state, with_stats=True
    )
    if error is not None:
        raise HTTPException(status_code=503, detail=error.get("message", "search failed"))

    if not rows:
        # Empty session that always abstains.
        sig = filter_signature(crisis_id, payload.filter)
        session_id = secrets.token_urlsafe(16)
        session = ChatSession(
            session_id=session_id,
            crisis_id=crisis_id,
            filter_signature=sig,
            k_ids=[],
            opened_total=0,
        )
        await state.chat_sessions.put(session)
        return ChatTurnResponse(
            session_id=session_id,
            text="No reports match the current filter; widen it to chat.",
            cited_report_ids=[],
            k_size=0,
            filter_signature=sig,
            opened_total=0,
            answered_at=datetime.now(UTC),
        )

    hit_ids = [r.id for r in rows]
    if total > SAMPLE_TOTAL_CEILING:
        # A small sample cannot represent a set this large; answer from the
        # aggregates alone, with no citations.
        k_ids: list[uuid.UUID] = []
    elif is_generic_first_message(payload.message):
        k_ids = centroid_top_k(hit_ids, embeddings_by_id, k=DEFAULT_K)
    else:
        try:
            embedding = await embed_query(
                payload.message.strip(), client=state.ai_clients.embedding
            )
            k_ids = select_top_k(hit_ids, embeddings_by_id, embedding.vector, k=DEFAULT_K)
        except AIClientUnavailableError:
            k_ids = centroid_top_k(hit_ids, embeddings_by_id, k=DEFAULT_K)

    sig = filter_signature(crisis_id, payload.filter)
    session_id = secrets.token_urlsafe(16)
    # Guaranteed by with_stats=True and non-empty rows; narrows the type.
    if stats is None:
        raise HTTPException(status_code=500, detail="stats computation failed")
    stats_block = render_stats_block(stats, total)
    filter_block = render_filter_block(payload.filter)
    session = ChatSession(
        session_id=session_id,
        crisis_id=crisis_id,
        filter_signature=sig,
        k_ids=k_ids,
        stats_block=stats_block,
        filter_block=filter_block,
        opened_total=total,
    )
    await state.chat_sessions.put(session)

    k_hits = [hits_by_id[i] for i in k_ids if i in hits_by_id]
    result = await answer_turn(session, payload.message, k_hits, clients=state.ai_clients)
    await state.chat_sessions.put(session)  # persist updated messages list
    return ChatTurnResponse(
        session_id=session_id,
        text=result.text,
        cited_report_ids=result.cited_report_ids,
        k_size=len(k_ids),
        filter_signature=sig,
        opened_total=total,
        answered_at=datetime.now(UTC),
        stats=stats,
        context_reports=_context_reports(k_hits),
    )


def _context_reports(k_hits: list[dict[str, Any]]) -> list[ChatContextReport]:
    """Pin, else building centroid; geocode-only hits are dropped."""
    out: list[ChatContextReport] = []
    for h in k_hits:
        lat = h.get("report_lat")
        lng = h.get("report_lng")
        if lat is None or lng is None:
            lat = h.get("building_lat")
            lng = h.get("building_lng")
        if lat is None or lng is None:
            continue
        out.append(
            ChatContextReport(
                id=h["id"],
                lat=float(lat),
                lng=float(lng),
                damage_class=h.get("damage_class"),
            )
        )
    return out


@router.post("/{crisis_id}/chat/turn", response_model=ChatTurnResponse)
async def chat_turn(
    crisis_id: uuid.UUID,
    payload: ChatTurnRequest,
    state: Annotated[AppState, Depends(get_app_state)],
) -> ChatTurnResponse:
    session = await state.chat_sessions.get(payload.session_id)
    if session is None or session.crisis_id != crisis_id:
        raise HTTPException(status_code=404, detail="chat session not found")

    async with state.sessionmaker() as db_session:
        hits_by_id = await fetch_hits_by_ids(db_session, session.k_ids)
    k_hits = [hits_by_id[i] for i in session.k_ids if i in hits_by_id]

    result = await answer_turn(session, payload.message, k_hits, clients=state.ai_clients)
    await state.chat_sessions.put(session)
    return ChatTurnResponse(
        session_id=session.session_id,
        text=result.text,
        cited_report_ids=result.cited_report_ids,
        opened_total=session.opened_total,
        answered_at=datetime.now(UTC),
        filter_signature=session.filter_signature,
    )


# Shared by chat open


_FilteredSet = tuple[
    list[SearchHit],
    dict[uuid.UUID, list[float]],
    dict[uuid.UUID, dict[str, Any]],
    int,
    dict[str, Any] | None,
    SearchStats | None,
]


async def _fetch_filtered_set(
    crisis_id: uuid.UUID,
    payload: SearchRequest,
    state: AppState,
    *,
    with_stats: bool = False,
) -> _FilteredSet:
    """Return (rows, embeddings_by_id, hits_by_id, total, error, stats).

    total is exact only when with_stats is set; rows are capped at request.limit.
    """
    async with state.sessionmaker() as session:
        if not await ensure_crisis_exists(session, crisis_id):
            return [], {}, {}, 0, {"kind": "error", "message": "crisis not found"}, None

        query_vector: list[float] | None = None
        if payload.query and payload.query.strip():
            try:
                embedding = await embed_query(
                    payload.query.strip(), client=state.ai_clients.embedding
                )
                query_vector = embedding.vector
            except AIClientUnavailableError:
                query_vector = None

        rows = await run_search(
            session,
            crisis_id=crisis_id,
            request=payload,
            query_vector=query_vector,
        )
        if not rows:
            return [], {}, {}, 0, None, None

        stats = (
            await compute_search_aggregates(
                session, crisis_id=crisis_id, request=payload, query_vector=query_vector
            )
            if with_stats
            else None
        )
        total = stats.total if stats is not None else len(rows)

        ids = [r.id for r in rows]
        embeddings_by_id = await _fetch_embeddings_by_ids(session, ids)
        hits_by_id = await fetch_hits_by_ids(session, ids)
        return rows, embeddings_by_id, hits_by_id, total, None, stats


_EMBEDDING_FETCH_SQL = text(
    """
    select report_id, embedding::text as v
    from public.report_embeddings
    where report_id = any(:ids)
    """
)


async def _fetch_embeddings_by_ids(
    session: Any, ids: list[uuid.UUID]
) -> dict[uuid.UUID, list[float]]:
    """Embeddings as list[float], parsed from pgvector's halfvec text literal."""
    if not ids:
        return {}
    result = await session.execute(_EMBEDDING_FETCH_SQL, {"ids": [str(i) for i in ids]})
    out: dict[uuid.UUID, list[float]] = {}
    for row in result.all():
        parsed = halfvec.parse(row.v)
        if parsed:
            out[row.report_id] = parsed
    return out
