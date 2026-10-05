"""Unit tests for the `generate_crisis_report` job core.

`run_generate_crisis_report` takes the sessionmaker, AI clients, the PDF store,
and the four pipeline callables as parameters, so we drive it entirely with
fakes — no Redis, no WeasyPrint, no network, no live DB. A fake sessionmaker
captures every statement + params so we can assert on what the job wrote
('succeeded' + storage_key + headline counts on success; 'failed' + error on
exception) and that it never leaves the row 'running'.
"""

from __future__ import annotations

import uuid
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Any

import pytest

from api.analysis.job import run_generate_crisis_report
from api.analysis.metrics import (
    BBox,
    CommunitySummary,
    CoverageMetrics,
    CrisisReportData,
    DamageMix,
    HotspotMetrics,
    ImpactEstimates,
    InfraMetrics,
    Narrative,
    ReportMeta,
    ReportMetrics,
)

CRISIS_ID = uuid.uuid4()
REPORT_ID = uuid.uuid4()


def _metrics() -> ReportMetrics:
    meta = ReportMeta(
        crisis_id=CRISIS_ID,
        crisis_name="Test Crisis",
        crisis_type="flood",
        countries=["QA"],
        as_of=datetime(2026, 6, 3, 14, 0, tzinfo=UTC),
        bbox=BBox(0.0, 0.0, 1.0, 1.0),
        report_count=42,
        device_count=17,
        building_count=1000,
        reported_building_count=120,
    )
    return ReportMetrics(
        meta=meta,
        damage=DamageMix(minimal=10, partial=20, complete=12),
        priority_districts=[],
        coverage=CoverageMetrics(
            coverage_pct=12.0,
            observed_total=42,
            expected_total=50.0,
            blind_spot_district_ids=[],
            cells=[],
        ),
        hotspots=HotspotMetrics(hot_cell_count=0, cold_cell_count=0, significance_z=1.96),
        infra=InfraMetrics(items=[], by_type={}),
        impact=ImpactEstimates(
            affected_buildings=32,
            displaced_low=50,
            displaced_high=150,
            population_available=False,
            economic_available=False,
            economic_low_usd=None,
            economic_high_usd=None,
            notes=[],
        ),
    )


# --- fakes -----------------------------------------------------------------


class _FakeResult:
    def __init__(self, row: Any) -> None:
        self._row = row

    def first(self) -> Any:
        return self._row


class _FakeSession:
    """Captures (sql, params) for every execute; serves the crisis_id lookup.

    Doubles as its own `async with session:` and `async with session.begin():`
    context — the job uses both shapes (a plain read session and a
    transactional write session)."""

    def __init__(self, statements: list[tuple[str, dict[str, Any]]]) -> None:
        self._statements = statements

    async def execute(self, statement: Any, params: dict[str, Any] | None = None) -> _FakeResult:
        sql = str(statement)
        self._statements.append((sql, params or {}))
        if "select crisis_id from public.crisis_reports" in sql:
            return _FakeResult(type("Row", (), {"crisis_id": CRISIS_ID})())
        return _FakeResult(None)

    async def __aenter__(self) -> _FakeSession:
        return self

    async def __aexit__(self, *exc: object) -> None:
        return None

    @asynccontextmanager
    async def begin(self) -> Any:
        yield self


class _FakeSessionmaker:
    def __init__(self) -> None:
        self.statements: list[tuple[str, dict[str, Any]]] = []

    def __call__(self) -> _FakeSession:
        return _FakeSession(self.statements)


class _FakeStore:
    def __init__(self) -> None:
        self.uploaded: list[tuple[bytes, str]] = []

    async def upload_report_pdf(self, content: bytes, key: str) -> str:
        self.uploaded.append((content, key))
        return key

    async def sign_report_url(self, key: str, ttl_seconds: int) -> str:  # pragma: no cover
        return f"https://signed/{key}"


def _terminal_update(statements: list[tuple[str, dict[str, Any]]]) -> tuple[str, dict[str, Any]]:
    """The last statement that flips status to a terminal value."""
    for sql, params in reversed(statements):
        if "status = 'succeeded'" in sql or "status = 'failed'" in sql:
            return sql, params
    raise AssertionError("no terminal update written")


# --- the pipeline callables (async/sync to match the real signatures) ------


async def _ok_build_metrics(session: Any, crisis_id: uuid.UUID) -> ReportMetrics:
    return _metrics()


async def _ok_build_narrative(clients: Any, metrics: ReportMetrics) -> Narrative:
    return Narrative(bottom_line="bl", recommended_actions=["a"], section_prose={})


async def _ok_build_community(
    session: Any, clients: Any, crisis_id: uuid.UUID, metrics: ReportMetrics
) -> CommunitySummary:
    return CommunitySummary(headline=None, districts=[])


def _ok_render(data: CrisisReportData) -> bytes:
    assert isinstance(data, CrisisReportData)
    return b"%PDF-1.7 fake"


# --- tests -----------------------------------------------------------------


@pytest.mark.asyncio
async def test_job_marks_succeeded_with_storage_key_and_headline_counts() -> None:
    sm = _FakeSessionmaker()
    store = _FakeStore()

    await run_generate_crisis_report(
        report_id=REPORT_ID,
        sessionmaker=sm,  # type: ignore[arg-type]
        clients=object(),  # type: ignore[arg-type]
        store=store,
        build_metrics=_ok_build_metrics,
        build_narrative=_ok_build_narrative,
        build_community=_ok_build_community,
        render_pdf=_ok_render,
    )

    # PDF uploaded under the deterministic key.
    expected_key = f"{CRISIS_ID}/{REPORT_ID}.pdf"
    assert store.uploaded == [(b"%PDF-1.7 fake", expected_key)]

    sql, params = _terminal_update(sm.statements)
    assert "status = 'succeeded'" in sql
    assert params["storage_key"] == expected_key
    assert params["report_count"] == 42
    assert params["device_count"] == 17
    assert params["building_count"] == 1000
    assert params["coverage_pct"] == 12.0

    # Phases were written in order before the terminal write.
    phases = [
        params2["phase"]
        for sql2, params2 in sm.statements
        if "set phase = :phase" in sql2 and "phase" in params2
    ]
    assert phases == ["analysing", "summarising", "rendering", "uploading"]


@pytest.mark.asyncio
async def test_job_marks_failed_with_error_on_exception() -> None:
    sm = _FakeSessionmaker()
    store = _FakeStore()

    def _boom_render(data: CrisisReportData) -> bytes:
        raise RuntimeError("weasyprint exploded")

    with pytest.raises(RuntimeError, match="weasyprint exploded"):
        await run_generate_crisis_report(
            report_id=REPORT_ID,
            sessionmaker=sm,  # type: ignore[arg-type]
            clients=object(),  # type: ignore[arg-type]
            store=store,
            build_metrics=_ok_build_metrics,
            build_narrative=_ok_build_narrative,
            build_community=_ok_build_community,
            render_pdf=_boom_render,
        )

    assert store.uploaded == []  # never got to upload
    sql, params = _terminal_update(sm.statements)
    assert "status = 'failed'" in sql
    assert "weasyprint exploded" in params["error"]
    # No 'succeeded' write anywhere — row never wedged at running on a caught error.
    assert not any("status = 'succeeded'" in s for s, _ in sm.statements)
