// DB rows and the API are UTC. The coordinator picks a display timezone
// (browser default, overridable in the topbar); every time-shaped admin widget
// renders and parses through this module.

import { formatInTimeZone, fromZonedTime } from "date-fns-tz";
import { STORAGE_KEYS } from "./storageKeys";

const STORAGE_KEY = STORAGE_KEYS.adminDisplayTimezone.key;
const EVENT_NAME = "admin-display-timezone-change";

export function getBrowserTimezone(): string {
  return Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC";
}

export function getStoredTimezone(): string | null {
  try {
    return window.localStorage.getItem(STORAGE_KEY);
  } catch {
    return null;
  }
}

export function getEffectiveTimezone(): string {
  return getStoredTimezone() ?? getBrowserTimezone();
}

export function setStoredTimezone(tz: string | null): void {
  try {
    if (tz === null) window.localStorage.removeItem(STORAGE_KEY);
    else window.localStorage.setItem(STORAGE_KEY, tz);
  } catch {
    // Quota or privacy mode: the in-memory value still applies for this tab.
  }
  window.dispatchEvent(new CustomEvent(EVENT_NAME));
}

export function subscribeTimezone(listener: () => void): () => void {
  window.addEventListener(EVENT_NAME, listener);
  // The storage event fires in *other* tabs, so a second admin tab follows along.
  const onStorage = (e: StorageEvent) => {
    if (e.key === STORAGE_KEY) listener();
  };
  window.addEventListener("storage", onStorage);
  return () => {
    window.removeEventListener(EVENT_NAME, listener);
    window.removeEventListener("storage", onStorage);
  };
}

// --- Conversion helpers ---

/** UTC ISO to `YYYY-MM-DDTHH:mm` in `tz`, the `<input type="datetime-local">` shape. Empty for null. */
export function utcIsoToLocalInput(iso: string | null, tz: string): string {
  if (iso === null || iso === "") return "";
  return formatInTimeZone(new Date(iso), tz, "yyyy-MM-dd'T'HH:mm");
}

/** Parse a `YYYY-MM-DDTHH:mm` string from a datetime-local input, treating
 *  its digits as wall-clock time in `tz`, and return a UTC ISO string. */
export function localInputToUtcIso(local: string, tz: string): string | null {
  if (!local) return null;
  return fromZonedTime(local, tz).toISOString();
}

/** "May 14, 2026, 9:00 AM Qatar time (UTC+3)". The year keeps old reports unambiguous. */
export function formatDisplay(iso: string, tz: string): string {
  return formatWithZone(iso, tz, "MMM d, yyyy, h:mm a");
}

/** `iso` in `tz` plus the zone suffix; the raw `iso` if it can't be formatted. */
function formatWithZone(iso: string, tz: string, pattern: string): string {
  try {
    const at = new Date(iso);
    const time = formatInTimeZone(at, tz, pattern);
    return `${time} ${zoneSuffix(tz, at)}`;
  } catch {
    return iso;
  }
}

/** Compact stamp for table cells: "14 May, 09:00". No zone; pair with `formatFull` in the title. */
export function formatCompact(iso: string, tz: string): string {
  try {
    return formatInTimeZone(new Date(iso), tz, "d MMM, HH:mm");
  } catch {
    return iso;
  }
}

/** Full stamp with seconds and zone, for tooltips: "May 14, 2026, 9:00:42 AM Qatar time (UTC+3)". */
export function formatFull(iso: string, tz: string): string {
  return formatWithZone(iso, tz, "MMM d, yyyy, h:mm:ss a");
}

/** Picker label, e.g. "Asia/Qatar (UTC+03)". */
export function timezoneOptionLabel(tz: string): string {
  const offset = formatInTimeZone(new Date(), tz, "xxx");
  return `${tz} (UTC${offset})`;
}

/** Zone suffix, e.g. "Qatar time (UTC+3)". A bare city reads as a place (it was
 *  mistaken for the photo location), hence "time" and the offset. The offset is
 *  resolved at the stamp's own instant so DST is handled. "UTC" stays bare. */
function zoneSuffix(tz: string, at: Date = new Date()): string {
  const label = shortZoneLabel(tz);
  if (label === "UTC") return label;
  return `${label} time (UTC${utcOffsetLabel(tz, at)})`;
}

/** The zone's offset at `at`, trimmed for readability: "+3", "-5", "+5:30". */
function utcOffsetLabel(tz: string, at: Date): string {
  // date-fns "xxx" gives a padded ISO offset like "+03:00" / "+05:30" / "-04:00".
  const raw = formatInTimeZone(at, tz, "xxx");
  const m = raw.match(/^([+-])(\d{2}):(\d{2})$/);
  if (!m) return raw;
  const [, sign, hh, mm] = m;
  const hours = String(Number(hh));
  return mm === "00" ? `${sign}${hours}` : `${sign}${hours}:${mm}`;
}

/** City portion of an IANA zone (e.g. 'Asia/Qatar' → 'Qatar'). */
export function shortZoneLabel(tz: string): string {
  const tail = tz.includes("/") ? tz.split("/").slice(-1)[0] : tz;
  return tail.replace(/_/g, " ");
}

/** Curated IANA zones for the picker. The full ~400-entry database is too big a
 *  bundle cost for one dropdown; the picker also accepts free-text entry. */
export const COMMON_TIMEZONES: readonly string[] = [
  "UTC",
  "Europe/London",
  "Europe/Paris",
  "Europe/Berlin",
  "Europe/Istanbul",
  "Africa/Cairo",
  "Africa/Nairobi",
  "Asia/Beirut",
  "Asia/Riyadh",
  "Asia/Qatar",
  "Asia/Dubai",
  "Asia/Tehran",
  "Asia/Karachi",
  "Asia/Kolkata",
  "Asia/Dhaka",
  "Asia/Bangkok",
  "Asia/Jakarta",
  "Asia/Manila",
  "Asia/Singapore",
  "Asia/Hong_Kong",
  "Asia/Tokyo",
  "Australia/Sydney",
  "Pacific/Auckland",
  "America/New_York",
  "America/Chicago",
  "America/Denver",
  "America/Los_Angeles",
  "America/Mexico_City",
  "America/Sao_Paulo",
  "America/Buenos_Aires",
];
