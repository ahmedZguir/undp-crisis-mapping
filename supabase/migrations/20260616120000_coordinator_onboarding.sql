-- Per-coordinator first-run onboarding state, stored server-side so it follows
-- the account across devices.
--
--   walkthrough_completed_at  welcome walkthrough finished or skipped
--   dashboard_explored_at     dashboard checklist fully ticked
--
-- Both are set once and never cleared. coordinator_id is auth.users.id with no
-- FK; the RLS policy limits direct PostgREST access to the owner's row.

create table if not exists public.coordinator_onboarding (
    coordinator_id            uuid primary key,
    walkthrough_completed_at  timestamptz,
    dashboard_explored_at     timestamptz,
    updated_at                timestamptz not null default now()
);

alter table public.coordinator_onboarding enable row level security;
alter table public.coordinator_onboarding force row level security;

create policy coordinator_onboarding_owner
    on public.coordinator_onboarding
    for all
    to authenticated
    using (coordinator_id = auth.uid())
    with check (coordinator_id = auth.uid());
