"""Logger-level filters that drop health-probe access lines and noisy cron ticks."""

from __future__ import annotations

import logging


class HealthEndpointAccessFilter(logging.Filter):
    """Drop uvicorn access lines for successful health probes; failures still log.

    uvicorn access args are (client_addr, method, path, http_version, status).
    Records of any other shape are kept.
    """

    _SILENCED_PATHS = frozenset({"/healthz", "/readyz", "/health"})

    def filter(self, record: logging.LogRecord) -> bool:
        args = record.args
        if not isinstance(args, tuple) or len(args) < 5:
            return True
        path, status = args[2], args[4]
        if not isinstance(path, str) or not isinstance(status, int):
            return True
        path = path.split("?", 1)[0]
        if path not in self._SILENCED_PATHS:
            return True
        return status >= 400


class ArqCronTickFilter(logging.Filter):
    """Drop arq's start/complete INFO lines for one cron job; warnings and errors pass."""

    def __init__(self, function_name: str) -> None:
        super().__init__()
        self._function_name = function_name

    def filter(self, record: logging.LogRecord) -> bool:
        if record.levelno > logging.INFO:
            return True
        return self._function_name not in record.getMessage()
