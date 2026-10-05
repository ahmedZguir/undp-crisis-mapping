-- Scheduled crisis activation, either at a time (activate_at, checked by a
-- per-minute cron) or when building ingest succeeds. The two are mutually
-- exclusive.

alter table public.crises
    add column activate_at                  timestamptz,
    add column activate_on_ingest_success   boolean not null default false,
    add constraint crises_activation_mode_chk
        check (not (activate_at is not null and activate_on_ingest_success));

-- Only rows that could still fire are indexed, keeping the cron scan cheap.
create index crises_activate_at_pending_idx
    on public.crises (activate_at)
    where status = 'inactive' and activate_at is not null;
