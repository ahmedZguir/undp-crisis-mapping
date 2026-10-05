import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { ReportForm } from "../components/ReportForm";
import type { Crisis } from "../types";

vi.mock("maplibre-gl", () => {
  const createMapInstance = () => ({
    on: vi.fn(),
    off: vi.fn(),
    remove: vi.fn(),
    addControl: vi.fn(),
    getCenter: vi.fn(() => ({ lat: 0, lng: 0 })),
    getZoom: vi.fn(() => 16),
    flyTo: vi.fn(),
    addSource: vi.fn(),
    addLayer: vi.fn(),
    getLayer: vi.fn(() => null),
    getSource: vi.fn(() => null),
    setFeatureState: vi.fn(),
    queryRenderedFeatures: vi.fn(() => []),
  });
  return {
    default: {
      addProtocol: vi.fn(),
      removeProtocol: vi.fn(),
      Map: class {
        constructor() {
          Object.assign(this, createMapInstance());
        }
      },
      NavigationControl: class {},
    },
  };
});

vi.mock("../api/reports", () => ({
  getCrises: vi
    .fn()
    .mockResolvedValue([
      { id: "c1", name: "Earthquake Response", pmtiles_url: null, overture_release_pinned: null },
    ]),
  submitReport: vi.fn().mockResolvedValue({ id: "report-1" }),
}));

const validCrisis: Crisis = {
  id: "crisis-1",
  name: "Test Crisis",
  pmtiles_url: null,
  overture_release_pinned: null,
  public_visibility: "aggregate_view",
};

describe("ReportForm crisis gate", () => {
  it("shows the crisis interlude when crisis prop is null", async () => {
    render(<ReportForm crisis={null} onComplete={vi.fn()} />);

    await waitFor(() => {
      expect(screen.getByText(/which crises are you reporting on/i)).toBeInTheDocument();
    });
    expect(screen.queryByText(/Step \d of \d/)).toBeNull();
  });

  it("skips the interlude and shows form steps when crisis is provided", async () => {
    render(<ReportForm crisis={validCrisis} onComplete={vi.fn()} />);

    await waitFor(() => {
      expect(screen.getByText("Step 1 of 7")).toBeInTheDocument();
    });
    expect(screen.queryByText(/which crises are you reporting on/i)).toBeNull();
  });

  it("proceeds to the form after picking a crisis in the interlude", async () => {
    const user = userEvent.setup();
    render(<ReportForm crisis={null} onComplete={vi.fn()} />);

    await waitFor(() => {
      expect(screen.getByRole("button", { name: /earthquake response/i })).toBeInTheDocument();
    });
    await user.click(screen.getByRole("button", { name: /earthquake response/i }));
    await user.click(screen.getByRole("button", { name: /confirm/i }));

    await waitFor(() => {
      expect(screen.getByText("Step 1 of 7")).toBeInTheDocument();
    });
  });
});
