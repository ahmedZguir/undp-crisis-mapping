import { act, renderHook, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { prefetchResource, readResource, useCachedResource, writeResource } from "./cachedResource";

afterEach(() => {
  localStorage.clear();
  vi.restoreAllMocks();
});

describe("readResource / writeResource", () => {
  it("returns null on a cache miss", () => {
    expect(readResource("rid-test:missing")).toBeNull();
  });

  it("round-trips a written value", () => {
    writeResource("rid-test:x", { n: 42 });
    expect(readResource<{ n: number }>("rid-test:x")).toEqual({ n: 42 });
  });

  it("returns null on a corrupt envelope", () => {
    localStorage.setItem("rid-test:bad", "{not json");
    expect(readResource("rid-test:bad")).toBeNull();
  });
});

describe("prefetchResource", () => {
  it("writes the fetched value into the cache", async () => {
    await prefetchResource("rid-test:pf", async () => ({ ok: true }));
    expect(readResource("rid-test:pf")).toEqual({ ok: true });
  });

  it("coalesces concurrent calls for the same key into one fetch", async () => {
    const fetcher = vi.fn(async () => {
      await Promise.resolve();
      return "v";
    });
    await Promise.all([
      prefetchResource("rid-test:co", fetcher),
      prefetchResource("rid-test:co", fetcher),
      prefetchResource("rid-test:co", fetcher),
    ]);
    expect(fetcher).toHaveBeenCalledTimes(1);
  });

  it("swallows fetcher errors (best-effort) and leaves no cache entry", async () => {
    await prefetchResource("rid-test:err", async () => {
      throw new Error("network");
    });
    expect(readResource("rid-test:err")).toBeNull();
  });
});

describe("useCachedResource", () => {
  it("renders the cached value synchronously on first paint (no null flash)", () => {
    writeResource("rid-test:hook", { count: 7 });
    const { result } = renderHook(() =>
      useCachedResource("rid-test:hook", async () => ({ count: 7 })),
    );
    // First render already has the cached value — never null.
    expect(result.current.data).toEqual({ count: 7 });
  });

  it("revalidates and updates state from the fetcher", async () => {
    const { result } = renderHook(() =>
      useCachedResource("rid-test:reval", async () => ({ count: 99 })),
    );
    expect(result.current.data).toBeNull();
    await waitFor(() => expect(result.current.data).toEqual({ count: 99 }));
  });

  it("does not fetch when the key is null", async () => {
    const fetcher = vi.fn(async () => "x");
    const { result } = renderHook(() => useCachedResource(null, fetcher));
    await act(async () => {
      await Promise.resolve();
    });
    expect(result.current.data).toBeNull();
    expect(fetcher).not.toHaveBeenCalled();
  });
});
