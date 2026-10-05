// Offline submission-queue adapter. Web: IndexedDB + Workbox Background Sync; native: SQLite + OS triggers.

import type { OutboxEnqueueResult } from "../../lib/outbox";
import type { ReportPayload } from "../../types";
import { isNativePlatform } from "../platformInfo";
import { nativeOutbox } from "./native";
import { webOutbox } from "./web";

export type { OutboxEnqueueResult };

export interface OutboxAdapter {
  // The caller has already stored the photo; `photoId` is null for a description-only report.
  enqueueFromDraft(
    draftId: string,
    photoId: string | null,
    payload: Omit<ReportPayload, "photo">,
  ): Promise<OutboxEnqueueResult>;
  requestFlush(reason: string): Promise<void>;
  clearBackoffTimer(): void;
  // Idempotent. No-op on web, where App.tsx wires the triggers.
  init(): void;
}

// Contract: any impl that enqueues crisis-less rows must emit this.
export { OUTBOX_NEEDS_CRISIS_EVENT } from "../../lib/outbox";

export const outbox: OutboxAdapter = isNativePlatform() ? nativeOutbox : webOutbox;
