// Native SQLite backend mirroring idbBackend, so local data survives OS storage eviction.
// Rows are JSON in a `data` column, with filter/sort fields duplicated into real columns;
// photos are data: URLs so Blobs can be rebuilt without a blob driver.

import type { FormState, ReportPayload } from "../../types";
import { base64ToBytes, bytesToBase64 } from "../base64";
import {
  type OutboxResult,
  type OutboxRow,
  isReady,
  nextOutboxState,
  patchPayload,
  resetBackoffRow,
} from "../outbox-core";
import { MAX_REPORT_THUMBS, TTL } from "../storeConfig";
import type { DbBackend } from "./backend";
import { publishChange } from "./notify";
import type { SqlExecutor } from "./sql/executor";
import { type DraftRow, draftFromFailedRow } from "./types";

const SCHEMA: readonly string[] = [
  "CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)",
  "CREATE TABLE IF NOT EXISTS drafts (id TEXT PRIMARY KEY, data TEXT NOT NULL, updated_at INTEGER NOT NULL)",
  "CREATE TABLE IF NOT EXISTS photos (id TEXT PRIMARY KEY, data_url TEXT NOT NULL, created_at INTEGER NOT NULL)",
  "CREATE TABLE IF NOT EXISTS outbox (id TEXT PRIMARY KEY, data TEXT NOT NULL, status TEXT NOT NULL, created_at INTEGER NOT NULL, updated_at INTEGER NOT NULL, next_attempt_at INTEGER NOT NULL)",
  // Unused table, kept for schema stability (mirrors the idb `submissions` store).
  "CREATE TABLE IF NOT EXISTS submissions (client_submission_id TEXT PRIMARY KEY, data TEXT NOT NULL, submitted_at INTEGER NOT NULL)",
  "CREATE TABLE IF NOT EXISTS report_thumbs (report_id TEXT PRIMARY KEY, data_url TEXT NOT NULL, created_at INTEGER NOT NULL)",
];

// Blob <-> data URL ---------------------------------------------------------

async function blobToDataUrl(blob: Blob): Promise<string> {
  const bytes = new Uint8Array(await blob.arrayBuffer());
  const mime = blob.type || "application/octet-stream";
  return `data:${mime};base64,${bytesToBase64(bytes)}`;
}

function dataUrlToBlob(url: string): Blob {
  const comma = url.indexOf(",");
  const meta = url.slice(0, comma);
  const b64 = url.slice(comma + 1);
  const mime = /data:([^;]+)/.exec(meta)?.[1] ?? "application/octet-stream";
  const bytes = base64ToBytes(b64);
  return new Blob([bytes as BlobPart], { type: mime });
}

// Row mapping ---------------------------------------------------------------

function parseRow<T>(row: { data: string } | undefined): T | undefined {
  return row ? (JSON.parse(row.data) as T) : undefined;
}

function parseRows<T>(rows: { data: string }[]): T[] {
  return rows.map((r) => JSON.parse(r.data) as T);
}

async function upsertOutbox(exec: SqlExecutor, row: OutboxRow): Promise<void> {
  await exec.run(
    "INSERT OR REPLACE INTO outbox (id, data, status, created_at, updated_at, next_attempt_at) VALUES (?, ?, ?, ?, ?, ?)",
    [row.id, JSON.stringify(row), row.status, row.created_at, row.updated_at, row.next_attempt_at],
  );
}

async function upsertDraft(exec: SqlExecutor, row: DraftRow): Promise<void> {
  await exec.run("INSERT OR REPLACE INTO drafts (id, data, updated_at) VALUES (?, ?, ?)", [
    row.id,
    JSON.stringify(row),
    row.updated_at,
  ]);
}

async function deletePhotoRow(exec: SqlExecutor, id: string): Promise<void> {
  await exec.run("DELETE FROM photos WHERE id = ?", [id]);
}

async function getOutbox(exec: SqlExecutor, id: string): Promise<OutboxRow | undefined> {
  return parseRow<OutboxRow>(
    await exec.get<{ data: string }>("SELECT data FROM outbox WHERE id = ?", [id]),
  );
}

// Factory -------------------------------------------------------------------

