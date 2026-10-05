import { deleteDB } from "idb";
import { beforeEach, describe, expect, it } from "vitest";
import type { FormState, ReportPayload } from "../types";
import {
  type DraftRow,
  type OutboxRow,
  _resetForTests,
  cancelOutboxRow,
  claimOutboxRow,
  deleteDraft,
  editFailedOutboxBackToDraft,
  getDraft,
  getOutboxRow,
  getPhoto,
  getReportThumb,
  loadDrafts,
  loadOutbox,
  markOutboxResult,
  promoteDraftToOutbox,
  pruneExpired,
  putOutboxRow,
  putPhoto,
  putReportThumb,
  saveDraft,
} from "./db";
import { CLAIM_LEASE_MS, backoffDelayMs } from "./outbox-core";
import { MAX_REPORT_THUMBS } from "./storeConfig";

function emptyState(overrides: Partial<Omit<FormState, "photo">> = {}): Omit<FormState, "photo"> {
  return {
    damage_class: null,
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
    crisis_id: "",
    latitude: null,
    longitude: null,
    route_description: "",
    photo_metadata: null,
    generic_answers: {},
    ...overrides,
  };
}

function makeDraft(id: string, overrides: Partial<DraftRow> = {}): DraftRow {
  return {
    id,
    state: emptyState(),
    photo_id: null,
    step: 0,
    updated_at: Date.now(),
    ...overrides,
  };
}

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

function makeOutboxRow(id: string, overrides: Partial<OutboxRow> = {}): OutboxRow {
  const now = Date.now();
  return {
    id,
    client_submission_id: `csid-${id}`,
    payload: emptyPayload(),
    photo_id: `photo-${id}`,
    status: "queued",
    attempt_count: 0,
    next_attempt_at: 0,
    last_error: null,
    created_at: now,
    updated_at: now,
    ...overrides,
  };
}

beforeEach(async () => {
  await _resetForTests();
  await deleteDB("undp-app");
  await deleteDB("keyval-store");
});

describe("drafts", () => {
  it("round-trips a draft and lists by updated_at desc", async () => {
    await saveDraft(makeDraft("a", { updated_at: 1 }));
    await saveDraft(makeDraft("b", { updated_at: 3 }));
    await saveDraft(makeDraft("c", { updated_at: 2 }));

    const all = await loadDrafts();
    expect(all.map((d) => d.id)).toEqual(["b", "c", "a"]);
  });

  it("deleteDraft also deletes the orphan photo", async () => {
    const photoId = await putPhoto(new Blob(["x"], { type: "image/jpeg" }));
    await saveDraft(makeDraft("d1", { photo_id: photoId }));

    expect(await getPhoto(photoId)).not.toBeNull();
    await deleteDraft("d1");
    expect(await getDraft("d1")).toBeUndefined();
    expect(await getPhoto(photoId)).toBeNull();
  });

  it("deleteDraft on a missing id is a no-op", async () => {
    await expect(deleteDraft("missing")).resolves.toBeUndefined();
  });
});

describe("photos", () => {
  it("putPhoto stores the bytes and getPhoto reconstructs the blob", async () => {
    const blob = new Blob([new Uint8Array([1, 2, 3])], { type: "image/jpeg" });
    const id = await putPhoto(blob);
    const got = await getPhoto(id);
    expect(got).not.toBeNull();
    // Photos are stored as an ArrayBuffer (not a Blob — that throws in Safari
    // Private Browsing), so the bytes and MIME type must survive the round-trip.
    expect(got?.type).toBe("image/jpeg");
    expect([...new Uint8Array(await (got as Blob).arrayBuffer())]).toEqual([1, 2, 3]);
  });
});

