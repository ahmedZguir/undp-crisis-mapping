-- Drop the saved_search_views.coordinator_id FK to auth.users on databases
-- that were created with it. No public table references the auth schema.

alter table public.saved_search_views
    drop constraint if exists saved_search_views_coordinator_id_fkey;
