import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { getCrises } from "../../api/reports";

describe("getCrises", () => {
  const fetchMock = vi.fn();

  beforeEach(() => {
    vi.stubGlobal("fetch", fetchMock);
  });

  afterEach(() => {
    fetchMock.mockReset();
    vi.unstubAllGlobals();
  });

  it("fetches /crises and returns the parsed array verbatim", async () => {
    const payload = [
      { id: "a", name: "Earthquake Response 2026" },
      { id: "b", name: "Other / Unspecified" },
    ];
    fetchMock.mockResolvedValueOnce({
      ok: true,
      json: async () => payload,
    });

    const result = await getCrises();

    expect(result).toEqual(payload);
    expect(fetchMock).toHaveBeenCalledWith(expect.stringMatching(/\/crises$/));
  });

  it("rejects when the response is not ok", async () => {
    fetchMock.mockResolvedValueOnce({ ok: false, status: 500, json: async () => ({}) });

    await expect(getCrises()).rejects.toThrow();
  });
});
