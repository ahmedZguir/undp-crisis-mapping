import { act, renderHook, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import * as adminApi from "../../api/admin";
import { useAnalysisReports } from "../../hooks/useAnalysisReports";
import type { CrisisReportDetail, CrisisReportListItem } from "../../types/admin";

vi.mock("../../api/admin", () => ({
  createAnalysisReport: vi.fn(),
  listAnalysisReports: vi.fn(),
  getAnalysisReport: vi.fn(),
}));

const createMock = vi.mocked(adminApi.createAnalysisReport);
const listMock = vi.mocked(adminApi.listAnalysisReports);
const getMock = vi.mocked(adminApi.getAnalysisReport);

const CRISIS = "crisis-1";

function detail(over: Partial<CrisisReportDetail>): CrisisReportDetail {
  return {
    id: "r1",
    crisis_id: CRISIS,
    created_at: "2026-06-03T00:00:00Z",
    created_by: "coord-1",
    created_by_email: null,
    status: "running",
    phase: "analysing",
    progress_count: null,
    progress_total: null,
    error: null,
    ended_at: null,
    report_count: null,
    device_count: null,
    building_count: null,
    coverage_pct: null,
    download_url: null,
    ...over,
  };
}

describe("useAnalysisReports", () => {
  beforeEach(() => {
    listMock.mockResolvedValue([]);
    createMock.mockResolvedValue({
      id: "r1",
      status: "running",
      created_at: "2026-06-03T00:00:00Z",
    });
  });

  afterEach(() => {
    vi.clearAllMocks();
    vi.useRealTimers();
  });

  it("loads the history list on mount", async () => {
    const rows: CrisisReportListItem[] = [
      {
        id: "r0",
        created_at: "2026-06-02T00:00:00Z",
        created_by: "coord-1",
        created_by_email: null,
        status: "succeeded",
        phase: null,
        report_count: 5,
        coverage_pct: 12.5,
        download_ready: true,
      },
    ];
    listMock.mockResolvedValue(rows);

    const { result } = renderHook(() => useAnalysisReports(CRISIS));

    await waitFor(() => expect(result.current.list).toEqual(rows));
    expect(listMock).toHaveBeenCalledWith(CRISIS);
  });

  it("polls the detail endpoint and stops once status is terminal", async () => {
    vi.useFakeTimers();
    // First poll: still running. Second poll: succeeded (terminal).
    getMock
      .mockResolvedValueOnce(detail({ status: "running", phase: "rendering" }))
      .mockResolvedValueOnce(
        detail({ status: "succeeded", phase: null, download_url: "https://signed/r1.pdf" }),
      );

    const { result } = renderHook(() => useAnalysisReports(CRISIS));

    // generate() POSTs then begins polling. The state updates it triggers
    // (and the polling effect they schedule) must flush inside act().
    await act(async () => {
      await result.current.generate();
    });

    // First tick.
    await act(async () => {
      await vi.advanceTimersByTimeAsync(0);
    });
    expect(getMock).toHaveBeenCalledTimes(1);

    // Second tick after the 2s interval: terminal, so polling stops.
    await act(async () => {
      await vi.advanceTimersByTimeAsync(2000);
    });
    expect(getMock).toHaveBeenCalledTimes(2);
    expect(result.current.active?.status).toBe("succeeded");
    expect(result.current.active?.download_url).toBe("https://signed/r1.pdf");

    // No further polling after a terminal status, even as time advances.
    await act(async () => {
      await vi.advanceTimersByTimeAsync(10000);
    });
    expect(getMock).toHaveBeenCalledTimes(2);
  });

  it("exposes no download_url while the report is still running", async () => {
    vi.useFakeTimers();
    getMock.mockResolvedValue(detail({ status: "running", phase: "analysing" }));

    const { result } = renderHook(() => useAnalysisReports(CRISIS));
    await act(async () => {
      await result.current.generate();
    });
    await act(async () => {
      await vi.advanceTimersByTimeAsync(0);
    });

    expect(result.current.active?.status).toBe("running");
    expect(result.current.active?.download_url).toBeNull();
  });

  it("fetchDetail re-fetches a single report (fresh signed URL on download)", async () => {
    getMock.mockResolvedValue(
      detail({ status: "succeeded", download_url: "https://fresh/r1.pdf" }),
    );

    const { result } = renderHook(() => useAnalysisReports(CRISIS));
    const fresh = await result.current.fetchDetail("r1");

    expect(getMock).toHaveBeenCalledWith(CRISIS, "r1");
    expect(fresh.download_url).toBe("https://fresh/r1.pdf");
  });
});
