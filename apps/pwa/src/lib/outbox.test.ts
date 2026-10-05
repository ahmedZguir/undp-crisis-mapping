import { deleteDB } from "idb";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { SubmitReportError } from "../api/reports";
import type { ReportPayload } from "../types";
import { _resetForTests, getOutboxRow, loadOutbox, patchOutboxPayload, putPhoto } from "./db";
import { _resetOutboxForTests, classifyError, enqueueDirect, requestFlush } from "./outbox";
import { buildReportData } from "./outbox-core";

vi.mock("../api/reports", async () => {
  const actual = await vi.importActual<typeof import("../api/reports")>("../api/reports");
  return {
    ...actual,
    submitReport: vi.fn(),
  };
});

const submitReport = (await import("../api/reports")).submitReport as unknown as ReturnType<
  typeof vi.fn
>;

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

beforeEach(async () => {
  await _resetForTests();
  _resetOutboxForTests();
  const { _resetClientIdCacheForTests } = await import("./clientId");
  _resetClientIdCacheForTests();
  await deleteDB("undp-app");
  await deleteDB("keyval-store");
  submitReport.mockReset();
});

describe("classifyError", () => {
  it("treats 5xx as transient", () => {
    expect(classifyError(new SubmitReportError("x", 500))).toBe("transient");
    expect(classifyError(new SubmitReportError("x", 503))).toBe("transient");
  });
  it("treats 408 / 429 as transient", () => {
    expect(classifyError(new SubmitReportError("x", 408))).toBe("transient");
    expect(classifyError(new SubmitReportError("x", 429))).toBe("transient");
  });
  it("treats 4xx (other) as terminal", () => {
    expect(classifyError(new SubmitReportError("x", 400))).toBe("terminal");
    expect(classifyError(new SubmitReportError("x", 413))).toBe("terminal");
    expect(classifyError(new SubmitReportError("x", 401))).toBe("terminal");
  });
  it("treats network errors as transient", () => {
    expect(classifyError(new TypeError("Failed to fetch"))).toBe("transient");
  });
});

describe("requestFlush", () => {
  it("submits a queued row and removes it on success", async () => {
    submitReport.mockResolvedValue({ id: "report-1" });
    const photoId = await putPhoto(new Blob(["x"]));
    await enqueueDirect(photoId, emptyPayload());

    await requestFlush("test");

    expect(submitReport).toHaveBeenCalledTimes(1);
    expect(await loadOutbox()).toHaveLength(0);
  });

  it("transient failure re-queues with attempt_count incremented", async () => {
    submitReport.mockRejectedValue(new SubmitReportError("boom", 503));
    const photoId = await putPhoto(new Blob(["x"]));
    const { outboxId } = await enqueueDirect(photoId, emptyPayload());

    await requestFlush("test");
    const row = await getOutboxRow(outboxId);
    expect(row?.status).toBe("queued");
    expect(row?.attempt_count).toBe(1);
    expect(row?.next_attempt_at).toBeGreaterThan(Date.now());
  });

  it("terminal failure marks the row failed", async () => {
    submitReport.mockRejectedValue(new SubmitReportError("bad payload", 400));
    const photoId = await putPhoto(new Blob(["x"]));
    const { outboxId } = await enqueueDirect(photoId, emptyPayload());

    await requestFlush("test");
    const row = await getOutboxRow(outboxId);
    expect(row?.status).toBe("failed");
    expect(row?.last_error).toBe("bad payload");
  });

  it("forwards client_submission_id in the payload", async () => {
    submitReport.mockResolvedValue({ id: "report-1" });
    const photoId = await putPhoto(new Blob(["x"]));
    const { clientSubmissionId } = await enqueueDirect(photoId, emptyPayload());

    await requestFlush("test");
    expect(submitReport).toHaveBeenCalledTimes(1);
    const sentPayload = submitReport.mock.calls[0][0] as ReportPayload;
    expect(sentPayload.client_submission_id).toBe(clientSubmissionId);
  });

  it("stamps client_id onto the outbox row at enqueue time", async () => {
    submitReport.mockResolvedValue({ id: "report-1" });
    const photoId = await putPhoto(new Blob(["x"]));
    await enqueueDirect(photoId, emptyPayload());

    await requestFlush("test");
    const sentPayload = submitReport.mock.calls[0][0] as ReportPayload;
    expect(sentPayload.client_id).toMatch(/^[0-9a-f-]{36}$/i);
  });

  it("two parallel flushes share one in-flight promise (no duplicate POST)", async () => {
    let resolve: (v: { id: string }) => void = () => {};
    let calledAt = 0;
    submitReport.mockImplementation(() => {
      calledAt = Date.now();
      return new Promise<{ id: string }>((r) => {
        resolve = r;
      });
    });
    const photoId = await putPhoto(new Blob(["x"]));
    await enqueueDirect(photoId, emptyPayload());

    const a = requestFlush("a");
    // Yield until submitReport has been entered once.
    while (calledAt === 0) await new Promise((r) => setTimeout(r, 0));
    const b = requestFlush("b");
    expect(a).toBe(b); // same in-flight promise
    expect(submitReport).toHaveBeenCalledTimes(1);
    resolve({ id: "report-1" });
    await Promise.all([a, b]);
    expect(submitReport).toHaveBeenCalledTimes(1);
  });

  it("does not retry rows whose next_attempt_at is in the future", async () => {
    submitReport.mockRejectedValue(new SubmitReportError("boom", 503));
    const photoId = await putPhoto(new Blob(["x"]));
    await enqueueDirect(photoId, emptyPayload());

    await requestFlush("first"); // bumps next_attempt_at into the future
    submitReport.mockClear();
    await requestFlush("second");
    expect(submitReport).not.toHaveBeenCalled();
  });

  it("deletes a row whose photo went missing (terminal)", async () => {
    const photoId = await putPhoto(new Blob(["x"]));
    const { outboxId } = await enqueueDirect(photoId, emptyPayload());
    // Simulate photo eviction by deleting the photo manually.
    const { deletePhoto } = await import("./db");
    await deletePhoto(photoId);

    await requestFlush("test");
    const row = await getOutboxRow(outboxId);
    expect(row?.status).toBe("failed");
    expect(row?.last_error).toContain("photo missing");
    expect(submitReport).not.toHaveBeenCalled();
  });
});

// The native worker can't re-run buildReportData, so `request_data` must be present and in sync.
describe("request_data precompute", () => {
  it("enqueue persists request_data equal to buildReportData(payload)", async () => {
    const photoId = await putPhoto(new Blob(["x"]));
    const { outboxId } = await enqueueDirect(photoId, emptyPayload());
    const row = await getOutboxRow(outboxId);
    if (!row) throw new Error("outbox row not found");
    expect(row.request_data).toEqual(buildReportData(row.payload));
    expect(row.request_data?.crisis_id).toBe("crisis-1");
  });

  it("patchOutboxPayload refreshes request_data (crisis assignment)", async () => {
    const photoId = await putPhoto(new Blob(["x"]));
    const { outboxId } = await enqueueDirect(photoId, {
      ...emptyPayload(),
      crisis_id: "",
    });
    let row = await getOutboxRow(outboxId);
    expect(row?.request_data?.crisis_id).toBe("");

    await patchOutboxPayload(outboxId, { crisis_id: "crisis-9" });
    row = await getOutboxRow(outboxId);
    if (!row) throw new Error("outbox row not found");
    expect(row.payload.crisis_id).toBe("crisis-9");
    expect(row.request_data?.crisis_id).toBe("crisis-9");
    expect(row.request_data).toEqual(buildReportData(row.payload));
  });
});
