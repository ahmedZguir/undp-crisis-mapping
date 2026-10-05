-- Saved search views: a coordinator's named /admin/reports/search filter
-- payload, re-run against live data when opened.
--
-- coordinator_id is auth.users.id with no FK; no public table references the
-- auth schema. The RLS policy keeps direct PostgREST access to the owner's rows.

create table if not exists public.saved_search_views (
    id              uuid primary key default gen_random_uuid(),
    coordinator_id  uuid not null,
    crisis_id       uuid not null references public.crises(id) on delete cascade,
    name            text not null,
    payload         jsonb not null,
    created_at      timestamptz not null default now(),
    updated_at      timestamptz not null default now()
);

create index if not exists saved_search_views_coordinator_crisis
    on public.saved_search_views (coordinator_id, crisis_id);

alter table public.saved_search_views enable row level security;
alter table public.saved_search_views force row level security;

create policy saved_search_views_owner
    on public.saved_search_views
    for all
    to authenticated
    using (coordinator_id = auth.uid())
    with check (coordinator_id = auth.uid());
