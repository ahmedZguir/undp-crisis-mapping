-- Fold reports.infra_description into description, then drop it.

update public.reports
set description = infra_description
where description is null
  and infra_description is not null;

alter table public.reports
    drop column if exists infra_description;
