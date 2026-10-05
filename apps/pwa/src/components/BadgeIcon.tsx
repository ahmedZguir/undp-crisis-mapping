// Shared badge glyphs; the single source for badge path data.
const CHECK_CIRCLE_PATH =
  "M10 2C5.59 2 2 5.59 2 10s3.59 8 8 8 8-3.59 8-8-3.59-8-8-8Zm-1 11.5L5.5 10l1.41-1.41L9 10.67l4.59-4.58L15 7.5 9 13.5Z";

const BADGE_ICONS: Record<string, string> = {
  first_report:
    "M11 2l2.39 5.26L19 8.27l-4 3.89.94 5.5L11 15.27l-4.94 2.39.94-5.5L3 8.27l5.61-.01L11 2Z",
  // `quality_reporter` is a legacy alias so older cached stats still render.
  ground_truth: CHECK_CIRCLE_PATH,
  quality_reporter: CHECK_CIRCLE_PATH,
  community_anchor:
    "M16 11c1.66 0 3-1.34 3-3s-1.34-3-3-3-3 1.34-3 3 1.34 3 3 3Zm-8 0c1.66 0 3-1.34 3-3S9.66 5 8 5 5 6.34 5 8s1.34 3 3 3Zm0 2c-2.33 0-7 1.17-7 3.5V19h14v-2.5c0-2.33-4.67-3.5-7-3.5Zm8 0c-.29 0-.62.02-.97.05C16.19 13.89 17 14.6 17 16.5V19h6v-2.5c0-2.33-4.67-3.5-7-3.5Z",
  verified_by_coordinator:
    "M12 1L3 5v6c0 5.55 3.84 10.74 9 12 5.16-1.26 9-6.45 9-12V5l-9-4Zm-2 16l-4-4 1.41-1.41L10 14.17l6.59-6.59L18 9l-8 8Z",
  first_on_scene: "M7 2v11h3v9l7-12h-4l4-8H7Z",
  area_mapper:
    "M20.5 3l-.16.03L15 5.1 9 3 3.36 4.9c-.21.07-.36.25-.36.48V20.5c0 .28.22.5.5.5l.16-.03L9 18.9l6 2.1 5.64-1.9c.21-.07.36-.25.36-.48V3.5c0-.28-.22-.5-.5-.5ZM15 19l-6-2.11V5l6 2.11V19Z",
  active_responder:
    "M10 2c0 0 3 4 3 7 0 1.66-1.34 3-3 3S7 10.66 7 9c0 0-2 2-2 4 0 2.76 2.24 5 5 5s5-2.24 5-5C15 8 10 2 10 2Z",
};

export function BadgeIcon({ slug, size = 16 }: { slug: string; size?: number }) {
  const d = BADGE_ICONS[slug] ?? BADGE_ICONS.first_report;
  return (
    <svg width={size} height={size} viewBox="0 0 20 20" fill="currentColor" aria-hidden="true">
      <path d={d} />
    </svg>
  );
}
