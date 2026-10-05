import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { CrisisPickerScreen } from "../components/CrisisPickerScreen";

vi.mock("../api/reports", () => ({
  getCrises: vi.fn().mockResolvedValue([
    {
      id: "1",
      name: "Earthquake Response 2026",
      pmtiles_url: null,
      overture_release_pinned: null,
      public_visibility: "aggregate_view",
    },
    {
      id: "2",
      name: "Floods Q2",
      pmtiles_url: null,
      overture_release_pinned: null,
      public_visibility: "aggregate_view",
    },
    {
      id: "other",
      name: "Other / Unspecified",
      pmtiles_url: null,
      overture_release_pinned: null,
      public_visibility: "aggregate_view",
    },
  ]),
}));

describe("CrisisPickerScreen", () => {
  it("renders one card per crisis", async () => {
    render(<CrisisPickerScreen onPick={vi.fn()} onBack={vi.fn()} />);

    await waitFor(() => {
      expect(screen.getByRole("button", { name: /earthquake response 2026/i })).toBeInTheDocument();
    });
    expect(screen.getByRole("button", { name: /floods q2/i })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /other \/ unspecified/i })).toBeInTheDocument();
  });

  it("disables confirm until a card is selected", async () => {
    const user = userEvent.setup();
    render(<CrisisPickerScreen onPick={vi.fn()} onBack={vi.fn()} />);

    await waitFor(() => {
      expect(screen.getByRole("button", { name: /earthquake response 2026/i })).toBeInTheDocument();
    });
    const confirm = screen.getByRole("button", { name: /confirm/i });
    expect(confirm).toBeDisabled();
    await user.click(screen.getByRole("button", { name: /earthquake response 2026/i }));
    expect(confirm).not.toBeDisabled();
  });

  it("calls onPick with the selected crisis when confirm is tapped", async () => {
    const onPick = vi.fn();
    const user = userEvent.setup();
    render(<CrisisPickerScreen onPick={onPick} onBack={vi.fn()} />);

    await waitFor(() => {
      expect(screen.getByRole("button", { name: /floods q2/i })).toBeInTheDocument();
    });
    await user.click(screen.getByRole("button", { name: /floods q2/i }));
    await user.click(screen.getByRole("button", { name: /confirm/i }));
    expect(onPick).toHaveBeenCalledWith({
      id: "2",
      name: "Floods Q2",
      pmtiles_url: null,
      overture_release_pinned: null,
      public_visibility: "aggregate_view",
    });
  });

  it("shows loading skeleton while fetching", async () => {
    const { getCrises } = await import("../api/reports");
    vi.mocked(getCrises).mockReturnValueOnce(new Promise(() => {}));

    render(<CrisisPickerScreen onPick={vi.fn()} onBack={vi.fn()} />);
    expect(screen.getByTestId("crisis-picker-loading")).toBeInTheDocument();
  });

  it("shows hard-block error with retry when fetch fails, and retry reloads the list", async () => {
    const { getCrises } = await import("../api/reports");
    vi.mocked(getCrises)
      .mockRejectedValueOnce(new Error("network error"))
      .mockResolvedValueOnce([
        {
          id: "1",
          name: "Earthquake Response 2026",
          pmtiles_url: null,
          overture_release_pinned: null,
          public_visibility: "aggregate_view",
        },
      ]);

    const user = userEvent.setup();
    render(<CrisisPickerScreen onPick={vi.fn()} onBack={vi.fn()} />);

    await waitFor(() => {
      expect(screen.getByRole("button", { name: /retry|try again/i })).toBeInTheDocument();
    });
    await user.click(screen.getByRole("button", { name: /retry|try again/i }));

    await waitFor(() => {
      expect(screen.getByRole("button", { name: /earthquake response 2026/i })).toBeInTheDocument();
    });
  });

  it("renders banner when bannerMessage is set", async () => {
    render(
      <CrisisPickerScreen
        onPick={vi.fn()}
        onBack={vi.fn()}
        bannerMessage="The previous crisis is no longer accepting reports."
      />,
    );

    await waitFor(() => {
      expect(
        screen.getByText(/previous crisis is no longer accepting reports/i),
      ).toBeInTheDocument();
    });
  });
});
