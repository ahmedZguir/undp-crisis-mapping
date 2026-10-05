-- Keep the report-photos bucket private and deny anon reads explicitly, so a
-- later broad storage policy that forgets a bucket_id scope still can't expose
-- photos to anon.

update storage.buckets
   set public = false
 where id = 'report-photos';

-- Guarded by pg_policies because older stacks lack create policy if not exists.
do $$
begin
    if not exists (
        select 1 from pg_policies
        where schemaname = 'storage'
          and tablename = 'objects'
          and policyname = 'photos_anon_no_read'
    ) then
        create policy photos_anon_no_read on storage.objects
            for select
            to anon
            using (false);
    end if;
end
$$;

-- The API mints signed URLs with the service-role JWT, which bypasses RLS.
