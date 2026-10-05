-- Scratch schema for building ingest staging tables.
--
-- api_service has no CREATE on public, so the loader gets its own schema for
-- its per-crisis UNLOGGED stage tables. Destructive loader DDL stays here;
-- public.buildings is only ever upserted into.

create schema if not exists ingest;

-- api_service owns the tables it creates here, so TRUNCATE/DROP need no grant.
grant usage, create on schema ingest to api_service;
