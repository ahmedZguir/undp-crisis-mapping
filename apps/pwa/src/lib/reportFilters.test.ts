import { describe, expect, it } from "vitest";
import type { AdminReportListItem } from "../types/admin";
import {
  DEFAULT_REPORT_FILTERS,
  type ReportFilters,
  applyClientFilters,
  resolveTimeRange,
  timeFilterActive,
} from "./reportFilters";

// Fixed "now" so relative-window assertions are deterministic. Reports below
// predate it by years, the case the custom range exists for.
const NOW = Date.parse("2026-06-15T12:00:00Z");
const DAY = 24 * 60 * 60 * 1000;

function filters(over: Partial<ReportFilters>): ReportFilters {
  return { ...DEFAULT_REPORT_FILTERS, ...over };
}

function report(createdAt: string): AdminReportListItem {
  // Only `created_at` matters for the time filter; the rest is filler the
  // other predicates ignore when their filters are "any".
  return {
    id: createdAt,
    created_at: createdAt,
    debris: null,
    location_source: "submitted_pin",
    infra_type: null,
  } as unknown as AdminReportListItem;
}

describe("resolveTimeRange", () => {
  it("maps a relative window to a from-only bound anchored on now", () => {
    expect(resolveTimeRange(filters({ timeWindow: "7d" }), NOW)).toEqual({
      fromMs: NOW - 7 * DAY,
      toMs: null,
    });
  });

  it("leaves both ends open for 'any'", () => {
    expect(resolveTimeRange(filters({ timeWindow: "any" }), NOW)).toEqual({
      fromMs: null,
      toMs: null,
    });
  });

  it("treats a custom range as inclusive UTC day boundaries", () => {
    const { fromMs, toMs } = resolveTimeRange(
      filters({ timeWindow: "custom", customFrom: "2023-02-06", customTo: "2023-02-08" }),
      NOW,
    );
    expect(fromMs).toBe(Date.parse("2023-02-06T00:00:00Z"));
    // End of 2023-02-08, not its start: the whole picked day is included.
    expect(toMs).toBe(Date.parse("2023-02-09T00:00:00Z") - 1);
  });

  it("supports an open-ended custom range (from only, to only)", () => {
    expect(
      resolveTimeRange(filters({ timeWindow: "custom", customFrom: "2023-02-06" }), NOW),
    ).toEqual({ fromMs: Date.parse("2023-02-06T00:00:00Z"), toMs: null });
    expect(
      resolveTimeRange(filters({ timeWindow: "custom", customTo: "2023-02-08" }), NOW),
    ).toEqual({ fromMs: null, toMs: Date.parse("2023-02-09T00:00:00Z") - 1 });
  });

  it("swaps a reversed custom range instead of returning an empty span", () => {
    const { fromMs, toMs } = resolveTimeRange(
      filters({ timeWindow: "custom", customFrom: "2023-02-08", customTo: "2023-02-06" }),
      NOW,
    );
    expect(fromMs).toBe(Date.parse("2023-02-06T00:00:00Z"));
    expect(toMs).toBe(Date.parse("2023-02-09T00:00:00Z") - 1);
    expect(fromMs ?? 0).toBeLessThan(toMs ?? 0);
  });
});

describe("timeFilterActive", () => {
  it("is false for 'any' and for a custom window with no bounds", () => {
    expect(timeFilterActive(filters({ timeWindow: "any" }))).toBe(false);
    expect(timeFilterActive(filters({ timeWindow: "custom" }))).toBe(false);
  });

  it("is true for any relative window and for a bounded custom window", () => {
    expect(timeFilterActive(filters({ timeWindow: "24h" }))).toBe(true);
    expect(timeFilterActive(filters({ timeWindow: "custom", customTo: "2023-02-08" }))).toBe(true);
  });
});

describe("applyClientFilters with a custom range", () => {
  const items = [
    report("2023-02-05T23:00:00Z"), // before window
    report("2023-02-06T00:00:00Z"), // first instant of `from` day
    report("2023-02-08T23:59:59Z"), // last instant of `to` day
    report("2023-02-09T00:30:00Z"), // after window
  ];

  it("keeps only reports within the inclusive day boundaries", () => {
    const kept = applyClientFilters(
      items,
      filters({ timeWindow: "custom", customFrom: "2023-02-06", customTo: "2023-02-08" }),
    );
    expect(kept.map((r) => r.created_at)).toEqual(["2023-02-06T00:00:00Z", "2023-02-08T23:59:59Z"]);
  });

  it("does not filter on time when the custom window has no bounds", () => {
    const kept = applyClientFilters(items, filters({ timeWindow: "custom" }));
    expect(kept).toHaveLength(items.length);
  });
});
