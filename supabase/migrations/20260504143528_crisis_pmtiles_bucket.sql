-- crisis-pmtiles bucket for per-crisis PMTiles.
--
-- Public so the PMTiles plugin can make cross-origin range requests without
-- signed URLs. Files are immutable; re-ingest writes a new object name.

insert into storage.buckets (id, name, public)
values ('crisis-pmtiles', 'crisis-pmtiles', true)
on conflict (id) do update set public = excluded.public;
