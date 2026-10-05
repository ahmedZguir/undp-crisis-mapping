import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { vi } from "vitest";
import { ReportForm } from "../components/ReportForm";

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
      // Chainable stub for the center pin StepLocation drops on the map.
      Marker: class {
        setLngLat() {
          return this;
        }
        addTo() {
          return this;
        }
        remove() {}
      },
    },
  };
});

vi.mock("../api/reports", () => ({
  getCrises: vi.fn().mockResolvedValue([
    {
      id: "00000000-0000-0000-0000-000000000001",
      name: "Other / Unspecified",
      pmtiles_url: null,
      overture_release_pinned: null,
      public_visibility: "aggregate_view",
    },
  ]),
  submitReport: vi.fn().mockResolvedValue({ id: "test-123" }),
}));

describe("ReportForm", () => {
  it("renders step 1 of 6 initially", async () => {
    const onComplete = vi.fn();
    render(
      <ReportForm
        crisis={{
          id: "crisis-1",
          name: "Test Crisis",
          pmtiles_url: null,
          overture_release_pinned: null,
          public_visibility: "aggregate_view",
        }}
        onComplete={onComplete}
      />,
    );

    await waitFor(() => {
      expect(screen.getByText("Step 1 of 7")).toBeInTheDocument();
    });
  });

  it("clicking Next on step 0 advances to step 2", async () => {
    const user = userEvent.setup();
    const onComplete = vi.fn();
    render(
      <ReportForm
        crisis={{
          id: "crisis-1",
          name: "Test Crisis",
          pmtiles_url: null,
          overture_release_pinned: null,
          public_visibility: "aggregate_view",
        }}
        onComplete={onComplete}
      />,
    );

    await waitFor(() => {
      expect(screen.getByText("Step 1 of 7")).toBeInTheDocument();
    });

    await user.click(screen.getByRole("button", { name: /minimal/i }));
    await user.click(screen.getByRole("button", { name: /next/i }));

    expect(await screen.findByText("Step 2 of 7")).toBeInTheDocument();
  });

  it("Back on step 1 returns to step 1", async () => {
    const user = userEvent.setup();
    const onComplete = vi.fn();
    render(
      <ReportForm
        crisis={{
          id: "crisis-1",
          name: "Test Crisis",
          pmtiles_url: null,
          overture_release_pinned: null,
          public_visibility: "aggregate_view",
        }}
        onComplete={onComplete}
      />,
    );

    await waitFor(() => {
      expect(screen.getByText("Step 1 of 7")).toBeInTheDocument();
    });

    await user.click(screen.getByRole("button", { name: /minimal/i }));
    await user.click(screen.getByRole("button", { name: /next/i }));
    expect(await screen.findByText("Step 2 of 7")).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: /back/i }));
    expect(screen.getByText("Step 1 of 7")).toBeInTheDocument();
  });
});
