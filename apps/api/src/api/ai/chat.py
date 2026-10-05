"""Coordinator chat over a filtered report set.

Sessions are in-memory only. The top-K report sample is chosen at session open
and stays fixed across turns; citations outside it are stripped.
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

import numpy as np

from api.ai import prompts
from api.ai.client import AIClients, AIClientUnavailableError
from api.ai.llm import complete_chat
from api.ai.report_text import report_detail_lines
from api.ai.vectors import l2_normalize
from api.schemas.admin_search import SearchRequest, SearchStats

logger = logging.getLogger(__name__)

# Breadth questions are answered from the stats block; K only grounds
# narrative and citations.
DEFAULT_K = 15

# Above this many matches the sample is too thin to cite, so the route locks
# k_ids=[] and the chat answers from the aggregates only.
SAMPLE_TOTAL_CEILING = 100000

# Idle TTL so abandoned sessions don't accumulate in memory.
SESSION_TTL_SECONDS = 60 * 60

# Older turns are dropped first.
MAX_HISTORY_TURNS = 20

_MAX_OUTPUT_TOKENS = 1024


@dataclass
class ChatSession:
    """The K-set is fixed at open; re-ranking needs a new session."""

    session_id: str
    crisis_id: uuid.UUID
    filter_signature: str
    k_ids: list[uuid.UUID]
    # Dashboard stats at open, injected into the system prompt every turn.
    stats_block: str = ""
    # Filters active at open, injected into the system prompt every turn.
    filter_block: str = ""
    # Lets the UI flag when the current filter no longer matches the session's scope.
    opened_total: int = 0
    # Excludes the system prompt, which is rebuilt per request.
    messages: list[dict[str, Any]] = field(default_factory=lambda: [])
    last_touched: float = field(default_factory=time.time)


class SessionStore:
    """Process-local session store; sessions are lost across replicas or restarts."""

    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._sessions: dict[str, ChatSession] = {}

    async def put(self, session: ChatSession) -> None:
        session.last_touched = time.time()
        async with self._lock:
            self._gc_locked()
            self._sessions[session.session_id] = session

    async def get(self, session_id: str) -> ChatSession | None:
        async with self._lock:
            self._gc_locked()
            session = self._sessions.get(session_id)
        if session is not None:
            session.last_touched = time.time()
        return session

    def _gc_locked(self) -> None:
        cutoff = time.time() - SESSION_TTL_SECONDS
        stale = [sid for sid, s in self._sessions.items() if s.last_touched < cutoff]
        for sid in stale:
            self._sessions.pop(sid, None)


def select_top_k(
    hit_ids: list[uuid.UUID],
    embeddings_by_id: dict[uuid.UUID, list[float]],
    query_vector: list[float],
    *,
    k: int = DEFAULT_K,
) -> list[uuid.UUID]:
    """Rank by cosine similarity; hits not yet embedded sort last."""
    return _rank_against_vector(hit_ids, embeddings_by_id, query_vector, k=k)


def centroid_top_k(
    hit_ids: list[uuid.UUID],
    embeddings_by_id: dict[uuid.UUID, list[float]],
    *,
    k: int = DEFAULT_K,
) -> list[uuid.UUID]:
    """For a vague first message: the K hits closest to the centroid, a representative sample."""
    vectors = [embeddings_by_id[rid] for rid in hit_ids if rid in embeddings_by_id]
    if not vectors:
        return hit_ids[:k]
    centroid = np.mean(np.asarray(vectors, dtype=np.float32), axis=0).tolist()
    return _rank_against_vector(hit_ids, embeddings_by_id, centroid, k=k)


def _rank_against_vector(
    hit_ids: list[uuid.UUID],
    embeddings_by_id: dict[uuid.UUID, list[float]],
    target: list[float],
    *,
    k: int,
) -> list[uuid.UUID]:
    with_vec: list[tuple[uuid.UUID, list[float] | None]] = [
        (rid, embeddings_by_id.get(rid)) for rid in hit_ids
    ]
    vectors = [v for _, v in with_vec if v is not None]
    if not vectors:
        return hit_ids[:k]
    matrix = l2_normalize(np.asarray(vectors, dtype=np.float32))
    target_vec = l2_normalize(np.asarray(target, dtype=np.float32)[None, :])[0]
    sims = matrix @ target_vec  # shape (n,)
    distances = 1.0 - sims
    distances_iter = iter(distances.tolist())
    scored: list[tuple[float, uuid.UUID]] = []
    for rid, vec in with_vec:
        if vec is None:
            scored.append((1.0, rid))
        else:
            scored.append((float(next(distances_iter)), rid))
    scored.sort(key=lambda pair: pair[0])
    return [rid for _, rid in scored[:k]]


# Heuristic: "tell me about this", "what's going on", "summary", etc.
_GENERIC_FIRST_MESSAGE = re.compile(
    r"^\s*(tell me|summary|summarize|summarise|what'?s? (going on|happening)|"
    r"overview|brief me|describe|whats up|status)\b",
    re.IGNORECASE,
)


def is_generic_first_message(text: str) -> bool:
    text = text.strip()
    if len(text) < 12:
        return True
    return _GENERIC_FIRST_MESSAGE.search(text) is not None


@dataclass(frozen=True, slots=True)
class ChatTurnResult:
    text: str
    cited_report_ids: list[uuid.UUID]


_REPORT_ID_CITATION = re.compile(r"\[report_id=([0-9a-fA-F-]{36})\]")


async def answer_turn(
    session: ChatSession,
    user_message: str,
    k_hits: list[dict[str, Any]],
    *,
    clients: AIClients,
) -> ChatTurnResult:
    """`cited_report_ids` holds only valid citations, in first-mention order."""
    try:
        cfg = clients.require_text()
    except AIClientUnavailableError as exc:
        return ChatTurnResult(text=str(exc), cited_report_ids=[])

    session.messages.append({"role": "user", "content": user_message})
    _trim_history(session)

    # One system message: some providers reject consecutive system entries.
    sections: list[str] = [prompts.CHAT_SYSTEM]
    if session.filter_block:
        sections.append(f"Active filters:\n\n{session.filter_block}")
    if session.stats_block:
        sections.append(f"Scope at session open:\n\n{session.stats_block}")
    # Empty when nothing matched or the set exceeded SAMPLE_TOTAL_CEILING.
    if k_hits:
        context_block = _format_context(k_hits)
        sections.append(f"Reports in scope (sample of {len(k_hits)} reports):\n\n{context_block}")
    messages = [
        {"role": "system", "content": "\n\n".join(sections)},
        *session.messages,
    ]

    try:
        raw = await complete_chat(cfg, messages, max_tokens=_MAX_OUTPUT_TOKENS, temperature=0.2)
    except Exception as exc:
        logger.warning("chat.answer_turn.error: %s", exc)
        # Drop the unanswered user turn; a user/user sequence is rejected by
        # strict-alternation providers.
        if session.messages and session.messages[-1].get("role") == "user":
            session.messages.pop()
        return ChatTurnResult(text="The model call failed; try again.", cited_report_ids=[])

    valid_ids = set(session.k_ids)
    cleaned, cited = _validate_citations(raw, valid_ids)
    session.messages.append({"role": "assistant", "content": cleaned})
    _trim_history(session)
    return ChatTurnResult(text=cleaned, cited_report_ids=cited)


def _validate_citations(text: str, valid_ids: set[uuid.UUID]) -> tuple[str, list[uuid.UUID]]:
    """Strip citations outside the K set; return valid ids in first-mention order."""
    cited: list[uuid.UUID] = []
    cited_seen: set[uuid.UUID] = set()

    def replace(match: re.Match[str]) -> str:
        try:
            rid = uuid.UUID(match.group(1))
        except ValueError:
            return ""
        if rid not in valid_ids:
            return ""
        if rid not in cited_seen:
            cited_seen.add(rid)
            cited.append(rid)
        return match.group(0)

    cleaned = _REPORT_ID_CITATION.sub(replace, text).strip()
    if not cleaned:
        return "Insufficient information in the current view.", []
    return cleaned, cited


def _trim_history(session: ChatSession) -> None:
    if len(session.messages) > MAX_HISTORY_TURNS * 2:
        # Each turn is a user + assistant pair.
        session.messages = session.messages[-MAX_HISTORY_TURNS * 2 :]


def _format_context(hits: list[dict[str, Any]]) -> str:
    blocks: list[str] = []
    for h in hits:
        blocks.append(_format_one(h))
    return "\n\n".join(blocks)


def _format_one(hit: dict[str, Any]) -> str:
    lines = report_detail_lines(hit, caption_format="(photo: {})")
    return "\n".join([f"[report_id={hit['id']}]", *lines])


__all__ = [
    "DEFAULT_K",
    "SAMPLE_TOTAL_CEILING",
    "ChatSession",
    "ChatTurnResult",
    "SessionStore",
    "answer_turn",
    "centroid_top_k",
    "is_generic_first_message",
    "select_top_k",
]


# Prompt blocks describing the dashboard scope


def render_stats_block(stats: SearchStats, total_match_count: int) -> str:
    """Chat prompt block with the same numbers the dashboard shows, so answers can quote them."""
    lines: list[str] = []
    if total_match_count > stats.total:
        lines.append(
            f"Total reports matching filter: {total_match_count} "
            f"(visible sample below covers the first {stats.total})"
        )
    else:
        lines.append(f"Total reports: {stats.total}")
    sev = stats.severity
    lines.append(
        "Severity histogram — "
        f"complete: {sev.get('complete', 0)}, "
        f"partial: {sev.get('partial', 0)}, "
        f"minimal: {sev.get('minimal', 0)}"
    )
    lines.append(f"Activity — last 24h: {stats.last_24h}, last hour: {stats.last_hour}")
    if stats.top_infra is not None:
        lines.append(f"Top infrastructure: {stats.top_infra} ({stats.top_infra_count} reports)")
    else:
        lines.append("Top infrastructure: none tagged")
    debris_denom = stats.debris_known or stats.total
    lines.append(
        "Data quality — "
        f"debris reported: {stats.debris_yes}/{debris_denom}, "
        f"building matched: {stats.with_building}/{stats.total}, "
        f"GPS fix: {stats.with_gps}/{stats.total}"
    )
    return "\n".join(lines)


def render_filter_block(request: SearchRequest) -> str:
    """Chat prompt block listing the active filters; limit is not a filter and is omitted."""
    lines: list[str] = []
    if request.query and request.query.strip():
        lines.append(
            f'Semantic query: "{request.query.strip()}" (strictness: {request.strictness})'
        )
    tw = request.time_window
    if tw is not None and (tw.from_ is not None or tw.to is not None):
        start = tw.from_.isoformat() if tw.from_ is not None else "any"
        end = tw.to.isoformat() if tw.to is not None else "now"
        lines.append(f"Time window: {start} to {end}")
    if request.damage_class != "any":
        lines.append(f"Damage class: {request.damage_class}")
    if request.infra_types:
        lines.append(f"Infrastructure types: {', '.join(request.infra_types)}")
    if request.debris != "any":
        lines.append(f"Debris present: {request.debris}")
    if request.location_kind != "any":
        lines.append(f"Location precision: {request.location_kind}")
    if request.building_id is not None:
        lines.append(f"Single building: {request.building_id}")
    loc = request.location
    if loc is not None and (loc.division_ids or loc.polygon or loc.bbox):
        where: list[str] = []
        if loc.division_ids:
            where.append(f"{len(loc.division_ids)} administrative division(s)")
        if loc.polygon:
            where.append("a drawn polygon")
        if loc.bbox:
            where.append("the current map viewport")
        lines.append(f"Location: limited to {', '.join(where)}")
    if not lines:
        return "No filters applied — showing all reports for this crisis."
    return "\n".join(lines)
