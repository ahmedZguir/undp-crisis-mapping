// Outbox queue/format/retry logic shared by the page (`outbox.ts`), the SW (`public/sw-outbox.js`)
// and native (`db/sqliteBackend.ts` + OutboxSync workers). Must stay PURE (type-only imports):
// it is also compiled to the SW's `self.OutboxCore` global (`swOutboxCorePlugin` in vite.config.ts).

import type { FormSchema, ReportPayload } from "../types";

// Mirrored natively — keep in sync: `OutboxSyncWorker.java` and `OutboxSync.swift` copy
// MAX_OUTBOX_ATTEMPTS, BACKOFF_SCHEDULE_MS, CLAIM_LEASE_MS, isReady, classifyStatus and
// nextOutboxState.

export const MAX_OUTBOX_ATTEMPTS = 20;

export const BACKOFF_SCHEDULE_MS = [5_000, 30_000, 120_000, 600_000, 3_600_000] as const;

// A `submitting` claim expires after this, so a claimer that dies mid-POST can't strand the row.
// Well past every POST timeout; a re-POST is safe, the server dedups on client_submission_id.
export const CLAIM_LEASE_MS = 5 * 60_000;

// 1-based attempt; attempts past the schedule clamp to the longest delay.
export function backoffDelayMs(attempt: number): number {
  if (attempt < 1) return 0;
  const idx = Math.min(attempt - 1, BACKOFF_SCHEDULE_MS.length - 1);
  return BACKOFF_SCHEDULE_MS[idx];
}

export type OutboxStatus = "queued" | "submitting" | "failed";

export interface OutboxRow {
  id: string;
  client_submission_id: string;
  payload: Omit<ReportPayload, "photo">;
  // null for a description-only report.
  photo_id: string | null;
  status: OutboxStatus;
  attempt_count: number;
  next_attempt_at: number;
  last_error: string | null;
  created_at: number;
  updated_at: number;
  // Pinned at enqueue; duplicates payload.form_version for debugging stale submissions.
  form_schema?: FormSchema;
  form_version?: number;
  // Pre-built `buildReportData(payload)` body for the native worker, which has no JS to rebuild it.
  // Web paths rebuild it at flush time. Keep in sync with `payload` on every write.
  request_data?: Record<string, unknown>;
}

export type OutboxResult =
  | { kind: "success" }
  | { kind: "transient"; error: string }
  | { kind: "terminal"; error: string };

// Retry policy: no status (network error/abort), 408, 429 and 5xx are transient.
export function classifyStatus(status: number | undefined): "transient" | "terminal" {
  if (status === undefined) return "transient";
  if (status === 408 || status === 429) return "transient";
  if (status >= 500) return "transient";
  return "terminal";
}

// Single source of truth for the `data` JSON part of `POST /reports` on every surface.
export function buildReportData(payload: Omit<ReportPayload, "photo">): Record<string, unknown> {
  const data: Record<string, unknown> = {
    crisis_id: payload.crisis_id,
    damage_class: payload.damage_class,
  };

  if (payload.client_id) {
    data.client_id = payload.client_id;
  }

  const resolvedInfraType = (payload.infra_type ?? [])
    .map((t) => (t === "other" ? payload.infra_type_other?.trim() || null : t))
    .filter((t): t is string => Boolean(t));
  if (resolvedInfraType.length > 0) {
    data.infra_type = resolvedInfraType;
  }

  if (payload.infra_name) data.infra_name = payload.infra_name;
  if (payload.description) data.description = payload.description;
  // "No GPS?" fallback; the backend geocodes it.
  if (payload.route_description) data.route_description = payload.route_description;

  if (payload.crisis_nature_type) data.crisis_type = payload.crisis_nature_type;
  if (payload.crisis_nature === "Other") {
    if (payload.crisis_nature_other) data.crisis_type_detailed = payload.crisis_nature_other;
  } else if (payload.crisis_nature) {
    data.crisis_type_detailed = payload.crisis_nature;
  }

  if (payload.debris) data.debris = payload.debris;

  if (payload.building_id) data.building_id = payload.building_id;

  if (payload.latitude != null && payload.longitude != null) {
    data.location = { lat: payload.latitude, lng: payload.longitude };
  }

  if (payload.client_submission_id) {
    data.client_submission_id = payload.client_submission_id;
  }

  // `form_version` is load-bearing for OTA safety: a queued report must submit against the schema it was pinned to.
  if (typeof payload.form_version === "number") {
    data.form_version = payload.form_version;
  }
  if (payload.generic_answers && Object.keys(payload.generic_answers).length > 0) {
    data.generic_answers = payload.generic_answers;
  }

  // The canvas re-encode strips EXIF, so this is the only way it reaches the server.
  if (payload.photo_metadata) {
    data.photo_metadata = payload.photo_metadata;
  }

  return data;
}

export function buildReportFormData(
  payload: Omit<ReportPayload, "photo">,
  photo: Blob | null,
): FormData {
  const form = new FormData();
  if (photo) form.append("photo", photo);
  form.append("data", JSON.stringify(buildReportData(payload)));
  return form;
}

// Claimable now: queued and past its backoff, or a claim whose lease has expired.
export function isReady(row: OutboxRow, now: number): boolean {
  if (row.status === "submitting") return row.updated_at < now - CLAIM_LEASE_MS;
  return row.status === "queued" && row.next_attempt_at <= now;
}

export function isPending(status: string): boolean {
  return status === "queued" || status === "submitting";
}

export function selectReady(rows: OutboxRow[], now: number): OutboxRow[] {
  return rows.filter((row) => isReady(row, now));
}

// On reconnect the backoff is dead time. Returns null when there is nothing to change.
export function resetBackoffRow(row: OutboxRow, now: number): OutboxRow | null {
  if (row.status !== "queued" || row.next_attempt_at <= now) return null;
  return { ...row, next_attempt_at: 0, updated_at: now };
}

// Keeps `request_data` in sync for the native worker.
export function patchPayload(
  row: OutboxRow,
  patch: Partial<Omit<ReportPayload, "photo">>,
  now: number,
): OutboxRow {
  const mergedPayload = { ...row.payload, ...patch };
  return {
    ...row,
    payload: mergedPayload,
    request_data: buildReportData(mergedPayload),
    updated_at: now,
  };
}

type OutboxTransition = { kind: "delete" } | { kind: "put"; row: OutboxRow };

// Caller owns the write (and photo cleanup on `delete`). A transient result at `maxAttempts` goes `failed`.
export function nextOutboxState(
  row: OutboxRow,
  result: OutboxResult,
  opts: { maxAttempts: number; backoff: (attempt: number) => number; now: number },
): OutboxTransition {
  if (result.kind === "success") {
    return { kind: "delete" };
  }
  if (result.kind === "terminal") {
    return {
      kind: "put",
      row: {
        ...row,
        status: "failed",
        attempt_count: row.attempt_count + 1,
        last_error: result.error,
        updated_at: opts.now,
      },
    };
  }
  const nextAttempt = row.attempt_count + 1;
  const isMax = nextAttempt >= opts.maxAttempts;
  return {
    kind: "put",
    row: {
      ...row,
      status: isMax ? "failed" : "queued",
      attempt_count: nextAttempt,
      next_attempt_at: isMax ? row.next_attempt_at : opts.now + opts.backoff(nextAttempt),
      last_error: result.error,
      updated_at: opts.now,
    },
  };
}