// `getExecutor` is lazy so the native plugin is only imported on device.
export function createSqliteBackend(getExecutor: () => Promise<SqlExecutor>): DbBackend {
  let execPromise: Promise<SqlExecutor> | null = null;

  function exec(): Promise<SqlExecutor> {
    if (!execPromise) {
      execPromise = (async () => {
        const e = await getExecutor();
        for (const stmt of SCHEMA) await e.run(stmt);
        return e;
      })();
    }
    return execPromise;
  }

  return {
    // Meta ----------------------------------------------------------------
    async getMeta<T = unknown>(key: string): Promise<T | undefined> {
      const e = await exec();
      const row = await e.get<{ value: string }>("SELECT value FROM meta WHERE key = ?", [key]);
      return row ? (JSON.parse(row.value) as T) : undefined;
    },

    async setMeta(key: string, value: unknown): Promise<void> {
      const e = await exec();
      await e.run("INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)", [
        key,
        JSON.stringify(value),
      ]);
    },

    // Drafts --------------------------------------------------------------
    async loadDrafts(): Promise<DraftRow[]> {
      const e = await exec();
      return parseRows<DraftRow>(
        await e.all<{ data: string }>("SELECT data FROM drafts ORDER BY updated_at DESC"),
      );
    },

    async getDraft(id: string): Promise<DraftRow | undefined> {
      const e = await exec();
      return parseRow<DraftRow>(
        await e.get<{ data: string }>("SELECT data FROM drafts WHERE id = ?", [id]),
      );
    },

    async saveDraft(row: DraftRow): Promise<void> {
      const e = await exec();
      await upsertDraft(e, row);
      publishChange({ kind: "drafts" });
    },

    async deleteDraft(id: string): Promise<void> {
      const e = await exec();
      const existing = await e.get<{ data: string }>("SELECT data FROM drafts WHERE id = ?", [id]);
      if (!existing) return;
      const draft = JSON.parse(existing.data) as DraftRow;
      await e.tx(async () => {
        await e.run("DELETE FROM drafts WHERE id = ?", [id]);
        if (draft.photo_id) await deletePhotoRow(e, draft.photo_id);
      });
      publishChange({ kind: "drafts" });
    },

    // Photos --------------------------------------------------------------
    async putPhoto(blob: Blob): Promise<string> {
      const e = await exec();
      const id = crypto.randomUUID();
      await e.run("INSERT OR REPLACE INTO photos (id, data_url, created_at) VALUES (?, ?, ?)", [
        id,
        await blobToDataUrl(blob),
        Date.now(),
      ]);
      return id;
    },

    async getPhoto(id: string): Promise<Blob | null> {
      const e = await exec();
      const row = await e.get<{ data_url: string }>("SELECT data_url FROM photos WHERE id = ?", [
        id,
      ]);
      return row ? dataUrlToBlob(row.data_url) : null;
    },

    async putReportThumb(reportId: string, blob: Blob): Promise<void> {
      const e = await exec();
      await e.run(
        "INSERT OR REPLACE INTO report_thumbs (report_id, data_url, created_at) VALUES (?, ?, ?)",
        [reportId, await blobToDataUrl(blob), Date.now()],
      );
      await e.run(
        "DELETE FROM report_thumbs WHERE report_id NOT IN (SELECT report_id FROM report_thumbs ORDER BY created_at DESC LIMIT ?)",
        [MAX_REPORT_THUMBS],
      );
    },

    async getReportThumb(reportId: string): Promise<Blob | null> {
      const e = await exec();
      const row = await e.get<{ data_url: string }>(
        "SELECT data_url FROM report_thumbs WHERE report_id = ?",
        [reportId],
      );
      return row ? dataUrlToBlob(row.data_url) : null;
    },

    async deletePhoto(id: string): Promise<void> {
      const e = await exec();
      await deletePhotoRow(e, id);
    },

    // Outbox --------------------------------------------------------------
    async loadOutbox(): Promise<OutboxRow[]> {
      const e = await exec();
      return parseRows<OutboxRow>(
        await e.all<{ data: string }>("SELECT data FROM outbox ORDER BY created_at ASC"),
      );
    },

    async getOutboxRow(id: string): Promise<OutboxRow | undefined> {
      const e = await exec();
      return getOutbox(e, id);
    },

    async putOutboxRow(row: OutboxRow): Promise<void> {
      const e = await exec();
      await upsertOutbox(e, row);
      publishChange({ kind: "outbox" });
    },

    async promoteDraftToOutbox(draftId: string, outboxRow: OutboxRow): Promise<void> {
      const e = await exec();
      await e.tx(async () => {
        await e.run("DELETE FROM drafts WHERE id = ?", [draftId]);
        await upsertOutbox(e, outboxRow);
      });
      publishChange({ kind: "drafts+outbox" });
    },

    async resetQueuedBackoff(): Promise<number> {
      const e = await exec();
      const rows = parseRows<OutboxRow>(await e.all<{ data: string }>("SELECT data FROM outbox"));
      const now = Date.now();
      let touched = 0;
      await e.tx(async () => {
        for (const row of rows) {
          const reset = resetBackoffRow(row, now);
          if (!reset) continue;
          await upsertOutbox(e, reset);
          touched += 1;
        }
      });
      if (touched > 0) publishChange({ kind: "outbox" });
      return touched;
    },

    async claimOutboxRow(id: string): Promise<OutboxRow | null> {
      const e = await exec();
      let claimed: OutboxRow | null = null;
      await e.tx(async () => {
        const row = await getOutbox(e, id);
        if (!row || !isReady(row, Date.now())) return;
        claimed = { ...row, status: "submitting", updated_at: Date.now() };
        await upsertOutbox(e, claimed);
      });
      if (claimed) publishChange({ kind: "outbox" });
      return claimed;
    },

    async markOutboxResult(
      id: string,
      result: OutboxResult,
      opts: { maxAttempts: number; backoff: (attempt: number) => number },
    ): Promise<void> {
      const e = await exec();
      let mutated = false;
      await e.tx(async () => {
        const row = await getOutbox(e, id);
        if (!row) return;
        mutated = true;
        const transition = nextOutboxState(row, result, { ...opts, now: Date.now() });
        if (transition.kind === "delete") {
          await e.run("DELETE FROM outbox WHERE id = ?", [id]);
          if (row.photo_id) await deletePhotoRow(e, row.photo_id);
        } else {
          await upsertOutbox(e, transition.row);
        }
      });
      if (mutated) publishChange({ kind: "outbox" });
    },

    async cancelOutboxRow(id: string): Promise<boolean> {
      const e = await exec();
      let cancelled = false;
      await e.tx(async () => {
        const row = await getOutbox(e, id);
        if (!row || row.status === "submitting") return;
        await e.run("DELETE FROM outbox WHERE id = ?", [id]);
        if (row.photo_id) await deletePhotoRow(e, row.photo_id);
        cancelled = true;
      });
      if (cancelled) publishChange({ kind: "outbox" });
      return cancelled;
    },

    async editFailedOutboxBackToDraft(
      id: string,
      rebuildState: (row: OutboxRow) => Omit<FormState, "photo">,
    ): Promise<DraftRow | null> {
      const e = await exec();
      let draft: DraftRow | null = null;
      await e.tx(async () => {
        const row = await getOutbox(e, id);
        if (!row || row.status !== "failed") return;
        const fresh = draftFromFailedRow(row, rebuildState, Date.now());
        draft = fresh;
        await upsertDraft(e, fresh);
        await e.run("DELETE FROM outbox WHERE id = ?", [id]);
      });
      if (draft) publishChange({ kind: "drafts+outbox" });
      return draft;
    },

    async patchOutboxPayload(
      id: string,
      patch: Partial<Omit<ReportPayload, "photo">>,
    ): Promise<void> {
      const e = await exec();
      let mutated = false;
      await e.tx(async () => {
        const row = await getOutbox(e, id);
        if (!row) return;
        await upsertOutbox(e, patchPayload(row, patch, Date.now()));
        mutated = true;
      });
      if (mutated) publishChange({ kind: "outbox" });
    },

    // Maintenance ---------------------------------------------------------
    async clearAllReports(): Promise<void> {
      const e = await exec();
      await e.tx(async () => {
        await e.run("DELETE FROM drafts");
        await e.run("DELETE FROM outbox");
        await e.run("DELETE FROM photos");
        await e.run("DELETE FROM submissions");
        await e.run("DELETE FROM report_thumbs");
      });
      publishChange({ kind: "drafts+outbox" });
    },

    async pruneExpired(
      now: number = Date.now(),
    ): Promise<{ draftsPruned: number; outboxFailedPruned: number }> {
      const e = await exec();
      const drafts = parseRows<DraftRow>(await e.all<{ data: string }>("SELECT data FROM drafts"));
      const outbox = parseRows<OutboxRow>(await e.all<{ data: string }>("SELECT data FROM outbox"));
      let draftsPruned = 0;
      let outboxFailedPruned = 0;
      await e.tx(async () => {
        for (const d of drafts) {
          if (now - d.updated_at > TTL.DRAFT_MS) {
            await e.run("DELETE FROM drafts WHERE id = ?", [d.id]);
            if (d.photo_id) await e.run("DELETE FROM photos WHERE id = ?", [d.photo_id]);
            draftsPruned += 1;
          }
        }
        for (const o of outbox) {
          if (o.status === "failed" && now - o.updated_at > TTL.OUTBOX_FAILED_MS) {
            await e.run("DELETE FROM outbox WHERE id = ?", [o.id]);
            if (o.photo_id) await e.run("DELETE FROM photos WHERE id = ?", [o.photo_id]);
            outboxFailedPruned += 1;
          }
        }
      });
      if (draftsPruned > 0 || outboxFailedPruned > 0) publishChange({ kind: "drafts+outbox" });
      return { draftsPruned, outboxFailedPruned };
    },

    // Lifecycle -----------------------------------------------------------
    async reset(): Promise<void> {
      if (!execPromise) return;
      try {
        const e = await execPromise;
        await e.close();
      } catch {
        // ignore
      }
      execPromise = null;
    },
  };
}
