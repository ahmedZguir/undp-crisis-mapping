-- Add report_embeddings to the supabase_realtime publication.
--
-- The embedding insert, not the report insert, is published: by then the
-- report is enriched and searchable, so clients don't get raw pins.

do $$
begin
    if not exists (
        select 1
        from pg_publication_tables
        where pubname = 'supabase_realtime'
          and schemaname = 'public'
          and tablename = 'report_embeddings'
    ) then
        alter publication supabase_realtime add table public.report_embeddings;
    end if;
exception
    when undefined_object then
        -- Some stripped-down Postgres images have no supabase_realtime.
        null;
end $$;
