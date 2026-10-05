// My Reports tab dot and per-row "new" markers, by diffing current state against a seen baseline
// in localStorage. `submitting` counts as queued for colour and seen-tracking.

import { isPending } from "./outbox-core";
import { readJson, writeJson } from "./safeStorage";
import { STORAGE_EVENTS, STORAGE_KEYS } from "./storageKeys";

export const REPORT_NOTICE_CHANGED_EVENT = STORAGE_EVENTS.reportNoticeChanged;

const REPORT_NOTICE_SEEN_KEY = STORAGE_KEYS.reportNoticeSeen.key;

export interface SeenBaseline {
  submittedIds: string[]; // server report ids already seen
  badgeSlugs: string[]; // badge slugs already seen
  queuedIds: string[]; // outbox rowIds already seen in queued/submitting
  failedIds: string[]; // outbox rowIds already seen as failed
  draftIds: string[]; // draft ids already seen
}

export interface NoticeInputs {
  submittedIds: string[];
  badgeSlugs: string[];
  outbox: { id: string; status: string }[];
  draftIds: string[];
}

export interface UnseenSets {
  submitted: Set<string>;
  badges: Set<string>;
  queued: Set<string>;
  failed: Set<string>;
  drafts: Set<string>;
}

export type TabBadgeColor = "red" | "yellow" | "green";

/** Fill colour per badge/dot colour (TabBar badge, My Reports "new" dot). */
export const TAB_BADGE_BG: Record<TabBadgeColor, string> = {
  red: "var(--c-danger)",
  yellow: "var(--c-warn)",
  green: "var(--color-success)",
};
export type TabBadge = { color: TabBadgeColor; count: number } | null;

export function readSeen(): SeenBaseline | null {
  const parsed = readJson<Partial<SeenBaseline>>(REPORT_NOTICE_SEEN_KEY);
  if (parsed === null) return null;
  return {
    submittedIds: parsed.submittedIds ?? [],
    badgeSlugs: parsed.badgeSlugs ?? [],
    queuedIds: parsed.queuedIds ?? [],
    failedIds: parsed.failedIds ?? [],
    draftIds: parsed.draftIds ?? [],
  };
}

function writeSeen(baseline: SeenBaseline): void {
  writeJson(REPORT_NOTICE_SEEN_KEY, baseline); // best-effort
  if (typeof window !== "undefined") {
    try {
      window.dispatchEvent(new CustomEvent(REPORT_NOTICE_CHANGED_EVENT));
    } catch {
      // best-effort
    }
  }
}

function currentBaseline(inputs: NoticeInputs): SeenBaseline {
  return {
    submittedIds: [...inputs.submittedIds],
    badgeSlugs: [...inputs.badgeSlugs],
    queuedIds: inputs.outbox.filter((r) => isPending(r.status)).map((r) => r.id),
    failedIds: inputs.outbox.filter((r) => r.status === "failed").map((r) => r.id),
    draftIds: [...inputs.draftIds],
  };
}

export function computeUnseen(inputs: NoticeInputs, seen: SeenBaseline): UnseenSets {
  const seenSubmitted = new Set(seen.submittedIds);
  const seenBadges = new Set(seen.badgeSlugs);
  const seenQueued = new Set(seen.queuedIds);
  const seenFailed = new Set(seen.failedIds);
  const seenDrafts = new Set(seen.draftIds);

  const queued = new Set<string>();
  const failed = new Set<string>();
  for (const r of inputs.outbox) {
    if (r.status === "failed") {
      if (!seenFailed.has(r.id)) failed.add(r.id);
    } else if (isPending(r.status) && !seenQueued.has(r.id)) {
      queued.add(r.id);
    }
  }

  return {
    submitted: new Set(inputs.submittedIds.filter((id) => !seenSubmitted.has(id))),
    badges: new Set(inputs.badgeSlugs.filter((s) => !seenBadges.has(s))),
    queued,
    failed,
    drafts: new Set(inputs.draftIds.filter((id) => !seenDrafts.has(id))),
  };
}

// Priority red(failed) > yellow(queued) > green(submitted+badges). Drafts never drive the tab dot.
export function badgeFromUnseen(u: UnseenSets): TabBadge {
  if (u.failed.size > 0) return { color: "red", count: u.failed.size };
  if (u.queued.size > 0) return { color: "yellow", count: u.queued.size };
  const green = u.submitted.size + u.badges.size;
  if (green > 0) return { color: "green", count: green };
  return null;
}

export function markSeen(inputs: NoticeInputs): void {
  writeSeen(currentBaseline(inputs));
}
