-- Per-report embeddings for semantic search.
--
-- halfvec because the 2560-d embeddings exceed HNSW's 2000-dim limit for
-- vector. Searches filter on embedding_version so a re-embed with a new model
-- never mixes vectors in one scan.

create extension if not exists vector;

create table if not exists public.report_embeddings (
    report_id          uuid primary key references public.reports(id) on delete cascade,
    embedding          halfvec(2560) not null,
    model              text not null,
    dimension          integer not null,
    embedding_version  integer not null,
    created_at         timestamptz not null default now()
);

-- ef_search is set per query from the strictness preset.
create index if not exists report_embeddings_hnsw
    on public.report_embeddings
    using hnsw (embedding halfvec_cosine_ops);

-- API-only data: RLS on with no policies.
alter table public.report_embeddings enable row level security;
alter table public.report_embeddings force row level security;
