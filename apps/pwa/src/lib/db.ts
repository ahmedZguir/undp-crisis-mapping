// Local store facade: IndexedDB on web, SQLite on native (eviction-durable). See `db/backend.ts`.

import { isNativePlatform } from "../platform/platformInfo";
import type { DbBackend } from "./db/backend";
import { idbBackend } from "./db/idbBackend";
import { resetChannel } from "./db/notify";
import { getCapacitorExecutor } from "./db/sql/capacitorExecutor";
import { createSqliteBackend } from "./db/sqliteBackend";

export type { OutboxRow } from "./outbox-core";
export type { DraftRow } from "./db/types";

// The SQLite executor lazy-imports the Capacitor plugin, so it never enters the web bundle.
const backend: DbBackend = isNativePlatform()
  ? createSqliteBackend(getCapacitorExecutor)
  : idbBackend;

export const {
  getMeta,
  setMeta,
  loadDrafts,
  getDraft,
  saveDraft,
  deleteDraft,
  putPhoto,
  getPhoto,
  deletePhoto,
  putReportThumb,
  getReportThumb,
  loadOutbox,
  getOutboxRow,
  putOutboxRow,
  promoteDraftToOutbox,
  resetQueuedBackoff,
  claimOutboxRow,
  markOutboxResult,
  cancelOutboxRow,
  editFailedOutboxBackToDraft,
  patchOutboxPayload,
  clearAllReports,
  pruneExpired,
} = backend;

// Web-only; called on first photo write so the user has some investment before any prompt.
let persistRequested = false;
export async function requestPersistentStorageOnce(): Promise<void> {
  if (persistRequested) return;
  persistRequested = true;
  try {
    if (
      typeof navigator !== "undefined" &&
      navigator.storage &&
      typeof navigator.storage.persist === "function"
    ) {
      await navigator.storage.persist();
    }
  } catch {
    // best-effort
  }
}

export async function _resetForTests(): Promise<void> {
  resetChannel();
  await backend.reset();
  persistRequested = false;
}
