import { describe, expect, it } from "vitest";
import type { ReportPayload } from "../types";
import {
  BACKOFF_SCHEDULE_MS,
  CLAIM_LEASE_MS,
  MAX_OUTBOX_ATTEMPTS,
  type OutboxRow,
  backoffDelayMs,
  buildReportData,
  classifyStatus,
  isReady,
  nextOutboxState,
  resetBackoffRow,
  selectReady,
} from "./outbox-core";

function emptyPayload(): Omit<ReportPayload, "photo"> {
  return {
    crisis_id: "crisis-1",
    damage_class: "partial",
    infra_type: [],
    infra_type_other: "",
    infra_name: "",
    description: "",
    crisis_nature: null,
    crisis_nature_type: null,
    crisis_nature_other: "",
    debris: null,
    electricity: null,
    health_services: null,
    pressing_needs: [],
    latitude: null,
    longitude: null,
  };
}

function makeRow(overrides: Partial<OutboxRow> = {}): OutboxRow {
  return {
    id: "row-1",
    client_submission_id: "csid-1",
    payload: emptyPayload(),
    photo_id: "photo-1",
    status: "queued",
    attempt_count: 0,
    next_attempt_at: 0,
    last_error: null,
    created_at: 1_000,
    updated_at: 1_000,
    ...overrides,
  };
}

describe("backoffDelayMs", () => {
  it("is immediate before the first real attempt", () => {
    expect(backoffDelayMs(0)).toBe(0);
    expect(backoffDelayMs(-3)).toBe(0);
  });
  it("follows the schedule for early attempts", () => {
    expect(backoffDelayMs(1)).toBe(BACKOFF_SCHEDULE_MS[0]);
    expect(backoffDelayMs(2)).toBe(BACKOFF_SCHEDULE_MS[1]);
  });
  it("clamps to the final delay past the schedule", () => {
    const last = BACKOFF_SCHEDULE_MS[BACKOFF_SCHEDULE_MS.length - 1];
    expect(backoffDelayMs(BACKOFF_SCHEDULE_MS.length)).toBe(last);
    expect(backoffDelayMs(999)).toBe(last);
  });
});

describe("classifyStatus", () => {
  it("treats 408 / 429 / 5xx as transient", () => {
    expect(classifyStatus(408)).toBe("transient");
    expect(classifyStatus(429)).toBe("transient");
    expect(classifyStatus(500)).toBe("transient");
    expect(classifyStatus(503)).toBe("transient");
  });
  it("treats a missing status (thrown network error) as transient", () => {
    expect(classifyStatus(undefined)).toBe("transient");
  });
  it("treats other 4xx as terminal", () => {
    expect(classifyStatus(400)).toBe("terminal");
    expect(classifyStatus(401)).toBe("terminal");
    expect(classifyStatus(413)).toBe("terminal");
  });
});

describe("buildReportData", () => {
  it("always carries crisis_id and damage_class", () => {
    expect(buildReportData(emptyPayload())).toMatchObject({
      crisis_id: "crisis-1",
      damage_class: "partial",
    });
  });

  // Regression guard: every surface must carry these three fields.
  it("carries description, form_version, and generic_answers when present", () => {
    const data = buildReportData({
      ...emptyPayload(),
      description: "wall collapsed",
      form_version: 5,
      generic_answers: { "q-uuid": "yes" },
    });
    expect(data.description).toBe("wall collapsed");
    expect(data.form_version).toBe(5);
    expect(data.generic_answers).toEqual({ "q-uuid": "yes" });
  });

  it("omits optional fields when empty", () => {
    const data = buildReportData(emptyPayload());
    expect(data).not.toHaveProperty("description");
    expect(data).not.toHaveProperty("form_version");
    expect(data).not.toHaveProperty("generic_answers");
    expect(data).not.toHaveProperty("infra_type");
    expect(data).not.toHaveProperty("location");
  });

  it("resolves the 'other' infra_type to its free-text value and drops blanks", () => {
    const data = buildReportData({
      ...emptyPayload(),
      infra_type: ["transport", "other"],
      infra_type_other: "  footbridge  ",
    });
    expect(data.infra_type).toEqual(["transport", "footbridge"]);
  });

  it("includes location only when both lat and lng are set", () => {
    expect(buildReportData({ ...emptyPayload(), latitude: 1.5 })).not.toHaveProperty("location");
    expect(buildReportData({ ...emptyPayload(), latitude: 1.5, longitude: 2.5 }).location).toEqual({
      lat: 1.5,
      lng: 2.5,
    });
  });

  it("maps crisis_nature 'Other' to its free text, else to itself", () => {
    expect(
      buildReportData({ ...emptyPayload(), crisis_nature: "Other", crisis_nature_other: "flood" })
        .crisis_type_detailed,
    ).toBe("flood");
    expect(
      buildReportData({ ...emptyPayload(), crisis_nature: "Earthquake" }).crisis_type_detailed,
    ).toBe("Earthquake");
  });
});

