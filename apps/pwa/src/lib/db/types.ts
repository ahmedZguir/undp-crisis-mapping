// Local-store row shapes shared by every backend; outbox types live in `outbox-core.ts`.

import type { Crisis, FormSchema, FormState, ReportPayload } from "../../types";
import type { OutboxRow } from "../outbox-core";

export interface DraftRow {
  id: string;
  state: Omit<FormState, "photo">;
  photo_id: string | null;
  step: number;
  updated_at: number;
  // Pinned at draft creation so a newer published form doesn't apply on resume. Optional for old drafts.
  form_schema?: FormSchema;
  form_version?: number;
  // Pinned so a resumed draft keeps its crisis after the user switches. Optional for old/pre-pick drafts.
  crisis?: Crisis;
}

export function draftFromFailedRow(
  row: OutboxRow,
  rebuildState: (row: OutboxRow) => Omit<FormState, "photo">,
  now: number,
): DraftRow {
  return {
    id: crypto.randomUUID(),
    state: rebuildState(row),
    photo_id: row.photo_id,
    step: 0,
    updated_at: now,
  };
}

export interface PhotoRow {
  id: string;
  // ArrayBuffer, not Blob: Safari Private Browsing throws DataCloneError on Blob-in-IDB.
  // `putPhoto`/`getPhoto` convert at the boundary.
  data: ArrayBuffer;
  type: string;
  created_at: number;
}

// ≤320px thumb of a submitted report's photo for offline My Reports. ArrayBuffer for the same reason as PhotoRow.
export interface ReportThumbRow {
  report_id: string;
  data: ArrayBuffer;
  type: string;
  created_at: number;
}

export interface MetaRow {
  key: string;
  value: unknown;
}

// The `submissions` store is unused but kept (and cleared) for schema stability.
export interface SubmissionRow {
  client_submission_id: string;
  crisis_id: string;
  summary: {
    infra_name: string;
    damage_class: ReportPayload["damage_class"] | null;
  };
  submitted_at: number;
  last_status: "submitted" | "under_review" | "accepted" | "dismissed";
  status_checked_at: number;
}
