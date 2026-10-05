"""Unit tests for the operational-noise logging filters.

Both filters are exercised against `LogRecord`s shaped exactly like the ones
their real producers emit, so a future uvicorn/arq upgrade that changes the
record shape (e.g. moves the request path out of `args[2]`) trips a test
rather than silently logging probes again.

- uvicorn access records: `'%s - "%s %s HTTP/%s" %d'` with
  args `(client_addr, method, path, http_version, status)`.
- arq worker records: `'%6.2fs → %s(%s)%s'` (start) and
  `'%6.2fs ← %s ● %s'` (complete), where the ref arg is
  `"<job_id>:<function_name>"`.
"""

from __future__ import annotations

import logging

from api.core.log_filters import ArqCronTickFilter, HealthEndpointAccessFilter


def _access_record(path: str, status: int = 200) -> logging.LogRecord:
    return logging.LogRecord(
        name="uvicorn.access",
        level=logging.INFO,
        pathname=__file__,
        lineno=0,
        msg='%s - "%s %s HTTP/%s" %d',
        args=("127.0.0.1:54321", "GET", path, "1.1", status),
        exc_info=None,
    )


def _arq_record(
    template: str,
    args: tuple[object, ...],
    level: int = logging.INFO,
) -> logging.LogRecord:
    return logging.LogRecord(
        name="arq.worker",
        level=level,
        pathname=__file__,
        lineno=0,
        msg=template,
        args=args,
        exc_info=None,
    )


class TestHealthEndpointAccessFilter:
    def test_drops_each_successful_health_path(self) -> None:
        f = HealthEndpointAccessFilter()
        for path in ("/healthz", "/readyz", "/health"):
            assert f.filter(_access_record(path, status=200)) is False, path

    def test_drops_health_path_with_query_string(self) -> None:
        f = HealthEndpointAccessFilter()
        assert f.filter(_access_record("/readyz?probe=lb", status=200)) is False

    def test_keeps_failing_health_probe(self) -> None:
        # A 4xx/5xx on a health path is signal (e.g. /readyz 503 = a dep down).
        f = HealthEndpointAccessFilter()
        assert f.filter(_access_record("/readyz", status=503)) is True
        assert f.filter(_access_record("/healthz", status=500)) is True

    def test_keeps_real_traffic(self) -> None:
        f = HealthEndpointAccessFilter()
        for path in ("/reports", "/crises", "/healthz/extra", "/"):
            assert f.filter(_access_record(path)) is True, path

    def test_keeps_record_with_unexpected_args_shape(self) -> None:
        # A non-access record (e.g. a plain string message, or fewer args than
        # the access template) must pass untouched — we only remove known noise.
        f = HealthEndpointAccessFilter()
        plain = logging.LogRecord(
            name="uvicorn.access",
            level=logging.INFO,
            pathname=__file__,
            lineno=0,
            msg="some operational note about /readyz",
            args=None,
            exc_info=None,
        )
        assert f.filter(plain) is True


class TestArqCronTickFilter:
    def test_drops_start_and_complete_lines_for_named_cron(self) -> None:
        f = ArqCronTickFilter("tick_scheduled_activations")
        ref = "abc123:tick_scheduled_activations"
        start = _arq_record("%6.2fs → %s(%s)%s", (0.01, ref, "", ""))
        complete = _arq_record("%6.2fs ← %s ● %s", (0.01, ref, "0"))
        assert f.filter(start) is False
        assert f.filter(complete) is False

    def test_keeps_other_jobs(self) -> None:
        f = ArqCronTickFilter("tick_scheduled_activations")
        rec = _arq_record("%6.2fs → %s(%s)%s", (0.01, "def456:ingest_buildings", "", ""))
        assert f.filter(rec) is True

    def test_keeps_warnings_and_errors_for_the_cron(self) -> None:
        # A failed/expired tick must stay visible even though it names the cron.
        f = ArqCronTickFilter("tick_scheduled_activations")
        warn = _arq_record(
            "%6.2fs ! %s max retries %d exceeded",
            (0.01, "abc123:tick_scheduled_activations", 1),
            level=logging.WARNING,
        )
        err = _arq_record(
            "%6.2fs ! %s failed, %s: %s",
            (0.01, "abc123:tick_scheduled_activations", "RuntimeError", "boom"),
            level=logging.ERROR,
        )
        assert f.filter(warn) is True
        assert f.filter(err) is True
