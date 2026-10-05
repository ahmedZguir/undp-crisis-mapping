// @vitest-environment node
//
// Runs the SQLite backend against real node:sqlite. Node environment so vite doesn't try to bundle it.

import { DatabaseSync } from "node:sqlite";
import { beforeEach, describe, expect, it } from "vitest";
import {
  CLAIM_LEASE_MS,
  MAX_OUTBOX_ATTEMPTS,
  type OutboxRow,
  backoffDelayMs,
} from "../outbox-core";
import type { SqlExecutor, SqlParams } from "./sql/executor";
import { createSqliteBackend } from "./sqliteBackend";
import type { DraftRow } from "./types";

function nodeExecutor(): SqlExecutor {
  const db = new DatabaseSync(":memory:");
  const p = (params?: SqlParams) => (params ? [...params] : []);
  return {
    async run(sql, params) {
      db.prepare(sql).run(...p(params));
    },
    async all<T = Record<string, unknown>>(sql: string, params?: SqlParams): Promise<T[]> {
      return db.prepare(sql).all(...p(params)) as T[];
    },
    async get<T = Record<string, unknown>>(
      sql: string,
      params?: SqlParams,
    ): Promise<T | undefined> {
      return db.prepare(sql).get(...p(params)) as T | undefined;
    },
    async tx<R>(fn: () => Promise<R>): Promise<R> {
      db.exec("BEGIN");
      try {
        const r = await fn();
        db.exec("COMMIT");
        return r;
      } catch (err) {
        db.exec("ROLLBACK");
        throw err;
      }
    },
    async close() {
      db.close();
    },
  };
}

function makeOutboxRow(id: string, overrides: Partial<OutboxRow> = {}): OutboxRow {
  return {
    id,
    client_submission_id: `csid-${id}`,
    // biome-ignore lint/suspicious/noExplicitAny: minimal payload stub for storage tests
    payload: { crisis_id: "c1", damage_class: "partial" } as any,
    photo_id: `photo-${id}`,
    status: "queued",
    attempt_count: 0,
    next_attempt_at: 0,
    last_error: null,
    created_at: 1000,
    updated_at: 1000,
    ...overrides,
  };
}

let backend: ReturnType<typeof createSqliteBackend>;

beforeEach(() => {
  const exec = nodeExecutor();
  backend = createSqliteBackend(async () => exec);
});

describe("meta", () => {
  it("round-trips client_id", async () => {
    await backend.setMeta("client_id", "abc-123");
    expect(await backend.getMeta<string>("client_id")).toBe("abc-123");
    expect(await backend.getMeta("missing")).toBeUndefined();
  });
});

describe("photos", () => {
  it("round-trips blob bytes and mime through SQLite", async () => {
    const id = await backend.putPhoto(new Blob(["hello-bytes"], { type: "image/jpeg" }));
    const out = await backend.getPhoto(id);
    expect(out).not.toBeNull();
    expect(out?.type).toBe("image/jpeg");
    expect(await out?.text()).toBe("hello-bytes");
  });
  it("returns null for a missing photo", async () => {
    expect(await backend.getPhoto("nope")).toBeNull();
  });
});

describe("outbox CRUD + ordering", () => {
  it("loads rows ordered by created_at", async () => {
    await backend.putOutboxRow(makeOutboxRow("a", { created_at: 3 }));
    await backend.putOutboxRow(makeOutboxRow("b", { created_at: 1 }));
    await backend.putOutboxRow(makeOutboxRow("c", { created_at: 2 }));
    expect((await backend.loadOutbox()).map((r) => r.id)).toEqual(["b", "c", "a"]);
  });
});

describe("claimOutboxRow", () => {
  it("transitions queued → submitting and a second claim no-ops", async () => {
    await backend.putOutboxRow(makeOutboxRow("o1"));
    expect((await backend.claimOutboxRow("o1"))?.status).toBe("submitting");
    expect(await backend.claimOutboxRow("o1")).toBeNull();
  });
  it("won't claim a row whose backoff is in the future", async () => {
    await backend.putOutboxRow(makeOutboxRow("o1", { next_attempt_at: Date.now() + 60_000 }));
    expect(await backend.claimOutboxRow("o1")).toBeNull();
  });
  it("reclaims a row whose claim outlived the lease", async () => {
    const stale = Date.now() - CLAIM_LEASE_MS - 1;
    await backend.putOutboxRow(makeOutboxRow("o1", { status: "submitting", updated_at: stale }));
    expect((await backend.claimOutboxRow("o1"))?.status).toBe("submitting");
    expect(await backend.claimOutboxRow("o1")).toBeNull();
  });
});

