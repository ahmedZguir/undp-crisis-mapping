// Storage backend contract behind the `db.ts` facade (IndexedDB on web, SQLite on native).
// Backends own their transactions and publish store changes via `db/notify.ts`.

import type { FormState, ReportPayload } from "../../types";
import type { OutboxResult, OutboxRow } from "../outbox-core";
import type { DraftRow } from "./types";

export interface DbBackend {
  // Meta (key/value; holds client_id among others)
  getMeta<T = unknown>(key: string): Promise<T | undefined>;
  setMeta(key: string, value: unknown): Promise<void>;

  // Drafts
  loadDrafts(): Promise<DraftRow[]>;
  getDraft(id: string): Promise<DraftRow | undefined>;
  saveDraft(row: DraftRow): Promise<void>;
  deleteDraft(id: string): Promise<void>;

  // Photos
  putPhoto(blob: Blob): Promise<string>;
  getPhoto(id: string): Promise<Blob | null>;
  deletePhoto(id: string): Promise<void>;

  // Submitted-report thumbs for offline My Reports, keyed by server report id; pruned to MAX_REPORT_THUMBS.
  putReportThumb(reportId: string, blob: Blob): Promise<void>;
  getReportThumb(reportId: string): Promise<Blob | null>;

  // Outbox
  loadOutbox(): Promise<OutboxRow[]>;
  getOutboxRow(id: string): Promise<OutboxRow | undefined>;
  putOutboxRow(row: OutboxRow): Promise<void>;
  promoteDraftToOutbox(draftId: string, outboxRow: OutboxRow): Promise<void>;
  resetQueuedBackoff(): Promise<number>;
  claimOutboxRow(id: string): Promise<OutboxRow | null>;
  markOutboxResult(
    id: string,
    result: OutboxResult,
    opts: { maxAttempts: number; backoff: (attempt: number) => number },
  ): Promise<void>;
  cancelOutboxRow(id: string): Promise<boolean>;
  editFailedOutboxBackToDraft(
    id: string,
    rebuildState: (row: OutboxRow) => Omit<FormState, "photo">,
  ): Promise<DraftRow | null>;
  patchOutboxPayload(id: string, patch: Partial<Omit<ReportPayload, "photo">>): Promise<void>;

  // Maintenance
  clearAllReports(): Promise<void>;
  pruneExpired(now?: number): Promise<{ draftsPruned: number; outboxFailedPruned: number }>;

  // Used by `_resetForTests`.
  reset(): Promise<void>;
}
