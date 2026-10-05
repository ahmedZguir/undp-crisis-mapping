import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { AdminReportDetail } from "../../types/admin";

vi.mock("../../api/admin", () => ({
  getAdminReportDetail: vi.fn(),
  verifyAdminReport: vi.fn(),
}));
vi.mock("../../hooks/useAdminTimezone", () => ({
  useAdminTimezone: () => "UTC",
}));

import { getAdminReportDetail, verifyAdminReport } from "../../api/admin";
import { AdminReportInspector } from "./AdminReportInspector";

const DETAIL: AdminReportDetail = {
  id: "r1",
  crisis_id: "c1",
  damage_class: "partial",
  description: "wall cracked",
  infra_type: null,
  infra_name: null,
  crisis_type: null,
  crisis_type_detailed: null,
  debris: null,
  building_id: null,
  building_name: null,
  client_id: null,
  client_submission_id: null,
  location: null,
  building_centroid: null,
  location_source: null,
  photo_path: null,
  photo_url: null,
  created_at: "2026-10-01T10:00:00Z",
  description_lang: null,
  description_en: null,
  description_status: null,
  route_description: null,
  route_description_lang: null,
  route_description_en: null,
  route_description_status: null,
  ai_caption: null,
  ai_caption_status: null,
  confidence_score: null,
  verified: false,
  reporter_stats: null,
  quality: null,
};

beforeEach(() => {
  vi.mocked(getAdminReportDetail).mockReset().mockResolvedValue(DETAIL);
  vi.mocked(verifyAdminReport).mockReset();
});

describe("AdminReportInspector verify", () => {
  it("re-enables the button and shows the error when verify fails", async () => {
    vi.mocked(verifyAdminReport).mockRejectedValue(new Error("403 forbidden"));
    render(<AdminReportInspector reportId="r1" />);

    await userEvent.click(await screen.findByRole("button", { name: "Verify" }));

    expect(await screen.findByText("403 forbidden")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Verify" })).toBeEnabled();
  });

  it("shows the verified state on success", async () => {
    vi.mocked(verifyAdminReport).mockResolvedValue({
      verified: true,
      confidence_score: 0.9,
    } as Awaited<ReturnType<typeof verifyAdminReport>>);
    render(<AdminReportInspector reportId="r1" />);

    await userEvent.click(await screen.findByRole("button", { name: "Verify" }));

    expect(await screen.findByRole("button", { name: "Unverify" })).toBeInTheDocument();
  });
});
