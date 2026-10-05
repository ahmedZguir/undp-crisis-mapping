import { beforeEach, describe, expect, it } from "vitest";
import type { SearchRequest } from "../api/search";
import {
  readSummaryCache,
  readWorkspace,
  summarySignature,
  writeSummaryCache,
  writeWorkspace,
} from "./adminWorkspace";

beforeEach(() => {
  sessionStorage.clear();
});

describe("readWorkspace / writeWorkspace", () => {
  it("round-trips a value", () => {
    writeWorkspace("slice", { a: 1, b: "x" });
    expect(readWorkspace("slice", null)).toEqual({ a: 1, b: "x" });
  });

  it("returns the fallback when the key is absent", () => {
    expect(readWorkspace("missing", { fallback: true })).toEqual({ fallback: true });
  });

  it("returns the fallback (not a throw) on corrupt JSON", () => {
    sessionStorage.setItem("rid-admin-ws:bad", "{not json");
    expect(readWorkspace("bad", "fallback")).toBe("fallback");
  });
});

describe("summarySignature", () => {
  const base: SearchRequest = {
    query: "flooded schools",
    strictness: "balanced",
    damage_class: "complete",
    debris: "any",
    location_kind: "any",
  };

  it("is stable across map viewport (bbox) changes", () => {
    const a: SearchRequest = { ...base, location: { bbox: [0, 0, 1, 1] } };
    const b: SearchRequest = { ...base, location: { bbox: [10, 10, 11, 11] } };
    expect(summarySignature(a)).toBe(summarySignature(b));
  });

  it("ignores the row limit", () => {
    expect(summarySignature({ ...base, limit: 1 })).toBe(summarySignature({ ...base, limit: 500 }));
  });

  it("a bbox-only location equals no location", () => {
    expect(summarySignature({ ...base, location: { bbox: [0, 0, 1, 1] } })).toBe(
      summarySignature(base),
    );
  });

  it("changes when a real filter changes", () => {
    expect(summarySignature(base)).not.toBe(summarySignature({ ...base, strictness: "strict" }));
    expect(summarySignature(base)).not.toBe(summarySignature({ ...base, query: "fires" }));
    expect(summarySignature(base)).not.toBe(summarySignature({ ...base, damage_class: "minimal" }));
  });

  it("distinguishes a chosen area from a bare viewport", () => {
    const viewport: SearchRequest = { ...base, location: { bbox: [0, 0, 1, 1] } };
    const area: SearchRequest = {
      ...base,
      location: { bbox: [0, 0, 1, 1], division_ids: ["d1"] },
    };
    expect(summarySignature(viewport)).not.toBe(summarySignature(area));
  });
});

describe("summary cache", () => {
  it("round-trips frames keyed by crisis", () => {
    const frames = [{ kind: "prose", text: "hi", id: 1 }];
    writeSummaryCache("crisis-a", "sig-1", frames, 1234);
    const got = readSummaryCache<{ kind: string; text: string; id: number }>("crisis-a");
    expect(got).toEqual({ sig: "sig-1", frames, generatedAt: 1234 });
  });

  it("is scoped per crisis", () => {
    writeSummaryCache("crisis-a", "sig-1", [{ id: 1 }], 1);
    expect(readSummaryCache("crisis-b")).toBeNull();
  });
});
