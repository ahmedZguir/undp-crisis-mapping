// Key invariant: concurrent `refresh()` callers share one POST, or reuse detection revokes the family.

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { __resetAuthForTests, authedFetch, refresh } from "./auth";

const fetchMock = vi.fn();

beforeEach(() => {
  fetchMock.mockReset();
  vi.stubGlobal("fetch", fetchMock);
});

afterEach(() => {
  __resetAuthForTests();
});

describe("refresh()", () => {
  it("returns true and stores access token on success", async () => {
    fetchMock.mockResolvedValueOnce(
      new Response(JSON.stringify({ access_token: "abc", expires_in: 900 }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    );

    const ok = await refresh();

    expect(ok).toBe(true);
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(fetchMock.mock.calls[0]?.[1]).toMatchObject({
      method: "POST",
      credentials: "include",
    });
  });

  it("targets the same hostname as the current page (same-site cookie ride)", async () => {
    // A cross-site API host (127.0.0.1 vs localhost) makes SameSite=Lax drop the refresh cookie.
    fetchMock.mockResolvedValueOnce(
      new Response(JSON.stringify({ access_token: "abc", expires_in: 900 }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    );

    await refresh();

    const url = fetchMock.mock.calls[0]?.[0];
    expect(typeof url).toBe("string");
    expect(url as string).toMatch(
      new RegExp(`^http://${window.location.hostname}:\\d+/auth/refresh$`),
    );
  });

  it("returns false on 401", async () => {
    fetchMock.mockResolvedValueOnce(new Response("nope", { status: 401 }));

    const ok = await refresh();

    expect(ok).toBe(false);
  });

  it("dedupes overlapping calls: one network request, identical result", async () => {
    let resolveBody!: (r: Response) => void;
    const responseReady = new Promise<Response>((res) => {
      resolveBody = res;
    });
    fetchMock.mockImplementationOnce(() => responseReady);

    const a = refresh();
    const b = refresh();

    expect(fetchMock).toHaveBeenCalledTimes(1);

    resolveBody(
      new Response(JSON.stringify({ access_token: "xyz", expires_in: 900 }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    );

    const [okA, okB] = await Promise.all([a, b]);
    expect(okA).toBe(true);
    expect(okB).toBe(true);
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it("holds the in-flight slot for the grace window so back-to-back sequential callers share one POST", async () => {
    // Matters even for sequential callers ~1ms apart: a second POST with the same jti burns the family.
    fetchMock.mockResolvedValueOnce(
      new Response(JSON.stringify({ access_token: "t1", expires_in: 900 }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    );

    expect(await refresh()).toBe(true);
    // Within the grace window: cached, no new network call.
    expect(await refresh()).toBe(true);
    expect(await refresh()).toBe(true);
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });
});

describe("authedFetch()", () => {
  it("short-circuits to a 401 Response when refresh fails and no token is available", async () => {
    // An unauthenticated request would 401 and trigger a second refresh (a 401 cascade).
    fetchMock.mockResolvedValueOnce(
      new Response(JSON.stringify({ detail: "refresh token reuse" }), { status: 401 }),
    );

    const res = await authedFetch("http://example.test/admin/x");

    expect(res.status).toBe(401);
    expect(fetchMock).toHaveBeenCalledTimes(1);
    const call = fetchMock.mock.calls[0]?.[0];
    expect(typeof call === "string" ? call : (call as URL | Request).toString()).toContain(
      "/auth/refresh",
    );
  });
});
