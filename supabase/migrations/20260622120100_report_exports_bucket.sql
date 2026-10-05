-- Private report-exports bucket for photo-bundle zip parts
-- (<crisis_id>/<export_id>/part-NN.zip). Bundles hold faces, exact coordinates
-- and client_id, so anon must never read them; admins download through
-- short-lived signed URLs.

insert into storage.buckets (id, name, public)
values ('report-exports', 'report-exports', false)
on conflict (id) do update set public = excluded.public;

-- Explicit anon deny. The API uses the service-role JWT, which bypasses RLS.
do $$
begin
    if not exists (
        select 1 from pg_policies
        where schemaname = 'storage'
          and tablename = 'objects'
          and policyname = 'report_exports_anon_no_read'
    ) then
        create policy report_exports_anon_no_read on storage.objects
            for select
            to anon
            using (false);
    end if;
end
$$;
