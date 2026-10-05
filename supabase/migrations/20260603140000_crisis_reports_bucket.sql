-- Private crisis-reports bucket for analysis-report PDFs, stored as
-- <crisis_id>/<report_id>.pdf. Admins download through short-lived signed URLs.

insert into storage.buckets (id, name, public)
values ('crisis-reports', 'crisis-reports', false)
on conflict (id) do update set public = excluded.public;

-- Explicit anon deny. The API uses the service-role JWT, which bypasses RLS.
do $$
begin
    if not exists (
        select 1 from pg_policies
        where schemaname = 'storage'
          and tablename = 'objects'
          and policyname = 'crisis_reports_anon_no_read'
    ) then
        create policy crisis_reports_anon_no_read on storage.objects
            for select
            to anon
            using (false);
    end if;
end
$$;
