-- crisis_reports.created_by_email, snapshotted at generation time.
--
-- api_service can't read auth.users, so the email can't be joined at read
-- time; a snapshot also records who generated the PDF then, regardless of later
-- account changes. NULL when unknown.

alter table public.crisis_reports
    add column if not exists created_by_email text;

comment on column public.crisis_reports.created_by_email is
  'Generator''s email, frozen at generation time (snapshot, not a live lookup). '
  'NULL for pre-existing rows or unresolved authors. See '
  'docs/decisions/03_06-crisis-analysis-reports.md.';

-- Backfill existing rows. Migrations run as postgres, which can read auth.users.
update public.crisis_reports cr
    set created_by_email = u.email
    from auth.users u
    where u.id = cr.created_by
      and cr.created_by_email is null;