describe("report thumbnails", () => {
  it("putReportThumb round-trips bytes + type keyed by report id", async () => {
    const blob = new Blob([new Uint8Array([9, 8, 7])], { type: "image/webp" });
    await putReportThumb("report-1", blob);
    const got = await getReportThumb("report-1");
    expect(got?.type).toBe("image/webp");
    expect([...new Uint8Array(await (got as Blob).arrayBuffer())]).toEqual([9, 8, 7]);
  });

  it("returns null for an unknown report id", async () => {
    expect(await getReportThumb("nope")).toBeNull();
  });

  it("caps the cache at MAX_REPORT_THUMBS and keeps the newest", async () => {
    const extra = 5;
    for (let i = 0; i < MAX_REPORT_THUMBS + extra; i++) {
      await putReportThumb(`r${i}`, new Blob([new Uint8Array([i & 0xff])], { type: "image/webp" }));
    }
    // Exactly MAX survive (every key unique, so the count is the contract).
    let survivors = 0;
    for (let i = 0; i < MAX_REPORT_THUMBS + extra; i++) {
      if (await getReportThumb(`r${i}`)) survivors += 1;
    }
    expect(survivors).toBe(MAX_REPORT_THUMBS);
    // The last write has the newest created_at, so it is never the one evicted.
    expect(await getReportThumb(`r${MAX_REPORT_THUMBS + extra - 1}`)).not.toBeNull();
  });
});

describe("outbox", () => {
  it("promoteDraftToOutbox atomically deletes the draft and inserts the outbox row", async () => {
    await saveDraft(makeDraft("d1"));
    const row = makeOutboxRow("o1");
    await promoteDraftToOutbox("d1", row);

    expect(await getDraft("d1")).toBeUndefined();
    expect(await getOutboxRow("o1")).toMatchObject({ id: "o1", status: "queued" });
  });

  it("claimOutboxRow transitions queued → submitting and second claim no-ops", async () => {
    await putOutboxRow(makeOutboxRow("o1"));
    const a = await claimOutboxRow("o1");
    expect(a?.status).toBe("submitting");
    const b = await claimOutboxRow("o1");
    expect(b).toBeNull();
  });

  it("reclaims a row whose claim outlived the lease", async () => {
    const stale = Date.now() - CLAIM_LEASE_MS - 1;
    await putOutboxRow(makeOutboxRow("o1", { status: "submitting", updated_at: stale }));
    const reclaimed = await claimOutboxRow("o1");
    expect(reclaimed?.status).toBe("submitting");
    expect(reclaimed?.updated_at).toBeGreaterThan(stale);
    // The fresh claim holds a new lease.
    expect(await claimOutboxRow("o1")).toBeNull();
  });

  it("two parallel claims race — only one wins", async () => {
    await putOutboxRow(makeOutboxRow("o1"));
    const [a, b] = await Promise.all([claimOutboxRow("o1"), claimOutboxRow("o1")]);
    const winners = [a, b].filter((r) => r !== null);
    expect(winners).toHaveLength(1);
  });

  it("claim respects next_attempt_at (in the future = not ready)", async () => {
    await putOutboxRow(makeOutboxRow("o1", { next_attempt_at: Date.now() + 60_000 }));
    const a = await claimOutboxRow("o1");
    expect(a).toBeNull();
  });

  it("markOutboxResult success deletes the row and its photo", async () => {
    const photoId = await putPhoto(new Blob(["x"]));
    await putOutboxRow(makeOutboxRow("o1", { photo_id: photoId }));
    await claimOutboxRow("o1");
    await markOutboxResult("o1", { kind: "success" }, { maxAttempts: 5, backoff: backoffDelayMs });
    expect(await getOutboxRow("o1")).toBeUndefined();
    expect(await getPhoto(photoId)).toBeNull();
  });

  it("markOutboxResult transient re-queues with backoff and increments attempt_count", async () => {
    await putOutboxRow(makeOutboxRow("o1"));
    await claimOutboxRow("o1");
    const before = Date.now();
    await markOutboxResult(
      "o1",
      { kind: "transient", error: "net" },
      { maxAttempts: 5, backoff: () => 5_000 },
    );
    const row = await getOutboxRow("o1");
    expect(row?.status).toBe("queued");
    expect(row?.attempt_count).toBe(1);
    expect(row?.last_error).toBe("net");
    expect(row?.next_attempt_at).toBeGreaterThanOrEqual(before + 4_900);
  });

  it("markOutboxResult transient downgrades to failed at maxAttempts", async () => {
    await putOutboxRow(makeOutboxRow("o1", { attempt_count: 4 }));
    await claimOutboxRow("o1");
    await markOutboxResult(
      "o1",
      { kind: "transient", error: "net" },
      { maxAttempts: 5, backoff: () => 5_000 },
    );
    const row = await getOutboxRow("o1");
    expect(row?.status).toBe("failed");
    expect(row?.attempt_count).toBe(5);
  });

  it("markOutboxResult terminal marks failed", async () => {
    await putOutboxRow(makeOutboxRow("o1"));
    await claimOutboxRow("o1");
    await markOutboxResult(
      "o1",
      { kind: "terminal", error: "400 bad" },
      { maxAttempts: 5, backoff: backoffDelayMs },
    );
    const row = await getOutboxRow("o1");
    expect(row?.status).toBe("failed");
    expect(row?.last_error).toBe("400 bad");
  });

  it("cancelOutboxRow refuses to cancel a row that is submitting", async () => {
    await putOutboxRow(makeOutboxRow("o1"));
    await claimOutboxRow("o1");
    expect(await cancelOutboxRow("o1")).toBe(false);
    expect(await getOutboxRow("o1")).toMatchObject({ status: "submitting" });
  });

  it("cancelOutboxRow deletes a queued row and its photo", async () => {
    const photoId = await putPhoto(new Blob(["x"]));
    await putOutboxRow(makeOutboxRow("o1", { photo_id: photoId }));
    expect(await cancelOutboxRow("o1")).toBe(true);
    expect(await getOutboxRow("o1")).toBeUndefined();
    expect(await getPhoto(photoId)).toBeNull();
  });

  it("editFailedOutboxBackToDraft moves a failed row into a fresh draft", async () => {
    await putOutboxRow(makeOutboxRow("o1", { status: "failed" }));
    const draft = await editFailedOutboxBackToDraft("o1", () =>
      emptyState({ infra_name: "rebuilt" }),
    );
    expect(draft).not.toBeNull();
    expect(draft?.state.infra_name).toBe("rebuilt");
    expect(await getOutboxRow("o1")).toBeUndefined();
    const drafts = await loadDrafts();
    expect(drafts).toHaveLength(1);
  });

  it("editFailedOutboxBackToDraft refuses non-failed rows", async () => {
    await putOutboxRow(makeOutboxRow("o1"));
    const draft = await editFailedOutboxBackToDraft("o1", () => emptyState());
    expect(draft).toBeNull();
  });
});

