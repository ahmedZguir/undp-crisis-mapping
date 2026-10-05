import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { CrisisPickerScreen } from "../components/CrisisPickerScreen";

const MOCK_CRISES = [
  {
    id: "c1",
    name: "Earthquake Response 2026",
    pmtiles_url: null,
    overture_release_pinned: null,
    public_visibility: "aggregate_view" as const,
  },
  {
    id: "c2",
    name: "Floods Q2",
    pmtiles_url: null,
    overture_release_pinned: null,
    public_visibility: "aggregate_view" as const,
  },
];

vi.mock("../api/reports", () => ({
  getCrises: vi.fn(),
}));

describe("CrisisPickerScreen (self-fetching)", () => {
  it("calls getCrises on mount and renders the crisis list", async () => {
    const { getCrises } = await import("../api/reports");
    vi.mocked(getCrises).mockResolvedValueOnce(MOCK_CRISES);

    render(<CrisisPickerScreen onPick={vi.fn()} onBack={vi.fn()} />);

    await waitFor(() => {
      expect(screen.getByRole("button", { name: /earthquake response 2026/i })).toBeInTheDocument();
    });
    expect(screen.getByRole("button", { name: /floods q2/i })).toBeInTheDocument();
    expect(getCrises).toHaveBeenCalledOnce();
  });

  it("shows loading state while fetching", async () => {
    const { getCrises } = await import("../api/reports");
    vi.mocked(getCrises).mockReturnValueOnce(new Promise(() => {}));

    render(<CrisisPickerScreen onPick={vi.fn()} onBack={vi.fn()} />);
    expect(screen.getByTestId("crisis-picker-loading")).toBeInTheDocument();
  });

  it("shows retry button on network failure", async () => {
    const { getCrises } = await import("../api/reports");
    vi.mocked(getCrises).mockRejectedValueOnce(new Error("network error"));

    render(<CrisisPickerScreen onPick={vi.fn()} onBack={vi.fn()} />);

    await waitFor(() => {
      expect(screen.getByRole("button", { name: /retry|try again/i })).toBeInTheDocument();
    });
  });

  it("retries on retry button click", async () => {
    const user = userEvent.setup();
    const { getCrises } = await import("../api/reports");
    vi.mocked(getCrises)
      .mockRejectedValueOnce(new Error("network error"))
      .mockResolvedValueOnce(MOCK_CRISES);

    render(<CrisisPickerScreen onPick={vi.fn()} onBack={vi.fn()} />);

    await waitFor(() => {
      expect(screen.getByRole("button", { name: /retry|try again/i })).toBeInTheDocument();
    });
    await user.click(screen.getByRole("button", { name: /retry|try again/i }));

    await waitFor(() => {
      expect(screen.getByRole("button", { name: /earthquake response 2026/i })).toBeInTheDocument();
    });
  });

  it("calls onPick with the selected crisis on confirm", async () => {
    const user = userEvent.setup();
    const { getCrises } = await import("../api/reports");
    vi.mocked(getCrises).mockResolvedValueOnce(MOCK_CRISES);
    const onPick = vi.fn();

    render(<CrisisPickerScreen onPick={onPick} onBack={vi.fn()} />);

    await waitFor(() => {
      expect(screen.getByRole("button", { name: /floods q2/i })).toBeInTheDocument();
    });
    await user.click(screen.getByRole("button", { name: /floods q2/i }));
    await user.click(screen.getByRole("button", { name: /confirm/i }));

    expect(onPick).toHaveBeenCalledWith({
      id: "c2",
      name: "Floods Q2",
      pmtiles_url: null,
      overture_release_pinned: null,
      public_visibility: "aggregate_view",
    });
  });
});
