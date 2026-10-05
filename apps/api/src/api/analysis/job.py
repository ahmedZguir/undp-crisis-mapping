"""Arq job that builds a crisis report and records progress on its pre-created
`crisis_reports` row. Any failure marks the row 'failed' and re-raises."""

from __future__ import annotations

import logging
import uuid
from typing import Protocol

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from api.ai.client import AIClients
from api.analysis.metrics import (
    CommunitySummary,
    CrisisReportData,
    Narrative,
    ReportMetrics,
)
from api.core.storage import ReportPdfStore
from api.workers.context import ai_clients_from_ctx, sessionmaker_from_ctx, storage_from_ctx
from api.workers.errors import format_terminal_error
from api.workers.job_rows import JobRow

logger = logging.getLogger(__name__)

# Values written to `crisis_reports.phase` as the pipeline advances.
PHASE_ANALYSING = "analysing"
PHASE_SUMMARISING = "summarising"
PHASE_RENDERING = "rendering"
PHASE_UPLOADING = "uploading"


class _MetricsBuilder(Protocol):
    async def __call__(self, session: AsyncSession, crisis_id: uuid.UUID) -> ReportMetrics: ...


class _NarrativeBuilder(Protocol):
    async def __call__(self, clients: AIClients, metrics: ReportMetrics) -> Narrative: ...


class _CommunityBuilder(Protocol):
    async def __call__(
        self,
        session: AsyncSession,
        clients: AIClients,
        crisis_id: uuid.UUID,
        metrics: ReportMetrics,
    ) -> CommunitySummary: ...


class _PdfRenderer(Protocol):
    def __call__(self, data: CrisisReportData) -> bytes: ...


async def generate_crisis_report(ctx: dict[str, object], report_id: str) -> None:
    """Arq task: render the analysis report for one pre-created row."""
    # Local imports defer WeasyPrint/PySAL loading.
    from api.analysis.builder import build_report_metrics
    from api.analysis.narrative import build_community_summary, build_narrative
    from api.analysis.render import render_report_pdf

    sessionmaker = sessionmaker_from_ctx(ctx)
    clients = ai_clients_from_ctx(ctx)
    store = storage_from_ctx(ctx)

    await run_generate_crisis_report(
        report_id=uuid.UUID(report_id),
        sessionmaker=sessionmaker,
        clients=clients,
        store=store,
        build_metrics=build_report_metrics,
        build_narrative=build_narrative,
        build_community=build_community_summary,
        render_pdf=render_report_pdf,
    )


async def run_generate_crisis_report(
    *,
    report_id: uuid.UUID,
    sessionmaker: async_sessionmaker[AsyncSession],
    clients: AIClients,
    store: ReportPdfStore,
    build_metrics: _MetricsBuilder,
    build_narrative: _NarrativeBuilder,
    build_community: _CommunityBuilder,
    render_pdf: _PdfRenderer,
) -> None:
    """Drive the report pipeline for one pre-created `crisis_reports` row."""
    crisis_id = await _load_crisis_id(sessionmaker, report_id)
    if crisis_id is None:
        logger.warning("generate_crisis_report: row %s vanished; nothing to do", report_id)
        return

    job_row = JobRow(sessionmaker, "crisis_reports", report_id, "generate_crisis_report report_id")
    try:
        await job_row.set_phase(PHASE_ANALYSING)
        async with sessionmaker() as session:
            metrics = await build_metrics(session, crisis_id)

            await job_row.set_phase(PHASE_SUMMARISING)
            narrative = await build_narrative(clients, metrics)
            community = await build_community(session, clients, crisis_id, metrics)

        await job_row.set_phase(PHASE_RENDERING)
        data = CrisisReportData(metrics=metrics, narrative=narrative, community=community)
        pdf = render_pdf(data)

        await job_row.set_phase(PHASE_UPLOADING)
        key = _storage_key(crisis_id, report_id)
        await store.upload_report_pdf(pdf, key)

        meta = metrics.meta
        await job_row.mark_succeeded(
            "storage_key = :storage_key, "
            "report_count = :report_count, device_count = :device_count, "
            "building_count = :building_count, coverage_pct = :coverage_pct",
            {
                "storage_key": key,
                "report_count": meta.report_count,
                "device_count": meta.device_count,
                "building_count": meta.building_count,
                "coverage_pct": meta.coverage_pct,
            },
        )
    except BaseException as exc:
        # BaseException so a job timeout or shutdown (CancelledError) still moves the
        # row off 'running'; otherwise the PWA polls it forever.
        logger.exception("generate_crisis_report failed report_id=%s", report_id)
        await job_row.safe_mark_failed(format_terminal_error(exc))
        raise


def _storage_key(crisis_id: uuid.UUID, report_id: uuid.UUID) -> str:
    """Deterministic object key: `<crisis_id>/<report_id>.pdf`."""
    return f"{crisis_id}/{report_id}.pdf"


async def _load_crisis_id(
    sessionmaker: async_sessionmaker[AsyncSession], report_id: uuid.UUID
) -> uuid.UUID | None:
    async with sessionmaker() as session:
        row = (
            await session.execute(
                text("select crisis_id from public.crisis_reports where id = :id"),
                {"id": str(report_id)},
            )
        ).first()
    return row.crisis_id if row is not None else None


__all__ = ["generate_crisis_report", "run_generate_crisis_report"]
