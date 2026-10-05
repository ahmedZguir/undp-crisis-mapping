"""RLS baseline linter.

Two assertions, both run against the live local Supabase Postgres via
the API's configured DSN:

1. Every user table in `public` (except the PostGIS-owned
   `spatial_ref_sys`) has Row-Level Security enabled. A new migration
   that adds `public.foo` without `alter table foo enable row level
   security` fails this test.
2. Every such table either has at least one policy attached, or its
   name appears in `RESERVED_NO_POLICY_TABLES`. The allowlist is
   empty at v1 — populating it is the deliberate "this table has no
   anon/authenticated reach and that is intentional" signal.

The test runs as part of `uv run pytest` and therefore as part of the
pre-commit / CI loop — the deny-by-default posture cannot regress
silently.

Connection: uses the same DSN as the rest of the test suite
(`settings.database_url`), which after the role switch is the
`api_service` role. `api_service` has `BYPASSRLS`, but it also has
default-read access to `pg_class` / `pg_policies` (system catalogues
are readable by every authenticated DB user) — so the queries here
work regardless of the role's RLS posture.
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterable

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from api.core.config import get_settings

# Tables that legitimately have no anon/authenticated policy attached.
# Deny-by-default ("RLS on, no policy") is the intended posture for the
# entries here — they are served exclusively by the API via the
# `api_service` role (BYPASSRLS) and must NOT be reachable via PostgREST
# with a leaked anon key.
#
# The only public
# tables with an anon SELECT policy are `crises`, `heat_cells`,
# `buildings`. The four below are the v1 deny-list — anything else added
# without a policy fails the linter and forces the author to either add
# a policy or extend this set with a justifying comment.
RESERVED_NO_POLICY_TABLES: frozenset[str] = frozenset(
    {
        # spine migration 20260430094937; never served to anon — admin
        # detail endpoints fetch reports via api_service (BYPASSRLS).
        "reports",
        # admin migration 20260507115640; admin-only job state, served
        # via /admin/crises/{id}/jobs which authenticates via the
        # coordinator JWT gate.
        "crisis_jobs",
        # divisions migration 20260511073759; loaded by /admin/areas
        # for crisis-polygon selection, never served to anon.
        "overture_divisions",
        # divisions migration 20260511073759; loaded by /admin/areas
        # for crisis-polygon selection, never served to anon.
        "overture_division_areas",
        # enrichment migration 20260519120000; machine-derived translations
        # of reports.description / route_description. Coordinator-only via
        # api_service; never served to anon.
        "report_translations",
        # enrichment migration 20260519120000; machine-derived caption +
        # relevance label for the report photo. Coordinator-only via
        # api_service; never served to anon.
        "image_captions",
        # per-crisis form schema migration 20260521120000; history of
        # published form versions. Coordinator-only via api_service for
        # writes and admin routes for reads; never served to anon.
        "crisis_form_versions",
        # embedding sidecar migration 20260523120000; halfvec(2560)
        # vectors written by `embed_report` and read by
        # `/admin/reports/search`. Coordinator-only via api_service;
        # never served to anon.
        "report_embeddings",
        # geocoding sidecar migration 20260603120000; machine-derived
        # geocode of a report's free-text route_description (LLM toponym
        # extraction + OSM). Coordinator-only via api_service; never served
        # to anon.
        "report_geocodes",
        # density-grid migration 20260601140000; DERIVED reference data
        # (precomputed Overture building counts per grid cell), built offline
        # by a superuser job and read only by the admin ingest-estimate route
        # via api_service. Never served to anon.
        "building_density_grid",
        # crisis-reports migration 20260603130000; immutable history of
        # generated analysis-report PDFs. Admin-only, written + read by the
        # Arq worker / admin routes via api_service. Never served to anon.
        "crisis_reports",
        # report-exports migration 20260622120000; job state + permanent audit
        # for photo-bundle exports. Admin-only, written + read by the Arq
        # worker / admin routes via api_service. Never served to anon.
        "report_exports",
        # litpop migration 20260603130500; DERIVED reference data (precomputed
        # LitPop gridded asset value), built offline by a superuser job and
        # read only by the report builder via api_service. Never served to
        # anon.
        "litpop_value",
        # data-retention migration 20260615120000; PII-free audit of report
        # disposal (counts only). Written by the retention purge + citizen
        # deletes via api_service (BYPASSRLS); never served to anon.
        "data_disposal_log",
        # reporter-rewards migration 20260620120000; per-report quality +
        # coordinator confidence sidecar. Served to citizens (scoped by
        # client_id) and coordinators only via api_service (BYPASSRLS);
        # never served to anon.
        "report_quality",
        # reporter-rewards migration 20260620120000; earned citizen badges,
        # read by GET /me/stats scoped to the caller's client_id via
        # api_service (BYPASSRLS); never served to anon.
        "citizen_badges",
    }
)

# Tables managed by Postgres extensions or Supabase's own infrastructure.
# These are not "our" tables and we cannot/should not turn RLS on for
# them — `spatial_ref_sys` is PostGIS-owned (relowner = supabase_admin)
# and is a static 3000-row reference table.
EXTENSION_OWNED_TABLES: frozenset[str] = frozenset({"spatial_ref_sys"})


pytestmark = pytest.mark.integration


async def _public_tables_missing_rls() -> list[str]:
    settings = get_settings()
    engine = create_async_engine(settings.database_url)
    try:
        async with engine.connect() as conn:
            result = await conn.execute(
                text(
                    """
                    select c.relname
                    from pg_class c
                    join pg_namespace n on n.oid = c.relnamespace
                    where n.nspname = 'public'
                      and c.relkind = 'r'
                      and c.relrowsecurity = false
                    order by c.relname
                    """
                )
            )
            return [row[0] for row in result.all()]
    finally:
        await engine.dispose()


async def _public_tables_without_policy() -> list[str]:
    settings = get_settings()
    engine = create_async_engine(settings.database_url)
    try:
        async with engine.connect() as conn:
            result = await conn.execute(
                text(
                    """
                    select c.relname
                    from pg_class c
                    join pg_namespace n on n.oid = c.relnamespace
                    where n.nspname = 'public'
                      and c.relkind = 'r'
                      and not exists (
                          select 1 from pg_policies p
                          where p.schemaname = 'public'
                            and p.tablename = c.relname
                      )
                    order by c.relname
                    """
                )
            )
            return [row[0] for row in result.all()]
    finally:
        await engine.dispose()


def _filtered(names: Iterable[str], drop: frozenset[str]) -> list[str]:
    return [n for n in names if n not in drop]


def test_every_public_table_has_rls_enabled() -> None:
    """RLS must be ON for every user table in `public`. Extension-owned
    tables are exempted by `EXTENSION_OWNED_TABLES`."""
    missing = asyncio.run(_public_tables_missing_rls())
    offenders = _filtered(missing, EXTENSION_OWNED_TABLES)
    assert offenders == [], (
        f"RLS is disabled on public table(s) {offenders}. "
        "Add `alter table <name> enable row level security; "
        "alter table <name> force row level security;` to the migration "
        "that introduces the table, plus an explicit policy (or add it "
        "to RESERVED_NO_POLICY_TABLES with a justifying comment)."
    )


def test_every_public_table_has_a_policy_or_is_allowlisted() -> None:
    """Every user table either has a policy or appears in
    `RESERVED_NO_POLICY_TABLES`. The allowlist is empty at v1."""
    without_policy = asyncio.run(_public_tables_without_policy())
    # Skip extension-owned tables (they don't have RLS on either, so they
    # already fail the first test — no need to flag them twice).
    candidates = _filtered(without_policy, EXTENSION_OWNED_TABLES)
    offenders = _filtered(candidates, RESERVED_NO_POLICY_TABLES)
    assert offenders == [], (
        f"public table(s) {offenders} have RLS enabled but no policy attached. "
        "Add at least one policy (e.g. `create policy ... for select to anon "
        "using (...)`), or — if the table is intentionally unreachable to "
        "anon/authenticated — add its name to RESERVED_NO_POLICY_TABLES in "
        "this test file with a comment naming the introducing migration."
    )
