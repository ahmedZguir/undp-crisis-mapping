import { readFileSync } from "node:fs";
import path from "node:path";
import { deleteDB } from "idb";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { ReportPayload } from "../types";
import { _resetForTests, loadOutbox, putOutboxRow, putPhoto } from "./db";
import * as OutboxCore from "./outbox-core";
import type { OutboxRow } from "./outbox-core";

// Runs the real public/sw-outbox.js on rows written by idbBackend, so the stored shape can't drift.
const SW_SOURCE = readFileSync(path.resolve(process.cwd(), "public/sw-outbox.js"), "utf8");

type SyncHandler = (event: { tag: string; waitUntil: (p: Promise<unknown>) => void }) => void;

function loadSw(): SyncHandler {
  let handler: SyncHandler | null = null;
  const fakeSelf = {
    OutboxCore,
    UNDP_API_BASE: "https://api.test",
    addEventListener: (type: string, fn: SyncHandler) => {
      if (type === "sync") handler = fn;
    },
  };
  new Function("self", SW_SOURCE)(fakeSelf);
  if (!handler) throw new Error("sw-outbox.js registered no sync handler");
  return handler;
}

async function fireSync(handler: SyncHandler): Promise<void> {
  let done: Promise<unknown> = Promise.resolve();
  handler({
    tag: "outbox-flush",
    waitUntil: (p) => {
      done = p;
    },
  });
  await done;
}

function queuedRow(photoId: string | null): OutboxRow {
  const now = Date.now();
  const payload: Omit<ReportPayload, "photo"> = {
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
  return {
    id: crypto.randomUUID(),
    client_submission_id: crypto.randomUUID(),
    payload,
    photo_id: photoId,
    status: "queued",
    attempt_count: 0,
    next_attempt_at: 0,
    last_error: null,
    created_at: now,
    updated_at: now,
  };
}

const fetchMock = vi.fn();

beforeEach(async () => {
  await _resetForTests();
  await deleteDB("undp-app");
  fetchMock.mockReset();
  vi.stubGlobal("fetch", fetchMock);
  vi.spyOn(console, "log").mockImplementation(() => {});
  vi.spyOn(console, "warn").mockImplementation(() => {});
});

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe("sw-outbox background sync", () => {
  it("submits a photo stored by idbBackend.putPhoto instead of marking it missing", async () => {
    const photoId = await putPhoto(new Blob(["jpeg-bytes"], { type: "image/jpeg" }));
    await putOutboxRow(queuedRow(photoId));
    fetchMock.mockResolvedValue(new Response("{}", { status: 201 }));

    await fireSync(loadSw());

    expect(fetchMock).toHaveBeenCalledTimes(1);
    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe("https://api.test/reports");
    const photo = (init.body as FormData).get("photo") as Blob;
    expect(photo).toBeInstanceOf(Blob);
    expect(photo.type).toBe("image/jpeg");
    expect(photo.size).toBe("jpeg-bytes".length);
    expect(await loadOutbox()).toEqual([]);
  });
});

describe("sw-outbox claim lease", () => {
  it("re-sends a row left in `submitting` by a claimer that died mid-POST", async () => {
    const stuck = { ...queuedRow(null), status: "submitting" as const };
    stuck.updated_at = Date.now() - OutboxCore.CLAIM_LEASE_MS - 1;
    await putOutboxRow(stuck);
    fetchMock.mockResolvedValue(new Response("{}", { status: 201 }));

    await fireSync(loadSw());

    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(await loadOutbox()).toEqual([]);
  });

  it("leaves a live claim alone", async () => {
    await putOutboxRow({ ...queuedRow(null), status: "submitting" });

    await fireSync(loadSw());

    expect(fetchMock).not.toHaveBeenCalled();
    expect((await loadOutbox())[0]?.status).toBe("submitting");
  });
});
