"""Integration tests for the enrichment Arq jobs.

Each test runs against the local Supabase Postgres but wraps its work in
an outer transaction with `join_transaction_mode="create_savepoint"`. The
job's internal sessions commit inside savepoints; we roll the outer
transaction back at teardown so nothing leaks.

The AI primitives and the photo downloader are stubbed via in-process
fakes — these tests pin DB plumbing, dispatcher branching, and the
atomic finalize, not the LLM contract (the parser unit tests cover
that). Two ctx keys are mocked: the AI client bundle (via objects whose
`require_text` return a stub `chat.completions.create`)
and the Arq pool (a list-recorder), so we can assert what was enqueued.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncGenerator, AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncConnection,
    AsyncEngine,
    async_sessionmaker,
    create_async_engine,
)

from api.ai.types import CaptionResult, RelevanceResult, TranslationResult
from api.core.config import get_settings
from api.workers import enrichment

pytestmark = pytest.mark.integration


# ---------------------------------------------------------------------------
# Stubs
# ---------------------------------------------------------------------------


_EnqueueCall = tuple[str, tuple[Any, ...], dict[str, Any]]


@dataclass
class FakeArqPool:
    """Records `enqueue_job` calls — stands in for `ctx['redis']`."""

    calls: list[_EnqueueCall] = field(default_factory=list[_EnqueueCall])

    async def enqueue_job(self, name: str, *args: Any, **kwargs: Any) -> object:
        self.calls.append((name, args, kwargs))
        return object()  # arq returns a Job; we don't read it.


class FakePhotoDownloader:
    """Stand-in for the Supabase photo downloader stashed on ctx."""

    def __init__(
        self,
        content: bytes = b"\xff\xd8\xff\xd9",
        content_type: str = "image/jpeg",
    ) -> None:
        self._content = content
        self._content_type = content_type

    async def download_photo(self, photo_path: str) -> tuple[bytes, str]:
        _ = photo_path
        return self._content, self._content_type


def _stub_clients() -> object:
    # Imported here to avoid coupling the module-level import to the AI SDK.
    from api.ai.client import AIClients

    return AIClients(text=None, embedding=None)


# ---------------------------------------------------------------------------
# DB fixtures
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def engine() -> AsyncIterator[AsyncEngine]:
    eng = create_async_engine(get_settings().database_url)
    try:
        yield eng
    finally:
        await eng.dispose()


@asynccontextmanager
async def _seeded_crisis(conn: AsyncConnection) -> AsyncGenerator[uuid.UUID]:
    crisis_id = uuid.uuid4()
    await conn.execute(
        text(
            "insert into public.crises (id, name, status, created_at) "
            "values (:id, :n, 'active', :t)"
        ),
        {
            "id": str(crisis_id),
            "n": f"Enrichment test crisis {uuid.uuid4().hex[:8]}",
            "t": datetime.now(UTC) - timedelta(minutes=1),
        },
    )
    yield crisis_id


async def _seed_report(
    conn: AsyncConnection,
    crisis_id: uuid.UUID,
    *,
    description: str | None = None,
    route_description: str | None = None,
    with_location: bool = True,
    with_photo: bool = True,
) -> uuid.UUID:
    """Insert one report row + the three sidecar rows in `pending` state.

    `with_location=True` (the default) stamps a GPS point so the row satisfies
    the `reports_location_or_route` invariant; pass `with_location=False`
    together with a `route_description` to exercise the no-GPS geocode path.

    `with_photo=False` inserts a description-only report (photo_path NULL) —
    the dispatcher should then mark the caption sidecar `skipped`. Pair it with
    a `description` so the `reports_photo_or_description` CHECK holds.
    """
    report_id = uuid.uuid4()
    location_wkt = "SRID=4326;POINT(36.16 36.20)" if with_location else None
    photo_path = "aa/bb/aabb.jpg" if with_photo else None
    await conn.execute(
        text(
            "insert into public.reports "
            "  (id, crisis_id, damage_class, description, route_description, photo_path, location) "
            "values (:id, :cid, 'minimal', :desc, :route, :path, cast(:loc as geography))"
        ),
        {
            "id": str(report_id),
            "cid": str(crisis_id),
            "desc": description,
            "route": route_description,
            "path": photo_path,
            "loc": location_wkt,
        },
    )
    await conn.execute(
        text("insert into public.report_translations (report_id) values (:id)"),
        {"id": str(report_id)},
    )
    await conn.execute(
        text("insert into public.image_captions (report_id) values (:id)"),
        {"id": str(report_id)},
    )
    await conn.execute(
        text("insert into public.report_geocodes (report_id) values (:id)"),
        {"id": str(report_id)},
    )
    return report_id


@dataclass
class _Harness:
    sessionmaker: async_sessionmaker[Any]
    conn: AsyncConnection
    crisis_id: uuid.UUID
    pool: FakeArqPool

    def make_ctx(
        self,
        *,
        downloader: FakePhotoDownloader | None = None,
        job_try: int = 1,
        max_tries: int = 1,
    ) -> dict[str, object]:
        return {
            "redis": self.pool,
            "db_sessionmaker": self.sessionmaker,
            "ai_clients": _stub_clients(),
            "storage_client": downloader or FakePhotoDownloader(),
            "job_try": job_try,
            "max_tries": max_tries,
        }


@pytest_asyncio.fixture
async def harness(engine: AsyncEngine) -> AsyncIterator[_Harness]:
    async with engine.connect() as conn:
        outer = await conn.begin()
        try:
            sessionmaker = async_sessionmaker(
                bind=conn,
                expire_on_commit=False,
                join_transaction_mode="create_savepoint",
            )
            async with _seeded_crisis(conn) as crisis_id:
                yield _Harness(
                    sessionmaker=sessionmaker,
                    conn=conn,
                    crisis_id=crisis_id,
                    pool=FakeArqPool(),
                )
        finally:
            await outer.rollback()


# ---------------------------------------------------------------------------
# Dispatcher
# ---------------------------------------------------------------------------


async def test_dispatcher_skips_empty_description_and_route(
    harness: _Harness,
) -> None:
    """A report with no text fields finalises after dispatch if the photo
    sub-job also resolves — but the dispatcher itself only needs to mark
    both fields skipped and enqueue caption_image."""
    report_id = await _seed_report(harness.conn, harness.crisis_id)

    await enrichment.enrich_report(harness.make_ctx(), str(report_id))

    row = (
        await harness.conn.execute(
            text(
                "select description_status, route_description_status "
                "from public.report_translations where report_id = :id"
            ),
            {"id": str(report_id)},
        )
    ).one()
    assert row.description_status == "skipped"
    assert row.route_description_status == "skipped"

    # Caption was the only pending stage — it must have been enqueued.
    job_names = [c[0] for c in harness.pool.calls]
    assert "caption_image" in job_names
    assert "translate_field" not in job_names


async def test_dispatcher_enqueues_translate_for_non_empty_fields(
    harness: _Harness,
) -> None:
    report_id = await _seed_report(
        harness.conn,
        harness.crisis_id,
        description="The wall is cracked",
        route_description="Take the side road",
    )

    await enrichment.enrich_report(harness.make_ctx(), str(report_id))

    fields_enqueued = sorted(c[1][1] for c in harness.pool.calls if c[0] == "translate_field")
    assert fields_enqueued == ["description", "route_description"]
    # Plus the caption sub-job.
    assert any(c[0] == "caption_image" for c in harness.pool.calls)


# ---------------------------------------------------------------------------
# Dispatcher — geocoding fan-out
# ---------------------------------------------------------------------------


async def test_dispatcher_enqueues_geocode_for_route_text_without_gps(
    harness: _Harness,
) -> None:
    """A report with a route description and no GPS/building → geocode_report
    is enqueued and the sidecar stays `pending`."""
    report_id = await _seed_report(
        harness.conn,
        harness.crisis_id,
        route_description="behind the Yeni Cami in Antakya",
        with_location=False,  # the route text is what satisfies the gate here
    )

    await enrichment.enrich_report(harness.make_ctx(), str(report_id))

    geocode_calls = [c for c in harness.pool.calls if c[0] == "geocode_report"]
    assert len(geocode_calls) == 1
    assert geocode_calls[0][1] == (str(report_id),)

    status = (
        await harness.conn.execute(
            text("select status from public.report_geocodes where report_id = :id"),
            {"id": str(report_id)},
        )
    ).scalar_one()
    assert status == "pending"


async def test_dispatcher_skips_geocode_when_report_has_gps(
    harness: _Harness,
) -> None:
    """A report with a GPS point needs no geocoding — the sidecar is marked
    `skipped` in place and no geocode_report job is enqueued."""
    report_id = await _seed_report(
        harness.conn,
        harness.crisis_id,
        route_description="behind the Yeni Cami in Antakya",
        with_location=True,
    )

    await enrichment.enrich_report(harness.make_ctx(), str(report_id))

    assert not [c for c in harness.pool.calls if c[0] == "geocode_report"]
    status = (
        await harness.conn.execute(
            text("select status from public.report_geocodes where report_id = :id"),
            {"id": str(report_id)},
        )
    ).scalar_one()
    assert status == "skipped"


async def test_dispatcher_skips_geocode_when_no_route_text(
    harness: _Harness,
) -> None:
    """GPS present and no route text → nothing to geocode; mark `skipped`.

    Post the minimum-content gate, a report with no route text necessarily
    carries a GPS fix (location OR route is required), so this is the
    canonical no-geocode case.
    """
    report_id = await _seed_report(harness.conn, harness.crisis_id)

    await enrichment.enrich_report(harness.make_ctx(), str(report_id))

    assert not [c for c in harness.pool.calls if c[0] == "geocode_report"]
    status = (
        await harness.conn.execute(
            text("select status from public.report_geocodes where report_id = :id"),
            {"id": str(report_id)},
        )
    ).scalar_one()
    assert status == "skipped"


async def test_dispatcher_skips_caption_when_no_photo(
    harness: _Harness,
) -> None:
    """A description-only report (no photo) has no image to caption: the
    dispatcher marks the caption sidecar `skipped` and enqueues no
    caption_image job, so the report can still finalize."""
    report_id = await _seed_report(
        harness.conn,
        harness.crisis_id,
        description="A written account of the collapse, no photo possible",
        with_photo=False,
    )

    await enrichment.enrich_report(harness.make_ctx(), str(report_id))

    assert not [c for c in harness.pool.calls if c[0] == "caption_image"]
    status = (
        await harness.conn.execute(
            text("select status from public.image_captions where report_id = :id"),
            {"id": str(report_id)},
        )
    ).scalar_one()
    assert status == "skipped"


# ---------------------------------------------------------------------------
# translate_field
# ---------------------------------------------------------------------------


async def test_translate_field_english_writes_ready_with_raw_text(
    harness: _Harness, monkeypatch: pytest.MonkeyPatch
) -> None:
    report_id = await _seed_report(
        harness.conn, harness.crisis_id, description="The roof has collapsed."
    )

    async def fake_translate(_text: str, *, clients: Any) -> TranslationResult:
        _ = clients
        return TranslationResult(lang="en", text_en="")

    monkeypatch.setattr(enrichment, "detect_and_translate", fake_translate)

    await enrichment.translate_field(harness.make_ctx(), str(report_id), "description")

    row = (
        await harness.conn.execute(
            text(
                "select description_lang, description_en, description_status "
                "from public.report_translations where report_id = :id"
            ),
            {"id": str(report_id)},
        )
    ).one()
    assert row.description_lang == "en"
    assert row.description_en == "The roof has collapsed."
    assert row.description_status == "ready"


async def test_translate_field_arabic_writes_translation(
    harness: _Harness, monkeypatch: pytest.MonkeyPatch
) -> None:
    report_id = await _seed_report(harness.conn, harness.crisis_id, description="الجدار متشقق")

    async def fake_translate(_text: str, *, clients: Any) -> TranslationResult:
        _ = clients
        return TranslationResult(lang="ar", text_en="The wall is cracked")

    monkeypatch.setattr(enrichment, "detect_and_translate", fake_translate)

    await enrichment.translate_field(harness.make_ctx(), str(report_id), "description")

    row = (
        await harness.conn.execute(
            text(
                "select description_lang, description_en, description_status "
                "from public.report_translations where report_id = :id"
            ),
            {"id": str(report_id)},
        )
    ).one()
    assert row.description_lang == "ar"
    assert row.description_en == "The wall is cracked"
    assert row.description_status == "ready"


async def test_translate_field_und_writes_passthrough_with_raw(
    harness: _Harness, monkeypatch: pytest.MonkeyPatch
) -> None:
    report_id = await _seed_report(harness.conn, harness.crisis_id, description="ok")

    async def fake_translate(_text: str, *, clients: Any) -> TranslationResult:
        _ = clients
        return TranslationResult(lang="und", text_en="")

    monkeypatch.setattr(enrichment, "detect_and_translate", fake_translate)

    await enrichment.translate_field(harness.make_ctx(), str(report_id), "description")

    row = (
        await harness.conn.execute(
            text(
                "select description_lang, description_en, description_status "
                "from public.report_translations where report_id = :id"
            ),
            {"id": str(report_id)},
        )
    ).one()
    assert row.description_lang == "und"
    assert row.description_en == "ok"
    assert row.description_status == "passthrough"


async def test_translate_field_parse_error_writes_failed(
    harness: _Harness, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A deterministic parse error is terminal — no retry, status='failed'."""
    report_id = await _seed_report(harness.conn, harness.crisis_id, description="x")

    async def fake_translate(_text: str, *, clients: Any) -> TranslationResult:
        _ = clients
        raise enrichment.LLMOutputError("model output is not JSON")

    monkeypatch.setattr(enrichment, "detect_and_translate", fake_translate)

    await enrichment.translate_field(harness.make_ctx(), str(report_id), "description")

    row = (
        await harness.conn.execute(
            text(
                "select description_status, description_error "
                "from public.report_translations where report_id = :id"
            ),
            {"id": str(report_id)},
        )
    ).one()
    assert row.description_status == "failed"
    assert "parse_error" in (row.description_error or "")


