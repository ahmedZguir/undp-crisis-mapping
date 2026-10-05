-- Crisis metadata: event type, countries, event time window.
--
-- type has no CHECK while the taxonomy is still settling. countries holds
-- ISO 3166-1 alpha-2 codes and defaults to '{}' so array filters never hit NULL.
-- started_at / ended_at are real-world event times, not platform times.

alter table public.crises
    add column type        text         not null default 'other',
    add column countries   text[]       not null default '{}',
    add column started_at  timestamptz,
    add column ended_at    timestamptz;

-- CHECKs can't contain subqueries, so the per-element regex test lives in an
-- IMMUTABLE function. Empty arrays pass.
create or replace function public._iso2_array_valid(c text[])
returns boolean
language sql
immutable
as $$
    select coalesce(bool_and(x ~ '^[A-Z]{2}$'), true) from unnest(c) x
$$;

alter table public.crises
    add constraint crises_countries_iso2_chk
        check (public._iso2_array_valid(countries));

alter table public.crises
    add constraint crises_time_window_chk
        check (ended_at is null or started_at is null or ended_at >= started_at);
