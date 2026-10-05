-- Least-privilege login role for the API: DML on public, no superuser.
--
-- BYPASSRLS because the API enforces authorisation in handler code; RLS
-- protects anon/authenticated callers hitting PostgREST directly.
--
-- The password is the local-dev value; production sets its own.

do $$
begin
    if not exists (select 1 from pg_roles where rolname = 'api_service') then
        create role api_service with login bypassrls password 'api_service_dev_password';
    else
        alter role api_service with login bypassrls password 'api_service_dev_password';
    end if;
end
$$;

grant usage on schema public to api_service;

grant select, insert, update, delete on all tables    in schema public to api_service;
grant usage, select                  on all sequences in schema public to api_service;
grant execute                        on all functions in schema public to api_service;

-- Objects created later in public by postgres get the same grants.
alter default privileges in schema public
    grant select, insert, update, delete on tables    to api_service;
alter default privileges in schema public
    grant usage, select                  on sequences to api_service;
alter default privileges in schema public
    grant execute                        on functions to api_service;

-- Not granted on purpose: SUPERUSER/CREATEDB/CREATEROLE/REPLICATION, the auth
-- schema (the API uses the Auth admin API), the storage schema (signed URLs
-- come from the service-role JWT), and grant option.