# ---------------------------------------------------------------------------
# caption_image
# ---------------------------------------------------------------------------


async def test_caption_image_writes_ready_with_label_and_score(
    harness: _Harness, monkeypatch: pytest.MonkeyPatch
) -> None:
    report_id = await _seed_report(harness.conn, harness.crisis_id)

    async def fake_caption(_bytes: bytes, *, clients: Any) -> CaptionResult:
        _ = clients
        return CaptionResult(caption="A collapsed three-storey concrete building.")

    async def fake_relevance(
        _caption: str, *, clients: Any, crisis_context: str | None = None
    ) -> RelevanceResult:
        _ = clients, crisis_context
        return RelevanceResult(label="relevant", score=0.91)

    monkeypatch.setattr(enrichment, "caption_image_primitive", fake_caption)
    monkeypatch.setattr(enrichment, "score_relevance", fake_relevance)

    await enrichment.caption_image(harness.make_ctx(), str(report_id))

    row = (
        await harness.conn.execute(
            text(
                "select caption, relevance_label, relevance_score, status "
                "from public.image_captions where report_id = :id"
            ),
            {"id": str(report_id)},
        )
    ).one()
    assert row.status == "ready"
    assert row.relevance_label == "relevant"
    assert abs(float(row.relevance_score) - 0.91) < 1e-6
    assert "collapsed" in (row.caption or "")


