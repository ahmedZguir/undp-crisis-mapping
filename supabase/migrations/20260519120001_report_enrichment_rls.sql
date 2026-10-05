-- Re-assert RLS on the enrichment sidecars for databases that applied the
-- previous migration before it enabled RLS. No-op otherwise.
alter table public.report_translations enable row level security;
alter table public.report_translations force row level security;
alter table public.image_captions enable row level security;
alter table public.image_captions force row level security;