describe("isReady / selectReady", () => {
  const now = 10_000;
  it("a queued row whose backoff has elapsed is ready", () => {
    expect(isReady(makeRow({ next_attempt_at: now - 1 }), now)).toBe(true);
  });
  it("a queued row whose backoff is still in the future is not ready", () => {
    expect(isReady(makeRow({ next_attempt_at: now + 1 }), now)).toBe(false);
  });
  it("a live claim and failed rows are not ready", () => {
    expect(isReady(makeRow({ status: "submitting", updated_at: now }), now)).toBe(false);
    expect(isReady(makeRow({ status: "failed" }), now)).toBe(false);
  });
  it("a claim older than the lease is ready again", () => {
    const later = now + CLAIM_LEASE_MS + 1;
    const stuck = makeRow({ status: "submitting", updated_at: now });
    expect(isReady(stuck, now + CLAIM_LEASE_MS)).toBe(false); // lease still held
    expect(isReady(stuck, later)).toBe(true);
    // Lease expiry ignores a future backoff: the claim already passed it.
    expect(isReady({ ...stuck, next_attempt_at: later + 60_000 }, later)).toBe(true);
  });
  it("selectReady returns only the ready rows", () => {
    const rows = [
      makeRow({ id: "a", next_attempt_at: 0 }),
      makeRow({ id: "b", next_attempt_at: now + 100 }),
      makeRow({ id: "c", status: "submitting" }),
    ];
    expect(selectReady(rows, now).map((r) => r.id)).toEqual(["a"]);
  });
});

describe("resetBackoffRow", () => {
  const now = 10_000;
  it("clears a future backoff on a queued row", () => {
    const reset = resetBackoffRow(makeRow({ next_attempt_at: now + 5_000 }), now);
    expect(reset).toMatchObject({ next_attempt_at: 0, updated_at: now });
  });
  it("leaves a ready or non-queued row untouched (returns null)", () => {
    expect(resetBackoffRow(makeRow({ next_attempt_at: now - 1 }), now)).toBeNull();
    expect(
      resetBackoffRow(makeRow({ status: "failed", next_attempt_at: now + 1 }), now),
    ).toBeNull();
  });
});

describe("nextOutboxState", () => {
  const opts = { maxAttempts: MAX_OUTBOX_ATTEMPTS, backoff: backoffDelayMs, now: 50_000 };

  it("success deletes the row", () => {
    expect(nextOutboxState(makeRow(), { kind: "success" }, opts)).toEqual({ kind: "delete" });
  });

  it("terminal marks failed, increments attempt, preserves next_attempt_at", () => {
    const row = makeRow({ attempt_count: 2, next_attempt_at: 999 });
    const t = nextOutboxState(row, { kind: "terminal", error: "bad" }, opts);
    expect(t).toEqual({
      kind: "put",
      row: {
        ...row,
        status: "failed",
        attempt_count: 3,
        last_error: "bad",
        updated_at: opts.now,
      },
    });
  });

  it("transient below max re-queues with backoff", () => {
    const row = makeRow({ attempt_count: 1 });
    const t = nextOutboxState(row, { kind: "transient", error: "503" }, opts);
    expect(t.kind).toBe("put");
    if (t.kind !== "put") return;
    expect(t.row.status).toBe("queued");
    expect(t.row.attempt_count).toBe(2);
    expect(t.row.next_attempt_at).toBe(opts.now + backoffDelayMs(2));
    expect(t.row.last_error).toBe("503");
  });

  it("transient at max downgrades to failed and preserves next_attempt_at", () => {
    const row = makeRow({ attempt_count: MAX_OUTBOX_ATTEMPTS - 1, next_attempt_at: 777 });
    const t = nextOutboxState(row, { kind: "transient", error: "503" }, opts);
    expect(t.kind).toBe("put");
    if (t.kind !== "put") return;
    expect(t.row.status).toBe("failed");
    expect(t.row.attempt_count).toBe(MAX_OUTBOX_ATTEMPTS);
    expect(t.row.next_attempt_at).toBe(777);
  });
});
