/**
 * Public visibility mode switching.
 *
 * Verifies the four BrowseMap render branches keyed on
 * `crisis.public_visibility`. Mocks MapLibre so we can introspect the
 * `addSource`/`addLayer` calls without spinning up a real WebGL canvas.
 */
import { render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const addSource = vi.fn();
const addLayer = vi.fn();
const onFn = vi.fn();

vi.mock("maplibre-gl", () => {
  return {
    default: {
      addProtocol: vi.fn(),
      removeProtocol: vi.fn(),
      Map: class {
        constructor() {
          Object.assign(this, {
            on: onFn,
            off: vi.fn(),
            remove: vi.fn(),
            addControl: vi.fn(),
            addSource,
            addLayer,
            getCanvas: () => ({ style: {} }),
            getSource: vi.fn(() => null),
            getLayer: vi.fn(() => null),
            queryRenderedFeatures: vi.fn(() => []),
            flyTo: vi.fn(),
            fitBounds: vi.fn(),
            getBounds: vi.fn(() => ({
              getWest: () => 51.0,
              getSouth: () => 25.0,
              getEast: () => 52.0,
              getNorth: () => 26.0,
            })),
            once: vi.fn(),
            zoomIn: vi.fn(),
            zoomOut: vi.fn(),
            moveLayer: vi.fn(),
          });
        }
      },
      NavigationControl: class {},
      Popup: class {
        setLngLat() {
          return this;
        }
        setHTML() {
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
  getCitizenReportHistory: vi.fn().mockResolvedValue({ items: [], total: 0 }),
  crisisStatsKey: (id: string) => `rid-crisis-stats:${id}`,
}));

vi.mock("../lib/clientId", () => ({
  getClientId: vi.fn().mockResolvedValue("test-client"),
}));

vi.mock("react-i18next", async () => {
  const mod = (await import("../../public/locales/en/translation.json")) as {
    default: Record<string, string>;
  };
  const en = mod.default;
  const t = (k: string, opts?: Record<string, unknown>) =>
    (en[k] ?? k).replace(/{{\s*(\w+)\s*}}/g, (_, key) => String(opts?.[key] ?? ""));
  return {
    useTranslation: () => ({
      t,
      i18n: {
        changeLanguage: () => Promise.resolve(),
        language: "en",
        dir: () => "ltr",
      },
    }),
  };
});

// Mock the persistence hook so each test injects a specific mode.
const mockUseCrisisPersistence = vi.fn();
vi.mock("../hooks/useCrisisPersistence", () => ({
  useCrisisPersistence: () => mockUseCrisisPersistence(),
}));

// Stub fetch so the stats call doesn't break the render. We don't assert
// on it here — sibling test covers that.
beforeEach(() => {
  addSource.mockClear();
  addLayer.mockClear();
  onFn.mockClear();
  mockUseCrisisPersistence.mockReset();
  vi.stubGlobal(
    "fetch",
    vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      json: () =>
        Promise.resolve({
          total_reports: 0,
          by_damage_class: { minimal: 0, partial: 0, complete: 0 },
          last_24h: 0,
          last_7d: 0,
          affected_cells: 0,
          latest_at: null,
        }),
    }),
  );
});

afterEach(() => {
  vi.clearAllMocks();
});

import { BrowseMap } from "../components/BrowseMap";

function pickedCrisis(mode: "none" | "aggregate_view" | "buildings" | "full") {
  return {
    stored: {
      id: "c1",
      name: "Test Crisis",
      pmtiles_url: null,
      overture_release_pinned: null,
      public_visibility: mode,
    },
    save: vi.fn(),
    clear: vi.fn(),
  };
}

/**
 * The component's `map.on("load", cb)` schedules the data-layer setup. Our
 * mock just records the callback — we invoke it manually so addSource /
 * addLayer fire synchronously and we can assert on them.
 */
function fireMapLoad() {
  const loadCall = onFn.mock.calls.find((c) => c[0] === "load");
  if (loadCall && typeof loadCall[1] === "function") loadCall[1]();
}

describe("BrowseMap — public_visibility branches", () => {
  it("aggregate_view: adds the heat MVT source + fill/line layers", async () => {
    mockUseCrisisPersistence.mockReturnValue(pickedCrisis("aggregate_view"));
    render(<BrowseMap onBack={vi.fn()} />);
    await waitFor(() => expect(onFn).toHaveBeenCalled());
    fireMapLoad();
    const sourceIds = addSource.mock.calls.map((c) => c[0]);
    const layerIds = addLayer.mock.calls.map((c) => c[0]?.id);
    expect(sourceIds).toContain("heat");
    expect(sourceIds).toContain("my-reports");
    expect(layerIds).toContain("heat-fill");
    expect(layerIds).toContain("heat-line");
  });

  it("none: skips data layers and renders the public-view-disabled banner", async () => {
    mockUseCrisisPersistence.mockReturnValue(pickedCrisis("none"));
    render(<BrowseMap onBack={vi.fn()} />);
    await waitFor(() => expect(onFn).toHaveBeenCalled());
    fireMapLoad();
    const sourceIds = addSource.mock.calls.map((c) => c[0]);
    expect(sourceIds).not.toContain("heat");
    // MY_REPORTS overlay still present in `none` mode — citizens see their
    // own reports regardless of the crisis posture.
    expect(sourceIds).toContain("my-reports");
    expect(screen.getByText(/public view turned off/i)).toBeInTheDocument();
  });

  it("buildings: adds the public-buildings GeoJSON source + circle layer (PRD B)", async () => {
    mockUseCrisisPersistence.mockReturnValue(pickedCrisis("buildings"));
    render(<BrowseMap onBack={vi.fn()} />);
    await waitFor(() => expect(onFn).toHaveBeenCalled());
    fireMapLoad();
    const sourceIds = addSource.mock.calls.map((c) => c[0]);
    const layerIds = addLayer.mock.calls.map((c) => c[0]?.id);
    // Heat MVT is still skipped — `buildings` mode owns its own layer.
    expect(sourceIds).not.toContain("heat");
    // The MY_REPORTS overlay still lives in buildings mode.
    expect(sourceIds).toContain("my-reports");
    // The live renderer replaces the old placeholder banner.
    expect(sourceIds).toContain("public-buildings");
    expect(layerIds).toContain("public-buildings-circle");
    expect(screen.queryByText(/per-building pins/i)).not.toBeInTheDocument();
  });

  it("full: adds the public-reports GeoJSON source + circle layer (PRD C)", async () => {
    mockUseCrisisPersistence.mockReturnValue(pickedCrisis("full"));
    render(<BrowseMap onBack={vi.fn()} />);
    await waitFor(() => expect(onFn).toHaveBeenCalled());
    fireMapLoad();
    const sourceIds = addSource.mock.calls.map((c) => c[0]);
    const layerIds = addLayer.mock.calls.map((c) => c[0]?.id);
    // Heat MVT is still skipped — `full` mode owns its own layer.
    expect(sourceIds).not.toContain("heat");
    // The MY_REPORTS overlay still lives in full mode.
    expect(sourceIds).toContain("my-reports");
    // The live renderer replaces the old placeholder banner.
    expect(sourceIds).toContain("public-reports");
    expect(layerIds).toContain("public-reports-circle");
    // The old placeholder banner is gone now that the live layer ships.
    expect(screen.queryByText(/individual report detail/i)).not.toBeInTheDocument();
  });
});
