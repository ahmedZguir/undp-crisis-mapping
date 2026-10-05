import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { suggestDamageClass } from "../../api/ai";

// Every failure mode collapses to `null` — no pre-selection, citizen picks
// manually. The helper never throws.

const photo = () => new File(["x"], "p.jpg", { type: "image/jpeg" });

describe("suggestDamageClass", () => {
  const fetchMock = vi.fn();

  beforeEach(() => {
    vi.stubGlobal("fetch", fetchMock);
  });

  afterEach(() => {
    fetchMock.mockReset();
    vi.unstubAllGlobals();
  });

  it("returns the label on a 200 with a valid damage_class", async () => {
    fetchMock.mockResolvedValue({ ok: true, json: async () => ({ damage_class: "complete" }) });
    await expect(suggestDamageClass(photo(), "client-1")).resolves.toBe("complete");
  });

  it("POSTs multipart with the photo part and an X-Client-Id header", async () => {
    fetchMock.mockResolvedValue({ ok: true, json: async () => ({ damage_class: "partial" }) });
    await suggestDamageClass(photo(), "client-xyz");
    const [, init] = fetchMock.mock.calls.at(-1) as [string, RequestInit];
    expect(init.method).toBe("POST");
    expect((init.headers as Record<string, string>)["X-Client-Id"]).toBe("client-xyz");
    expect((init.body as FormData).get("photo")).toBeInstanceOf(Blob);
  });

  it("returns null on a non-200 (e.g. 503 classifier unavailable)", async () => {
    fetchMock.mockResolvedValue({ ok: false, status: 503, json: async () => ({}) });
    await expect(suggestDamageClass(photo(), "c")).resolves.toBeNull();
  });

  it("returns null when the body carries a non-label", async () => {
    fetchMock.mockResolvedValue({ ok: true, json: async () => ({ damage_class: "totally_gone" }) });
    await expect(suggestDamageClass(photo(), "c")).resolves.toBeNull();
  });

  it("returns null on a network/abort error instead of throwing", async () => {
    fetchMock.mockRejectedValue(new Error("network down"));
    await expect(suggestDamageClass(photo(), "c")).resolves.toBeNull();
  });
});