async def test_caption_image_empty_caption_writes_failed(
    harness: _Harness, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An empty caption from the vision model is terminal — relevance would
    have nothing to score, so the whole sub-job is `failed`."""
    report_id = await _seed_report(harness.conn, harness.crisis_id)

    async def fake_caption(_bytes: bytes, *, clients: Any) -> CaptionResult:
        _ = clients
        return CaptionResult(caption="")

    monkeypatch.setattr(enrichment, "caption_image_primitive", fake_caption)

    await enrichment.caption_image(harness.make_ctx(), str(report_id))

    row = (
        await harness.conn.execute(
            text("select status, error from public.image_captions where report_id = :id"),
            {"id": str(report_id)},
        )
    ).one()
    assert row.status == "failed"
    assert row.error == "empty_caption"


# ---------------------------------------------------------------------------
# Atomic finalize
# ---------------------------------------------------------------------------


async def test_finalize_fires_embed_report_when_all_terminal(
    harness: _Harness, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When the last sub-job flips its row to terminal, finalize wins the
    UPDATE and `embed_report(report_id)` is enqueued exactly once.

    The downstream job is now the embed job, no longer the placeholder publish_to_rag."""
    report_id = await _seed_report(harness.conn, harness.crisis_id)

    # Pre-set translations to skipped (no text fields).
    await harness.conn.execute(
        text(
            "update public.report_translations "
            "   set description_status = 'skipped', "
            "       route_description_status = 'skipped' "
            " where report_id = :id"
        ),
        {"id": str(report_id)},
    )

    async def fake_caption(_bytes: bytes, *, clients: Any) -> CaptionResult:
        _ = clients
        return CaptionResult(caption="A scene with debris.")

    async def fake_relevance(
        _caption: str, *, clients: Any, crisis_context: str | None = None
    ) -> RelevanceResult:
        _ = clients, crisis_context
        return RelevanceResult(label="relevant", score=0.8)

    monkeypatch.setattr(enrichment, "caption_image_primitive", fake_caption)
    monkeypatch.setattr(enrichment, "score_relevance", fake_relevance)

    await enrichment.caption_image(harness.make_ctx(), str(report_id))

    finalized_at = (
        await harness.conn.execute(
            text("select enrichment_finalized_at from public.reports where id = :id"),
            {"id": str(report_id)},
        )
    ).scalar_one()
    assert finalized_at is not None

    publish_calls = [c for c in harness.pool.calls if c[0] == "embed_report"]
    assert len(publish_calls) == 1
    assert publish_calls[0][1] == (str(report_id),)


async def test_photo_less_report_finalizes_via_skipped_caption(
    harness: _Harness, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A description-only report (no photo) must still finalize and embed.

    A photo-less report always carries a description (the photo-or-description
    invariant), so the caption skips (no image) while the description is
    translated. Once translation reaches a terminal state the widened finalize
    predicate (`c.status in ('ready','skipped','failed')`) lets the report
    complete and enqueue embed_report.
    """
    report_id = await _seed_report(
        harness.conn,
        harness.crisis_id,
        with_photo=False,
        description="The stairwell collapsed; no safe way to take a photo.",
    )

    # Dispatcher: caption -> skipped (no photo), geocode -> skipped (has GPS),
    # description translation -> enqueued.
    await enrichment.enrich_report(harness.make_ctx(), str(report_id))

    caption_status = (
        await harness.conn.execute(
            text("select status from public.image_captions where report_id = :id"),
            {"id": str(report_id)},
        )
    ).scalar_one()
    assert caption_status == "skipped"

    async def fake_translate(_text: str, *, clients: Any) -> TranslationResult:
        _ = clients
        return TranslationResult(lang="en", text_en="")

    monkeypatch.setattr(enrichment, "detect_and_translate", fake_translate)

    # Translating the description is the last terminal transition; its
    # _try_finalize must win now that caption sits at 'skipped'.
    await enrichment.translate_field(harness.make_ctx(), str(report_id), "description")

    finalized_at = (
        await harness.conn.execute(
            text("select enrichment_finalized_at from public.reports where id = :id"),
            {"id": str(report_id)},
        )
    ).scalar_one()
    assert finalized_at is not None

    embed_calls = [c for c in harness.pool.calls if c[0] == "embed_report"]
    assert len(embed_calls) == 1
    assert embed_calls[0][1] == (str(report_id),)


async def test_finalize_no_fire_when_a_stage_still_pending(
    harness: _Harness, monkeypatch: pytest.MonkeyPatch
) -> None:
    """While description_status is still 'pending', a successful caption
    must NOT finalise."""
    report_id = await _seed_report(harness.conn, harness.crisis_id, description="some text")

    async def fake_caption(_bytes: bytes, *, clients: Any) -> CaptionResult:
        _ = clients
        return CaptionResult(caption="A scene with debris.")

    async def fake_relevance(
        _caption: str, *, clients: Any, crisis_context: str | None = None
    ) -> RelevanceResult:
        _ = clients, crisis_context
        return RelevanceResult(label="relevant", score=0.8)

    monkeypatch.setattr(enrichment, "caption_image_primitive", fake_caption)
    monkeypatch.setattr(enrichment, "score_relevance", fake_relevance)

    await enrichment.caption_image(harness.make_ctx(), str(report_id))

    finalized_at = (
        await harness.conn.execute(
            text("select enrichment_finalized_at from public.reports where id = :id"),
            {"id": str(report_id)},
        )
    ).scalar_one()
    assert finalized_at is None

    publish_calls = [c for c in harness.pool.calls if c[0] == "embed_report"]
    assert publish_calls == []


async def test_finalize_is_idempotent_once_set(
    harness: _Harness, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A second finalize attempt after the first has already set the
    timestamp does NOT re-enqueue the downstream jobs — the conditional
    UPDATE's `is null` predicate blocks the second writer.

    A finalize win fans out to BOTH `embed_report` and `score_report`;
    the idempotency property is that the second attempt adds
    nothing."""
    report_id = await _seed_report(harness.conn, harness.crisis_id)
    await harness.conn.execute(
        text(
            "update public.report_translations "
            "   set description_status = 'skipped', "
            "       route_description_status = 'skipped' "
            " where report_id = :id"
        ),
        {"id": str(report_id)},
    )
    # Set the captions to ready directly so any finalize attempt would win.
    await harness.conn.execute(
        text(
            "update public.image_captions "
            "   set caption = 'x', relevance_label = 'relevant', "
            "       relevance_score = 0.5, status = 'ready' "
            " where report_id = :id"
        ),
        {"id": str(report_id)},
    )

    ctx = harness.make_ctx()
    await enrichment._try_finalize(ctx, harness.sessionmaker, report_id)  # type: ignore[arg-type]
    first_calls = list(harness.pool.calls)
    await enrichment._try_finalize(ctx, harness.sessionmaker, report_id)  # type: ignore[arg-type]
    second_calls = list(harness.pool.calls)

    first_names = {c[0] for c in first_calls}
    assert first_names == {"embed_report", "score_report"}
    # Idempotent: the second attempt's `is null` predicate fails, so no new
    # downstream jobs are enqueued.
    assert second_calls == first_calls
