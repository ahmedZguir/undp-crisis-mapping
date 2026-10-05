// IndexedDB backend for web/PWA.

import { type DBSchema, type IDBPDatabase, openDB } from "idb";
import type { FormState, ReportPayload } from "../../types";
import {
  type OutboxResult,
  type OutboxRow,
  type OutboxStatus,
  isReady,
  nextOutboxState,
  patchPayload,
  resetBackoffRow,
} from "../outbox-core";
import { LOCAL_DB_NAME, MAX_REPORT_THUMBS, TTL } from "../storeConfig";
import type { DbBackend } from "./backend";
import { publishChange } from "./notify";
import {
  type DraftRow,
  type MetaRow,
  type PhotoRow,
  type ReportThumbRow,
  type SubmissionRow,
  draftFromFailedRow,
} from "./types";

interface UndpDb extends DBSchema {
  drafts: {
    key: string;
    value: DraftRow;
    indexes: { "by-updated": number };
  };
  outbox: {
    key: string;
    value: OutboxRow;
    indexes: { "by-status": OutboxStatus; "by-updated": number };
  };
  photos: {
    key: string;
    value: PhotoRow;
  };
  meta: {
    key: string;
    value: MetaRow;
  };
  submissions: {
    key: string;
    value: SubmissionRow;
    indexes: { "by-submitted": number };
  };
  report_thumbs: {
    key: string;
    value: ReportThumbRow;
    indexes: { "by-created": number };
  };
}

const DB_VERSION = 3;
const LEGACY_KEYVAL_DB = "keyval-store";
const LEGACY_KEYVAL_STORE = "keyval";
const LEGACY_DRAFT_KEY = "current-draft";

let dbPromise: Promise<IDBPDatabase<UndpDb>> | null = null;

function getDb(): Promise<IDBPDatabase<UndpDb>> {
  if (!dbPromise) {
    dbPromise = openDB<UndpDb>(LOCAL_DB_NAME, DB_VERSION, {
      upgrade(db, oldVersion) {
        if (!db.objectStoreNames.contains("drafts")) {
          const drafts = db.createObjectStore("drafts", { keyPath: "id" });
          drafts.createIndex("by-updated", "updated_at");
        }
        if (!db.objectStoreNames.contains("outbox")) {
          const outbox = db.createObjectStore("outbox", { keyPath: "id" });
          outbox.createIndex("by-status", "status");
          outbox.createIndex("by-updated", "updated_at");
        }
        if (!db.objectStoreNames.contains("photos")) {
          db.createObjectStore("photos", { keyPath: "id" });
        }
        if (!db.objectStoreNames.contains("meta")) {
          db.createObjectStore("meta", { keyPath: "key" });
        }
        // Unused store, kept so the DB version and schema don't change.
        if (!db.objectStoreNames.contains("submissions")) {
          const submissions = db.createObjectStore("submissions", {
            keyPath: "client_submission_id",
          });
          submissions.createIndex("by-submitted", "submitted_at");
        }
        if (!db.objectStoreNames.contains("report_thumbs")) {
          // Added in v3; the guard covers both a fresh DB and a v2→v3 upgrade.
          const thumbs = db.createObjectStore("report_thumbs", { keyPath: "report_id" });
          thumbs.createIndex("by-created", "created_at");
        }
        if (oldVersion < 1) {
          // Fresh DB: drop pre-multi-draft state from the legacy idb-keyval DB (independent of this tx).
          void dropLegacyDraftKey();
        }
      },
    });
  }
  return dbPromise;
}

async function dropLegacyDraftKey(): Promise<void> {
  try {
    await new Promise<void>((resolve) => {
      const req = indexedDB.open(LEGACY_KEYVAL_DB);
      req.onerror = () => resolve();
      req.onupgradeneeded = () => {
        // So the delete below can't throw on a fresh DB.
        try {
          if (!req.result.objectStoreNames.contains(LEGACY_KEYVAL_STORE)) {
            req.result.createObjectStore(LEGACY_KEYVAL_STORE);
          }
        } catch {
          // ignore
        }
      };
      req.onsuccess = () => {
        const db = req.result;
        if (!db.objectStoreNames.contains(LEGACY_KEYVAL_STORE)) {
          db.close();
          resolve();
          return;
        }
        const tx = db.transaction(LEGACY_KEYVAL_STORE, "readwrite");
        tx.objectStore(LEGACY_KEYVAL_STORE).delete(LEGACY_DRAFT_KEY);
        tx.oncomplete = () => {
          db.close();
          resolve();
        };
        tx.onerror = () => {
          db.close();
          resolve();
        };
      };
    });
  } catch {
    // best-effort
  }
}

