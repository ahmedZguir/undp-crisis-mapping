-- Public heatmap: report counts per (crisis, H3 res-9 cell).
-- The report writer upserts in the same transaction as the report insert. Cell
-- ids come from the Python h3 package, so Postgres needs no h3 extension.

create table public.heat_cells (
    crisis_id        uuid        not null references public.crises(id) on delete cascade,
    h3_cell          bigint      not null,
    report_count     int         not null default 0,
    minimal_count    int         not null default 0,
    partial_count    int         not null default 0,
    complete_count   int         not null default 0,
    latest_at        timestamptz not null,
    primary key (crisis_id, h3_cell)
);

-- k-anonymity floor for public heatmap reads. 1 shows every non-empty cell.
alter table public.crises
    add column if not exists heatmap_k_anonymity int not null default 1;

-- For the last_24h / last_7d counts on /stats.
create index if not exists idx_reports_crisis_created_at
    on public.reports (crisis_id, created_at desc);
