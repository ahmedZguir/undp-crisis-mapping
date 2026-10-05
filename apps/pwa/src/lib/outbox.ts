import { SubmitReportError, submitReport } from "../api/reports";
import type { ReportPayload } from "../types";
import { getClientId } from "./clientId";
import type { DraftRow } from "./db";
import {
  type OutboxRow,
  claimOutboxRow,
  getDraft,
  getPhoto,
  loadOutbox,
  markOutboxResult,
  promoteDraftToOutbox,
  putOutboxRow,
  putReportThumb,
  resetQueuedBackoff,
} from "./db";
import { errorMessage } from "./errors";
import { currentNetworkTier } from "./networkTier";
import {
  MAX_OUTBOX_ATTEMPTS,
  backoffDelayMs,
  buildReportData,
  classifyStatus,
  selectReady,
} from "./outbox-core";
import { resizePhotoOffMainThread } from "./photoResizeClient";
import { refreshReportsCache } from "./reportsCache";
import { SYNC_TAG } from "./storeConfig";

// Fired for rows with no crisis_id; the app auto-assigns a crisis or prompts the user.
export const OUTBOX_NEEDS_CRISIS_EVENT = "outbox-needs-crisis";

interface OutboxNeedsCrisisDetail {
  rowIds: string[];
}

function emitNeedsCrisis(rowIds: string[]) {
  if (rowIds.length === 0) return;
  if (typeof window === "undefined") return;
  try {
    window.dispatchEvent(
      new CustomEvent<OutboxNeedsCrisisDetail>(OUTBOX_NEEDS_CRISIS_EVENT, {
        detail: { rowIds },
      }),
    );
  } catch {
    // best-effort
  }
}

export function classifyError(err: unknown): "transient" | "terminal" {
  return classifyStatus(err instanceof SubmitReportError ? err.status : undefined);
}

const RETRY_POLICY = { maxAttempts: MAX_OUTBOX_ATTEMPTS, backoff: backoffDelayMs } as const;

let flushing: Promise<void> | null = null;

async function tryRegisterSyncTag(): Promise<void> {
  if (typeof navigator === "undefined" || !navigator.serviceWorker) return;
  try {
    const reg = await navigator.serviceWorker.ready;
    const sync = (
      reg as ServiceWorkerRegistration & {
        sync?: { register(tag: string): Promise<void> };
      }
    ).sync;
    if (!sync) {
      console.warn("[outbox] Background Sync API unavailable on this browser");
      return;
    }
    await sync.register(SYNC_TAG);
    console.log("[outbox] sync tag registered:", SYNC_TAG);
  } catch (err) {
    // e.g. insecure context or storage pressure; in-page retry covers us.
    console.warn("[outbox] sync.register failed:", err);
  }
}

// A draft's pinned form schema/version ride along, so a newer published form doesn't apply mid-report.
function buildQueuedRow(
  photoId: string | null,
  payload: Omit<ReportPayload, "photo">,
  clientId: string,
  draft?: DraftRow,
): OutboxRow {
  const clientSubmissionId = crypto.randomUUID();
  const now = Date.now();
  const finalPayload = {
    ...payload,
    client_submission_id: clientSubmissionId,
    client_id: clientId,
    ...(draft ? { form_version: payload.form_version ?? draft.form_version } : {}),
  };
  return {
    id: crypto.randomUUID(),
    client_submission_id: clientSubmissionId,
    payload: finalPayload,
    photo_id: photoId,
    status: "queued",
    attempt_count: 0,
    next_attempt_at: 0,
    last_error: null,
    created_at: now,
    updated_at: now,
    ...(draft ? { form_schema: draft.form_schema, form_version: draft.form_version } : {}),
    request_data: buildReportData(finalPayload),
  };
}

export interface OutboxEnqueueResult {
  outboxId: string;
  clientSubmissionId: string;
}

// The caller must already have stored the photo under `photoId`.
export async function enqueueFromDraft(
  draftId: string,
  photoId: string | null,
  payload: Omit<ReportPayload, "photo">,
): Promise<OutboxEnqueueResult> {
  const clientId = await getClientId();
  const draft = await getDraft(draftId);
  const row = buildQueuedRow(photoId, payload, clientId, draft);
  await promoteDraftToOutbox(draftId, row);
  void tryRegisterSyncTag();
  void requestFlush("enqueue");
  return { outboxId: row.id, clientSubmissionId: row.client_submission_id };
}

