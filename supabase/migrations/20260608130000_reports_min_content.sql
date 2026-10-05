-- Minimum-content rule: photo or description, and location or route
-- description. photo_path becomes nullable. The API also enforces this and
-- turns empty strings into NULL, so is not null is a real check.

alter table public.reports alter column photo_path drop not null;

alter table public.reports
    add constraint reports_photo_or_description
        check (photo_path is not null or description is not null),
    add constraint reports_location_or_route
        check (location is not null or route_description is not null);
