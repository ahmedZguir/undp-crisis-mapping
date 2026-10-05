-- Per-report switch to hide a single report from the public buildings and full
-- modes without changing the crisis's visibility.

alter table public.reports
    add column if not exists public_visible boolean not null default true;

comment on column public.reports.public_visible is
    'Per-report public-visibility kill switch. When false, the '
    'report is excluded from public buildings/full endpoints '
    'regardless of the crisis''s public_visibility setting. '
    'Default true. See ADR 13_05-per-crisis-public-visibility-model.md '
    'amendment §A5.';
