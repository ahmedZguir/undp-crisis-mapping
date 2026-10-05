import { beforeEach, describe, expect, it, vi } from "vitest";

// Run idle callbacks synchronously so warmHomeData's effects are observable.
vi.mock("./idle", () => ({
  onIdle: (fn: () => void) => {
    fn();
    return { cancel: vi.fn() };
  },
}));
vi.mock("./cachedResource", () => ({
  prefetchResource: vi.fn().mockResolvedValue(undefined),
}));
vi.mock("./reportsCache", () => ({
  refreshReportsCache: vi.fn().mockResolvedValue(undefined),
}));
vi.mock("../api/reports", () => ({
  crisisStatsKey: (id: string) => `rid-crisis-stats:${id}`,
  getCrisisStats: vi.fn(),
}));
vi.mock("../api/reporter", () => ({
  reporterStatsKey: (id: string) => `rid-reporter-stats:${id}`,
  getReporterStats: vi.fn(),
}));

import { prefetchResource } from "./cachedResource";
import { refreshReportsCache } from "./reportsCache";
import { warmHomeData } from "./warm";

describe("warmHomeData", () => {
  beforeEach(() => vi.clearAllMocks());

  it("prefetches crisis stats, reporter stats, and the reports history", () => {
    warmHomeData("crisis-1", "client-1");
    expect(prefetchResource).toHaveBeenCalledWith(
      "rid-crisis-stats:crisis-1",
      expect.any(Function),
    );
    expect(prefetchResource).toHaveBeenCalledWith(
      "rid-reporter-stats:client-1",
      expect.any(Function),
    );
    expect(refreshReportsCache).toHaveBeenCalledTimes(1);
  });

  it("skips per-id prefetches when ids are null but still refreshes reports", () => {
    warmHomeData(null, null);
    expect(prefetchResource).not.toHaveBeenCalled();
    expect(refreshReportsCache).toHaveBeenCalledTimes(1);
  });
});
