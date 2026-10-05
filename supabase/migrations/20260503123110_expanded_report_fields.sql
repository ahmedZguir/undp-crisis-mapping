-- Categorical report fields the PWA collects. Nullable, no backfill.

alter table public.reports
    add column if not exists infra_type text,
    add column if not exists infra_name text,
    add column if not exists infra_description text,
    add column if not exists crisis_type text,
    add column if not exists crisis_type_detailed text,
    add column if not exists debris text;

alter table public.reports
    drop constraint if exists reports_debris_check;

alter table public.reports
    add constraint reports_debris_check
    check (debris is null or debris in ('yes', 'no', 'unknown'));
