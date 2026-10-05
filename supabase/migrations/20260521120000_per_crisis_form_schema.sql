-- Per-crisis form schema with version history.
--
-- photo_and_damage and location are locked at indexes 0 and 1; the other
-- pages can be reordered. Built-in page titles come from PWA i18n.

-- crises.form_schema + form_version

alter table public.crises
    add column if not exists form_version int not null default 1,
    add column if not exists form_schema  jsonb not null default '{
        "pages": [
            {"kind": "photo_and_damage", "enabled": true,  "locked": true},
            {"kind": "location",         "enabled": true,  "locked": true},
            {"kind": "description",      "enabled": true,  "locked": false},
            {"kind": "debris",           "enabled": true,  "locked": false},
            {"kind": "infra_type",       "enabled": true,  "locked": false},
            {"kind": "crisis_nature",    "enabled": true,  "locked": false},
            {"kind": "electricity",      "enabled": false, "locked": false},
            {"kind": "health_services",  "enabled": false, "locked": false},
            {"kind": "pressing_needs",   "enabled": false, "locked": false}
        ]
    }'::jsonb;

-- crisis_form_versions history

create table if not exists public.crisis_form_versions (
    crisis_id    uuid        not null references public.crises(id) on delete cascade,
    version      int         not null,
    schema       jsonb       not null,
    published_at timestamptz not null default now(),
    primary key (crisis_id, version)
);

-- Admin-only via the API: RLS on with no policies.
alter table public.crisis_form_versions enable row level security;
alter table public.crisis_form_versions force  row level security;

-- Seed version 1 for existing crises.
insert into public.crisis_form_versions (crisis_id, version, schema)
select id, form_version, form_schema from public.crises
on conflict (crisis_id, version) do nothing;

-- reports.form_version + generic_answers

alter table public.reports
    add column if not exists form_version    int,
    add column if not exists generic_answers jsonb not null default '{}'::jsonb;
