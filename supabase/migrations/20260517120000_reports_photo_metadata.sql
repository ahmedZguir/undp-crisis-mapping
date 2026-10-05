-- Photo EXIF metadata, extracted by the PWA before its crop re-encodes the JPEG
-- and strips EXIF. Capture time and GPS get their own columns for filtering and
-- mapping; the rest goes in JSONB.

alter table public.reports
    add column if not exists photo_captured_at timestamptz,
    add column if not exists photo_exif_gps geography(point, 4326),
    add column if not exists photo_exif_extracted_at timestamptz,
    add column if not exists photo_exif_meta jsonb;

-- For capture-time window queries.
create index if not exists reports_photo_captured_at_idx
    on public.reports (photo_captured_at);
