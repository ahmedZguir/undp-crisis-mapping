-- crisis_jobs: latest run of each background job per crisis.
-- The (crisis_id, job_type) PK means a re-run overwrites the row.

create table if not exists public.crisis_jobs (
    crisis_id   uuid not null references public.crises(id) on delete cascade,
    job_type    text not null,
    status      text not null check (status in ('running', 'succeeded', 'failed')),
    error       text,
    started_at  timestamptz not null default now(),
    ended_at    timestamptz,
    primary key (crisis_id, job_type)
);
