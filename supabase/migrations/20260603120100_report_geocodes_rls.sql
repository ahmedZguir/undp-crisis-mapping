-- Re-assert RLS on report_geocodes for databases that applied the previous
-- migration before it enabled RLS. No-op otherwise.
alter table public.report_geocodes enable row level security;
alter table public.report_geocodes force row level security;
