// Tunables and shared names for the local store.

// Queued outbox rows never expire; only drafts and failed rows have a TTL.
// The retry policy lives in `outbox-core.ts`.
export const TTL = {
  DRAFT_MS: 30 * 24 * 60 * 60 * 1000,
  OUTBOX_FAILED_MS: 30 * 24 * 60 * 60 * 1000,
} as const;

// `public/sw-outbox.js` hard-codes this name, SYNC_TAG and STORES_CHANNEL — keep in sync.
export const LOCAL_DB_NAME = "undp-app";

export const STORES_CHANNEL = "undp-stores";
export const SYNC_TAG = "outbox-flush";

// Oldest pruned on each write; ~50 ≤320px thumbs is well under a megabyte.
export const MAX_REPORT_THUMBS = 50;