describe("loadOutbox", () => {
  it("returns rows sorted by created_at asc", async () => {
    await putOutboxRow(makeOutboxRow("a", { created_at: 3 }));
    await putOutboxRow(makeOutboxRow("b", { created_at: 1 }));
    await putOutboxRow(makeOutboxRow("c", { created_at: 2 }));
    const all = await loadOutbox();
    expect(all.map((r) => r.id)).toEqual(["b", "c", "a"]);
  });
});

describe("pruneExpired", () => {
  it("drops drafts older than the TTL and their photos", async () => {
    const photoId = await putPhoto(new Blob(["x"]));
    const fortyDaysAgo = Date.now() - 40 * 24 * 60 * 60 * 1000;
    await saveDraft(makeDraft("old", { photo_id: photoId, updated_at: fortyDaysAgo }));
    await saveDraft(makeDraft("fresh"));

    const result = await pruneExpired();
    expect(result.draftsPruned).toBe(1);
    expect(await getDraft("old")).toBeUndefined();
    expect(await getDraft("fresh")).toBeDefined();
    expect(await getPhoto(photoId)).toBeNull();
  });

  it("drops failed outbox rows older than the TTL but not queued ones", async () => {
    const fortyDaysAgo = Date.now() - 40 * 24 * 60 * 60 * 1000;
    await putOutboxRow(makeOutboxRow("old-failed", { status: "failed", updated_at: fortyDaysAgo }));
    await putOutboxRow(makeOutboxRow("old-queued", { status: "queued", updated_at: fortyDaysAgo }));

    const result = await pruneExpired();
    expect(result.outboxFailedPruned).toBe(1);
    expect(await getOutboxRow("old-failed")).toBeUndefined();
    expect(await getOutboxRow("old-queued")).toBeDefined();
  });
});
