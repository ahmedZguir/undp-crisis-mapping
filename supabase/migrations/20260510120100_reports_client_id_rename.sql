-- Rename reports.device_id (text) to client_id (uuid).
--
-- Reporting is anonymous: client_id is a per-install UUID the user can reset,
-- not a device fingerprint. Old values that aren't UUIDs become null.

alter table public.reports
    add column client_id uuid;

update public.reports
   set client_id = device_id::uuid
 where device_id ~ '^[0-9a-f-]{36}$';

alter table public.reports
    drop column device_id;