// Test seam: enqueue without a draft.
export async function enqueueDirect(
  photoId: string | null,
  payload: Omit<ReportPayload, "photo">,
): Promise<{ outboxId: string; clientSubmissionId: string }> {
  const clientId = await getClientId();
  const row = buildQueuedRow(photoId, payload, clientId);
  await putOutboxRow(row);
  void tryRegisterSyncTag();
  void requestFlush("enqueue-direct");
  return { outboxId: row.id, clientSubmissionId: row.client_submission_id };
}

// Concurrent callers share the in-flight flush. `_reason` is a call-site label only.
export function requestFlush(_reason: string): Promise<void> {
  if (flushing) return flushing;
  flushing = doFlush().finally(() => {
    flushing = null;
  });
  return flushing;
}

async function doFlush(): Promise<void> {
  const all = await loadOutbox();
  const ready = selectReady(all, Date.now());
  const needsCrisis: string[] = [];
  let anySuccess = false;
  for (const candidate of ready) {
    if (!candidate.payload.crisis_id) {
      needsCrisis.push(candidate.id);
      continue;
    }
    const claimed = await claimOutboxRow(candidate.id);
    if (!claimed) continue;
    const ok = await submitClaimedRow(claimed);
    if (ok) anySuccess = true;
  }
  emitNeedsCrisis(needsCrisis);
  if (anySuccess) void refreshReportsCache();
}

async function submitClaimedRow(row: OutboxRow): Promise<boolean> {
  // A referenced photo that can't be loaded is unrecoverable, so terminal.
  let blob: Blob | null = null;
  if (row.photo_id) {
    const baseline = await getPhoto(row.photo_id);
    if (!baseline) {
      await markOutboxResult(
        row.id,
        { kind: "terminal", error: "photo missing from local storage" },
        RETRY_POLICY,
      );
      return false;
    }
    // Tier is re-evaluated per attempt; on encode failure send the baseline rather than fail.
    blob = baseline;
    if (currentNetworkTier() === "constrained") {
      try {
        blob = await resizePhotoOffMainThread(baseline, "constrained");
      } catch {
        blob = baseline;
      }
    }
  }
  try {
    const { id: reportId } = await submitReport({ ...row.payload, photo: blob });
    // Local thumb for offline My Reports (signed URLs are short-lived and uncacheable). Best-effort,
    // and must run before markOutboxResult deletes the source photo.
    if (blob) {
      try {
        const thumb = await resizePhotoOffMainThread(blob, "thumb");
        await putReportThumb(reportId, thumb);
      } catch {
        // best-effort
      }
    }
    await markOutboxResult(row.id, { kind: "success" }, RETRY_POLICY);
    return true;
  } catch (err) {
    const kind = classifyError(err);
    await markOutboxResult(row.id, { kind, error: errorMessage(err) }, RETRY_POLICY);
    if (kind === "transient") scheduleBackoffRetry(row.attempt_count + 1);
    return false;
  }
}

// Self-reschedule after a transient failure: a one-shot ERR_NETWORK_CHANGED on the offline→online
// edge would otherwise strand the row until another trigger fires.
let backoffTimer: ReturnType<typeof setTimeout> | null = null;
let backoffTimerFiresAt = 0;

export function clearBackoffTimer(): void {
  if (backoffTimer) clearTimeout(backoffTimer);
  backoffTimer = null;
  backoffTimerFiresAt = 0;
}

// On reconnect, drop stale backoff (timer and row-level) and flush everything queued.
export async function flushAfterReconnect(reason: string): Promise<void> {
  clearBackoffTimer();
  await resetQueuedBackoff();
  await requestFlush(reason);
}

function scheduleBackoffRetry(nextAttempt: number): void {
  const delay = backoffDelayMs(nextAttempt);
  const fireAt = Date.now() + delay;
  if (backoffTimer && backoffTimerFiresAt <= fireAt) return;
  if (backoffTimer) clearTimeout(backoffTimer);
  backoffTimerFiresAt = fireAt;
  backoffTimer = setTimeout(() => {
    backoffTimer = null;
    backoffTimerFiresAt = 0;
    void requestFlush("backoff");
  }, delay);
}

export function _resetOutboxForTests(): void {
  flushing = null;
  if (backoffTimer) clearTimeout(backoffTimer);
  backoffTimer = null;
  backoffTimerFiresAt = 0;
}
