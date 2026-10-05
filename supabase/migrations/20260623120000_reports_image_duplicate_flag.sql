-- Backfill report_quality.is_duplicate_image for reports the scoring worker
-- never processed (e.g. bulk-loaded ones).
--
-- Every report after the first on a shared photo_path is flagged, matching the
-- worker. Only rows with computed_at null are touched, and computed_at stays
-- null since only this flag is set.

with ranked as (
    select id,
           row_number() over (partition by photo_path
                               order by created_at, id) as rn
    from public.reports
    where photo_path is not null
)
update public.report_quality rq
set is_duplicate_image = true
from ranked
where ranked.id = rq.report_id
  and ranked.rn > 1
  and rq.computed_at is null
  and rq.is_duplicate_image = false;
