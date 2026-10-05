import { renderHook, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("../lib/db", () => ({ pruneExpired: vi.fn() }));
vi.mock("../lib/outbox", () => ({ flushAfterReconnect: vi.fn() }));
vi.mock("../platform/outbox", () => ({ outbox: { init: vi.fn(), requestFlush: vi.fn() } }));

import { pruneExpired } from "../lib/db";
import { useOutboxFlushTriggers } from "./useOutboxFlushTriggers";

beforeEach(() => {
  vi.mocked(pruneExpired).mockReset();
});

describe("useOutboxFlushTriggers", () => {
  it("prunes expired drafts and failed rows after boot", async () => {
    vi.mocked(pruneExpired).mockResolvedValue({ draftsPruned: 0, outboxFailedPruned: 0 });
    renderHook(() => useOutboxFlushTriggers());

    await waitFor(() => expect(pruneExpired).toHaveBeenCalledTimes(1));
  });

  it("swallows a prune failure", async () => {
    const warn = vi.spyOn(console, "warn").mockImplementation(() => {});
    vi.mocked(pruneExpired).mockRejectedValue(new Error("db closed"));
    renderHook(() => useOutboxFlushTriggers());

    await waitFor(() =>
      expect(warn).toHaveBeenCalledWith("[outbox] pruneExpired failed", expect.any(Error)),
    );
    warn.mockRestore();
  });
});
