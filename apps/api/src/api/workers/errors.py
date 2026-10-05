"""Retry and error-message helpers shared by the Arq jobs."""

from __future__ import annotations

from typing import Any, cast

# Cap for the error text persisted on sidecar and job rows.
ERROR_MAX_LEN = 500


def is_last_try(ctx: dict[str, object]) -> bool:
    """Missing or invalid values count as the last try, so the failure gets recorded."""
    job_try = cast(Any, ctx.get("job_try", 1))
    max_tries = cast(Any, ctx.get("max_tries", 1))
    try:
        return int(job_try) >= int(max_tries)
    except (TypeError, ValueError):
        return True


def format_error(exc: BaseException) -> str:
    return f"{type(exc).__name__}: {exc}"


def truncate_error(msg: str) -> str:
    return msg.replace("\n", " ").strip()[:ERROR_MAX_LEN]


def format_terminal_error(exc: BaseException) -> str:
    return truncate_error(format_error(exc))
