-- Building count from the last ingest, so the admin view needs no spatial count.
-- NULL means never ingested; reset alongside buildings_ingested_at when the
-- polygon changes.

alter table public.crises
    add column if not exists buildings_ingested_count integer;
