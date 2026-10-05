import type { TFunction } from "i18next";

type Unit = "justNow" | "minutesAgo" | "hoursAgo" | "daysAgo";

// English fallbacks for the draft card's keys; the `myReports.*` calls pass none.
const DRAFT_DEFAULTS: Record<Unit, string> = {
  justNow: "just now",
  minutesAgo: "{{count}}m ago",
  hoursAgo: "{{count}}h ago",
  daysAgo: "{{count}}d ago",
};

/** "just now" / "5m ago" / "3h ago" / "2d ago", from the `${prefix}.*` keys. */
export function formatRelativeTime(
  ts: number,
  t: TFunction,
  prefix: "draft" | "myReports",
): string {
  const label = (unit: Unit, count?: number): string => {
    const key = `${prefix}.${unit}`;
    if (prefix === "draft") {
      return count === undefined
        ? t(key, { defaultValue: DRAFT_DEFAULTS[unit] })
        : t(key, { count, defaultValue: DRAFT_DEFAULTS[unit] });
    }
    return count === undefined ? t(key) : t(key, { count });
  };
  const min = Math.floor((Date.now() - ts) / 60_000);
  if (min < 1) return label("justNow");
  if (min < 60) return label("minutesAgo", min);
  const hr = Math.floor(min / 60);
  if (hr < 24) return label("hoursAgo", hr);
  return label("daysAgo", Math.floor(hr / 24));
}
