-- reports.client_submission_id: one UUID per PWA draft, sent on every retry.
-- The partial unique index turns a duplicate retry into a 23505 that the API
-- maps to the existing row. Callers that don't send it leave it null.

alter table public.reports
    add column client_submission_id uuid;

create unique index reports_client_submission_id_key
    on public.reports (client_submission_id)
    where client_submission_id is not null;
