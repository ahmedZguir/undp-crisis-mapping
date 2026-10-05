-- Free-text directions when the reporter has no GPS. Length is capped in the
-- API, not here.

alter table public.reports
    add column if not exists route_description text;