describe("markOutboxResult", () => {
  const opts = { maxAttempts: MAX_OUTBOX_ATTEMPTS, backoff: backoffDelayMs };

  it("success deletes the row and its photo", async () => {
    const photoId = await backend.putPhoto(new Blob(["x"]));
    await backend.putOutboxRow(makeOutboxRow("o1", { photo_id: photoId }));
    await backend.markOutboxResult("o1", { kind: "success" }, opts);
    expect(await backend.getOutboxRow("o1")).toBeUndefined();
    expect(await backend.getPhoto(photoId)).toBeNull();
  });

  it("transient re-queues with backoff and increments attempt_count", async () => {
    await backend.putOutboxRow(makeOutboxRow("o1"));
    await backend.markOutboxResult("o1", { kind: "transient", error: "503" }, opts);
    const row = await backend.getOutboxRow("o1");
    expect(row?.status).toBe("queued");
    expect(row?.attempt_count).toBe(1);
    expect(row?.next_attempt_at).toBeGreaterThan(Date.now());
  });

  it("transient at maxAttempts downgrades to failed", async () => {
    await backend.putOutboxRow(makeOutboxRow("o1", { attempt_count: MAX_OUTBOX_ATTEMPTS - 1 }));
    await backend.markOutboxResult("o1", { kind: "transient", error: "503" }, opts);
    expect((await backend.getOutboxRow("o1"))?.status).toBe("failed");
  });

  it("terminal marks failed", async () => {
    await backend.putOutboxRow(makeOutboxRow("o1"));
    await backend.markOutboxResult("o1", { kind: "terminal", error: "bad" }, opts);
    const row = await backend.getOutboxRow("o1");
    expect(row?.status).toBe("failed");
    expect(row?.last_error).toBe("bad");
  });
});

describe("cancelOutboxRow", () => {
  it("refuses to cancel a submitting row", async () => {
    await backend.putOutboxRow(makeOutboxRow("o1"));
    await backend.claimOutboxRow("o1");
    expect(await backend.cancelOutboxRow("o1")).toBe(false);
    expect((await backend.getOutboxRow("o1"))?.status).toBe("submitting");
  });
  it("deletes a queued row and its photo", async () => {
    const photoId = await backend.putPhoto(new Blob(["x"]));
    await backend.putOutboxRow(makeOutboxRow("o1", { photo_id: photoId }));
    expect(await backend.cancelOutboxRow("o1")).toBe(true);
    expect(await backend.getOutboxRow("o1")).toBeUndefined();
    expect(await backend.getPhoto(photoId)).toBeNull();
  });
});

describe("promoteDraftToOutbox", () => {
  it("removes the draft and inserts the outbox row atomically", async () => {
    const draft: DraftRow = {
      id: "d1",
      // biome-ignore lint/suspicious/noExplicitAny: minimal state stub
      state: {} as any,
      photo_id: "p1",
      step: 0,
      updated_at: 1,
    };
    await backend.saveDraft(draft);
    await backend.promoteDraftToOutbox("d1", makeOutboxRow("o1"));
    expect(await backend.getDraft("d1")).toBeUndefined();
    expect((await backend.loadOutbox()).map((r) => r.id)).toEqual(["o1"]);
  });
});

describe("resetQueuedBackoff", () => {
  it("clears future backoff on queued rows and reports the count", async () => {
    await backend.putOutboxRow(makeOutboxRow("o1", { next_attempt_at: Date.now() + 60_000 }));
    await backend.putOutboxRow(makeOutboxRow("o2", { next_attempt_at: 0 }));
    expect(await backend.resetQueuedBackoff()).toBe(1);
    expect((await backend.getOutboxRow("o1"))?.next_attempt_at).toBe(0);
  });
});

describe("pruneExpired", () => {
  it("drops only failed outbox rows past the TTL", async () => {
    const old = 1000;
    const now = old + 40 * 24 * 60 * 60 * 1000;
    await backend.putOutboxRow(makeOutboxRow("failed-old", { status: "failed", updated_at: old }));
    await backend.putOutboxRow(makeOutboxRow("queued-old", { status: "queued", updated_at: old }));
    const res = await backend.pruneExpired(now);
    expect(res.outboxFailedPruned).toBe(1);
    expect(await backend.getOutboxRow("failed-old")).toBeUndefined();
    expect(await backend.getOutboxRow("queued-old")).toBeDefined();
  });
});
