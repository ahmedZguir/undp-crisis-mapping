-- Trigram index for the admin building-name autocomplete (similarity + ILIKE).

create extension if not exists pg_trgm;

create index if not exists buildings_name_trgm
    on public.buildings using gin (name gin_trgm_ops)
    where name is not null;
