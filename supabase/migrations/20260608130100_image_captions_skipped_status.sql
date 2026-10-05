-- Allow image_captions.status = 'skipped' for reports without a photo, so they
-- still reach a terminal state and finalize.

alter table public.image_captions
    drop constraint image_captions_status_check,
    add constraint image_captions_status_check
        check (status in ('pending', 'ready', 'skipped', 'failed'));
