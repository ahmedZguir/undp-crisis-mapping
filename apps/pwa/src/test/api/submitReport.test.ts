import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { submitReport } from "../../api/reports";
import type { ReportPayload } from "../../types";

function basePayload(overrides: Partial<ReportPayload> = {}): ReportPayload {
  return {
    photo: new File(["x"], "p.jpg", { type: "image/jpeg" }),
    crisis_id: "crisis-1",
    damage_class: "minimal",
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
    ...overrides,
  };
}

function lastSentData(fetchMock: ReturnType<typeof vi.fn>): Record<string, unknown> {
  const call = fetchMock.mock.calls.at(-1);
  if (!call) throw new Error("fetch was not called");
  const init = call[1] as { body: FormData };
  const data = init.body.get("data");
  return JSON.parse(String(data)) as Record<string, unknown>;
}

describe("submitReport wire-boundary mapping", () => {
  const fetchMock = vi.fn();

  beforeEach(() => {
    vi.stubGlobal("fetch", fetchMock);
    fetchMock.mockResolvedValue({
      ok: true,
      status: 201,
      json: async () => ({ id: "r-1" }),
    });
  });

  afterEach(() => {
    fetchMock.mockReset();
    vi.unstubAllGlobals();
  });

  it("renames crisis_nature_type→crisis_type and crisis_nature→crisis_type_detailed verbatim", async () => {
    await submitReport(
      basePayload({
        crisis_nature_type: "natural_hazards",
        crisis_nature: "Earthquake",
      }),
    );

    const data = lastSentData(fetchMock);
    expect(data.crisis_type).toBe("natural_hazards");
    expect(data.crisis_type_detailed).toBe("Earthquake");
    expect(data).not.toHaveProperty("crisis_nature_type");
    expect(data).not.toHaveProperty("crisis_nature");
  });

  it("omits infra_type when the multi-select is empty", async () => {
    await submitReport(basePayload({ infra_type: [] }));
    const data = lastSentData(fetchMock);
    expect(data).not.toHaveProperty("infra_type");
  });

  it("sends canonical infra_type selections as a string array", async () => {
    await submitReport(basePayload({ infra_type: ["residential", "transport"] }));
    const data = lastSentData(fetchMock);
    expect(data.infra_type).toEqual(["residential", "transport"]);
  });

  it("replaces the 'other' marker with infra_type_other inside the array", async () => {
    await submitReport(
      basePayload({
        infra_type: ["residential", "other"],
        infra_type_other: "floating dock",
      }),
    );
    const data = lastSentData(fetchMock);
    expect(data.infra_type).toEqual(["residential", "floating dock"]);
  });

  it("drops the 'other' marker when no free-text value was provided", async () => {
    await submitReport(
      basePayload({
        infra_type: ["residential", "other"],
        infra_type_other: "",
      }),
    );
    const data = lastSentData(fetchMock);
    expect(data.infra_type).toEqual(["residential"]);
  });

  it("substitutes crisis_nature_other when crisis_nature is 'Other'", async () => {
    await submitReport(
      basePayload({
        crisis_nature: "Other",
        crisis_nature_other: "volcanic ash fall",
      }),
    );
    const data = lastSentData(fetchMock);
    expect(data.crisis_type_detailed).toBe("volcanic ash fall");
  });

  it("sends infra_name, description, debris as discrete fields without a description mash-up", async () => {
    await submitReport(
      basePayload({
        infra_type: ["residential"],
        infra_name: "Bridge 7",
        description: "cracked deck",
        debris: "yes",
      }),
    );
    const data = lastSentData(fetchMock);
    expect(data.infra_name).toBe("Bridge 7");
    expect(data.description).toBe("cracked deck");
    expect(data.debris).toBe("yes");
  });

  it("omits deferred fields (electricity, health_services, pressing_needs) when not set", async () => {
    await submitReport(
      basePayload({
        electricity: "none",
        health_services: "limited",
        pressing_needs: ["water", "shelter"],
      }),
    );
    const data = lastSentData(fetchMock);
    expect(data).not.toHaveProperty("electricity");
    expect(data).not.toHaveProperty("health_services");
    expect(data).not.toHaveProperty("pressing_needs");
  });

  it("rides crisis_id, client_id, damage_class, and location on the wire", async () => {
    await submitReport(
      basePayload({
        crisis_id: "c-42",
        damage_class: "partial",
        latitude: 1.5,
        longitude: 2.5,
        client_id: "client-uuid-x",
      }),
    );
    const data = lastSentData(fetchMock);
    expect(data.crisis_id).toBe("c-42");
    expect(data.damage_class).toBe("partial");
    expect(data.client_id).toBe("client-uuid-x");
    expect(data.location).toEqual({ lat: 1.5, lng: 2.5 });
    // device_id is no longer sent.
    expect(data).not.toHaveProperty("device_id");
  });

  it("omits client_id when not provided", async () => {
    await submitReport(basePayload({}));
    const data = lastSentData(fetchMock);
    expect(data).not.toHaveProperty("client_id");
    expect(data).not.toHaveProperty("device_id");
  });

  it("surfaces a 409 response as a generic Error (no CrisisArchivedError special-casing)", async () => {
    fetchMock.mockReset();
    fetchMock.mockResolvedValueOnce({
      ok: false,
      status: 409,
      json: async () => ({ error_code: "crisis_archived_or_unknown" }),
    });

    await expect(submitReport(basePayload())).rejects.toThrow(/409/);
  });

  it("does not export CrisisArchivedError", async () => {
    const mod = (await import("../../api/reports")) as Record<string, unknown>;
    expect(mod.CrisisArchivedError).toBeUndefined();
  });

  it("includes building_id in the request body when defined", async () => {
    await submitReport(basePayload({ building_id: "gers-building-123" }));
    const data = lastSentData(fetchMock);
    expect(data.building_id).toBe("gers-building-123");
  });

  it("omits building_id from the request body when undefined", async () => {
    await submitReport(basePayload({}));
    const data = lastSentData(fetchMock);
    expect(data).not.toHaveProperty("building_id");
  });

  it("forwards client_submission_id when present so the backend can dedupe", async () => {
    await submitReport(basePayload({ client_submission_id: "csid-123" }));
    const data = lastSentData(fetchMock);
    expect(data.client_submission_id).toBe("csid-123");
  });

  it("omits client_submission_id from the request body when undefined", async () => {
    await submitReport(basePayload({}));
    const data = lastSentData(fetchMock);
    expect(data).not.toHaveProperty("client_submission_id");
  });
});
