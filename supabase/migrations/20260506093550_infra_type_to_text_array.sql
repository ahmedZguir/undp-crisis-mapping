-- reports.infra_type becomes text[] to match the PWA's multi-select.
-- Existing values are wrapped into single-element arrays.

alter table public.reports
    alter column infra_type type text[]
    using case
        when infra_type is null then null
        else array[infra_type]
    end;
