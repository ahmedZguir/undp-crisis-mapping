-- Progress columns for the building ingest job, so the admin can show an x/n
-- bar during a long run.
--
-- The worker writes them in their own short transactions, so a progress write
-- never rolls back ingested rows. All nullable; jobs that don't report
-- progress leave them NULL.
--
--   phase           coarse stage label, free text so new phases need no migration
--   progress_count  rows upserted into buildings so far this run
--   progress_total  pre-flight estimate; NULL when none was taken

alter table public.crisis_jobs
    add column if not exists phase          text,
    add column if not exists progress_count bigint,
    add column if not exists progress_total bigint;