export const idbBackend: DbBackend = {
  // Meta ------------------------------------------------------------------
  async getMeta<T = unknown>(key: string): Promise<T | undefined> {
    const db = await getDb();
    const row = await db.get("meta", key);
    return row?.value as T | undefined;
  },

  async setMeta(key: string, value: unknown): Promise<void> {
    const db = await getDb();
    await db.put("meta", { key, value });
  },

  // Drafts ----------------------------------------------------------------
  async loadDrafts(): Promise<DraftRow[]> {
    const db = await getDb();
    const all = await db.getAll("drafts");
    return all.sort((a, b) => b.updated_at - a.updated_at);
  },

  async getDraft(id: string): Promise<DraftRow | undefined> {
    const db = await getDb();
    return db.get("drafts", id);
  },

  async saveDraft(row: DraftRow): Promise<void> {
    const db = await getDb();
    await db.put("drafts", row);
    publishChange({ kind: "drafts" });
  },

  async deleteDraft(id: string): Promise<void> {
    const db = await getDb();
    const existing = await db.get("drafts", id);
    if (!existing) return;
    const tx = db.transaction(["drafts", "photos"], "readwrite");
    await tx.objectStore("drafts").delete(id);
    if (existing.photo_id) {
      await tx.objectStore("photos").delete(existing.photo_id);
    }
    await tx.done;
    publishChange({ kind: "drafts" });
  },

  // Photos ----------------------------------------------------------------
  async putPhoto(blob: Blob): Promise<string> {
    const db = await getDb();
    const id = crypto.randomUUID();
    // ArrayBuffer, not Blob: see PhotoRow (Safari Private Browsing).
    const data = await blob.arrayBuffer();
    await db.put("photos", { id, data, type: blob.type, created_at: Date.now() });
    return id;
  },

  async getPhoto(id: string): Promise<Blob | null> {
    const db = await getDb();
    const row = await db.get("photos", id);
    if (!row) return null;
    // Legacy rows from before the ArrayBuffer switch may still hold a `blob`.
    const legacy = (row as { blob?: Blob }).blob;
    if (legacy) return legacy;
    return new Blob([row.data], { type: row.type });
  },

  async deletePhoto(id: string): Promise<void> {
    const db = await getDb();
    await db.delete("photos", id);
  },

  // Report thumbnails -----------------------------------------------------
  async putReportThumb(reportId: string, blob: Blob): Promise<void> {
    const db = await getDb();
    const data = await blob.arrayBuffer();
    const tx = db.transaction("report_thumbs", "readwrite");
    const store = tx.objectStore("report_thumbs");
    await store.put({ report_id: reportId, data, type: blob.type, created_at: Date.now() });
    const count = await store.count();
    if (count > MAX_REPORT_THUMBS) {
      let toDelete = count - MAX_REPORT_THUMBS;
      let cursor = await store.index("by-created").openCursor();
      while (cursor && toDelete > 0) {
        await cursor.delete();
        toDelete -= 1;
        cursor = await cursor.continue();
      }
    }
    await tx.done;
  },

  async getReportThumb(reportId: string): Promise<Blob | null> {
    const db = await getDb();
    const row = await db.get("report_thumbs", reportId);
    if (!row) return null;
    return new Blob([row.data], { type: row.type });
  },

  // Outbox ----------------------------------------------------------------
  async loadOutbox(): Promise<OutboxRow[]> {
    const db = await getDb();
    const all = await db.getAll("outbox");
    return all.sort((a, b) => a.created_at - b.created_at);
  },

  async getOutboxRow(id: string): Promise<OutboxRow | undefined> {
    const db = await getDb();
    return db.get("outbox", id);
  },

  async putOutboxRow(row: OutboxRow): Promise<void> {
    const db = await getDb();
    await db.put("outbox", row);
    publishChange({ kind: "outbox" });
  },

  async promoteDraftToOutbox(draftId: string, outboxRow: OutboxRow): Promise<void> {
    const db = await getDb();
    const tx = db.transaction(["drafts", "outbox"], "readwrite");
    await tx.objectStore("drafts").delete(draftId);
    await tx.objectStore("outbox").put(outboxRow);
    await tx.done;
    publishChange({ kind: "drafts+outbox" });
  },

  async resetQueuedBackoff(): Promise<number> {
    const db = await getDb();
    const tx = db.transaction("outbox", "readwrite");
    const store = tx.objectStore("outbox");
    const all = await store.getAll();
    const now = Date.now();
    let touched = 0;
    for (const row of all) {
      const reset = resetBackoffRow(row, now);
      if (!reset) continue;
      await store.put(reset);
      touched += 1;
    }
    await tx.done;
    if (touched > 0) publishChange({ kind: "outbox" });
    return touched;
  },

  // Atomic CAS queued → submitting; null if the row isn't queued and ready.
  async claimOutboxRow(id: string): Promise<OutboxRow | null> {
    const db = await getDb();
    const tx = db.transaction("outbox", "readwrite");
    const store = tx.objectStore("outbox");
    const row = await store.get(id);
    if (!row || !isReady(row, Date.now())) {
      await tx.done;
      return null;
    }
    const updated: OutboxRow = {
      ...row,
      status: "submitting",
      updated_at: Date.now(),
    };
    await store.put(updated);
    await tx.done;
    publishChange({ kind: "outbox" });
    return updated;
  },

  async markOutboxResult(
    id: string,
    result: OutboxResult,
    opts: { maxAttempts: number; backoff: (attempt: number) => number },
  ): Promise<void> {
    const db = await getDb();
    const tx = db.transaction(["outbox", "photos"], "readwrite");
    const store = tx.objectStore("outbox");
    const row = await store.get(id);
    if (!row) {
      await tx.done;
      return;
    }
    const transition = nextOutboxState(row, result, { ...opts, now: Date.now() });
    if (transition.kind === "delete") {
      await store.delete(id);
      if (row.photo_id) await tx.objectStore("photos").delete(row.photo_id);
    } else {
      await store.put(transition.row);
    }
    await tx.done;
    publishChange({ kind: "outbox" });
  },

  // A submitting row can't be cancelled.
  async cancelOutboxRow(id: string): Promise<boolean> {
    const db = await getDb();
    const tx = db.transaction(["outbox", "photos"], "readwrite");
    const store = tx.objectStore("outbox");
    const row = await store.get(id);
    if (!row) {
      await tx.done;
      return false;
    }
    if (row.status === "submitting") {
      await tx.done;
      return false;
    }
    await store.delete(id);
    if (row.photo_id) await tx.objectStore("photos").delete(row.photo_id);
    await tx.done;
    publishChange({ kind: "outbox" });
    return true;
  },

  async editFailedOutboxBackToDraft(
    id: string,
    rebuildState: (row: OutboxRow) => Omit<FormState, "photo">,
  ): Promise<DraftRow | null> {
    const db = await getDb();
    const tx = db.transaction(["outbox", "drafts"], "readwrite");
    const outbox = tx.objectStore("outbox");
    const row = await outbox.get(id);
    if (!row || row.status !== "failed") {
      await tx.done;
      return null;
    }
    const draft = draftFromFailedRow(row, rebuildState, Date.now());
    await tx.objectStore("drafts").put(draft);
    await outbox.delete(id);
    await tx.done;
    publishChange({ kind: "drafts+outbox" });
    return draft;
  },

  async patchOutboxPayload(
    id: string,
    patch: Partial<Omit<ReportPayload, "photo">>,
  ): Promise<void> {
    const db = await getDb();
    const tx = db.transaction("outbox", "readwrite");
    const store = tx.objectStore("outbox");
    const row = await store.get(id);
    if (!row) {
      await tx.done;
      return;
    }
    await store.put(patchPayload(row, patch, Date.now()));
    await tx.done;
    publishChange({ kind: "outbox" });
  },

  // Maintenance -----------------------------------------------------------
  // Leaves meta (client id) untouched.
  async clearAllReports(): Promise<void> {
    const db = await getDb();
    const tx = db.transaction(
      ["drafts", "outbox", "photos", "submissions", "report_thumbs"],
      "readwrite",
    );
    await tx.objectStore("drafts").clear();
    await tx.objectStore("outbox").clear();
    await tx.objectStore("photos").clear();
    await tx.objectStore("submissions").clear();
    await tx.objectStore("report_thumbs").clear();
    await tx.done;
    publishChange({ kind: "drafts+outbox" });
  },

  async pruneExpired(
    now: number = Date.now(),
  ): Promise<{ draftsPruned: number; outboxFailedPruned: number }> {
    const db = await getDb();
    let draftsPruned = 0;
    let outboxFailedPruned = 0;

    const draftTx = db.transaction(["drafts", "photos"], "readwrite");
    const drafts = await draftTx.objectStore("drafts").getAll();
    for (const d of drafts) {
      if (now - d.updated_at > TTL.DRAFT_MS) {
        await draftTx.objectStore("drafts").delete(d.id);
        if (d.photo_id) await draftTx.objectStore("photos").delete(d.photo_id);
        draftsPruned += 1;
      }
    }
    await draftTx.done;

    const outboxTx = db.transaction(["outbox", "photos"], "readwrite");
    const outbox = await outboxTx.objectStore("outbox").getAll();
    for (const o of outbox) {
      if (o.status === "failed" && now - o.updated_at > TTL.OUTBOX_FAILED_MS) {
        await outboxTx.objectStore("outbox").delete(o.id);
        if (o.photo_id) await outboxTx.objectStore("photos").delete(o.photo_id);
        outboxFailedPruned += 1;
      }
    }
    await outboxTx.done;

    if (draftsPruned > 0 || outboxFailedPruned > 0) {
      publishChange({ kind: "drafts+outbox" });
    }
    return { draftsPruned, outboxFailedPruned };
  },

  // Lifecycle -------------------------------------------------------------
  async reset(): Promise<void> {
    if (!dbPromise) return;
    try {
      const db = await dbPromise;
      db.close();
    } catch {
      // ignore
    }
    dbPromise = null;
  },
};
