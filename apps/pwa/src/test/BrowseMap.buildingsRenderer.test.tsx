/**
 * Public `buildings`-mode renderer.
 *
 * Asserts that `BrowseMap` wires the live buildings layer when the
 * persisted crisis is in `public_visibility="buildings"` mode:
 *   * a GeoJSON source pointing at `GET /crises/{id}/public/buildings`
 *     is added,
 *   * a `circle` layer painted by `damage_class` (green/yellow/red) is
 *     added on top,
 *   * tapping a pin renders the damage-class label + "N reports about
 *     this location" copy in the bottom sheet (no `building_id`
 *     surfaced).
 *
 * Mocks MapLibre so we can introspect the layer config without a real
 * WebGL canvas — mirrors `BrowseMap.modeBranches.test.tsx`.
 */
import { act, render, screen, waitFor } from "@testing-library/react";
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
            once: vi.fn(),
            zoomIn: vi.fn(),
            zoomOut: vi.fn(),
            moveLayer: vi.fn(),
          });
        }
      },
      NavigationControl: class {},
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

const mockUseCrisisPersistence = vi.fn();
vi.mock("../hooks/useCrisisPersistence", () => ({
  useCrisisPersistence: () => mockUseCrisisPersistence(),
}));

beforeEach(() => {
  addSource.mockClear();
  addLayer.mockClear();
  onFn.mockClear();
  mockUseCrisisPersistence.mockReset();
  mockUseCrisisPersistence.mockReturnValue({
    stored: {
      id: "c1",
      name: "Test Crisis",
      pmtiles_url: null,
      overture_release_pinned: null,
      public_visibility: "buildings",
    },
    save: vi.fn(),
    clear: vi.fn(),
  });
  // Stats fetch returns 200 — keeps the render path happy.
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

function fireMapLoad() {
  const loadCall = onFn.mock.calls.find((c) => c[0] === "load");
  if (loadCall && typeof loadCall[1] === "function") loadCall[1]();
}

function findClickHandler(): (e: unknown) => void {
  const clickCall = onFn.mock.calls.find(
    (c) => c[0] === "click" && c[1] === "public-buildings-circle",
  );
  expect(clickCall).toBeDefined();
  return clickCall?.[2] as (e: unknown) => void;
}

describe("BrowseMap — buildings-mode renderer (PRD B)", () => {
  it("adds a GeoJSON source pointed at /public/buildings", async () => {
    render(<BrowseMap onBack={vi.fn()} />);
    await waitFor(() => expect(onFn).toHaveBeenCalled());
    fireMapLoad();

    const buildingsCall = addSource.mock.calls.find((c) => c[0] === "public-buildings");
    expect(buildingsCall).toBeDefined();
    const [, sourceConfig] = buildingsCall as [string, { type: string; data: string }];
    expect(sourceConfig.type).toBe("geojson");
    expect(sourceConfig.data).toMatch(/\/crises\/c1\/public\/buildings$/);
  });

  it("adds a circle layer painted by damage_class with the green/yellow/red palette", async () => {
    render(<BrowseMap onBack={vi.fn()} />);
    await waitFor(() => expect(onFn).toHaveBeenCalled());
    fireMapLoad();

    const layerCall = addLayer.mock.calls.find(
      (c) => (c[0] as { id?: string })?.id === "public-buildings-circle",
    );
    expect(layerCall).toBeDefined();
    const layer = layerCall?.[0] as {
      id: string;
      type: string;
      source: string;
      paint: { "circle-color": unknown };
    };
    expect(layer.type).toBe("circle");
    expect(layer.source).toBe("public-buildings");
    // Damage palette: green (minimal) · yellow (partial) · red (complete).
    // The presence of all three colour codes in the paint config is the
    // load-bearing assertion — exact shape of the `match` expression can drift.
    const paintColor = JSON.stringify(layer.paint["circle-color"]);
    expect(paintColor).toContain("#16a34a");
    expect(paintColor).toContain("#f59e0b");
    expect(paintColor).toContain("#dc2626");
  });

  it("renders the damage-class label + report-count copy in the sheet on click", async () => {
    render(<BrowseMap onBack={vi.fn()} />);
    await waitFor(() => expect(onFn).toHaveBeenCalled());
    fireMapLoad();

    const handler = findClickHandler();
    act(() => {
      handler({
        features: [
          {
            properties: { damage_class: "complete", report_count: 3, building_id: "b-xyz" },
          },
        ],
        lngLat: { lng: 51.5, lat: 25.3 },
      });
    });

    // Damage class label appears (uses the existing `damage.*` keys).
    expect(screen.getByText(/Damage: Complete/)).toBeInTheDocument();
    // Plural copy resolves to the `_other` form for count=3, with the
    // count interpolated and "reports about this location" text.
    expect(screen.getByText("3 reports about this location")).toBeInTheDocument();
    // The panel MUST NOT surface `building_id`.
    expect(screen.queryByText(/b-xyz/)).not.toBeInTheDocument();
  });

  it("uses the singular plural form when report_count is 1", async () => {
    render(<BrowseMap onBack={vi.fn()} />);
    await waitFor(() => expect(onFn).toHaveBeenCalled());
    fireMapLoad();

    const handler = findClickHandler();
    act(() => {
      handler({
        features: [
          {
            properties: { damage_class: "minimal", report_count: 1, building_id: null },
          },
        ],
        lngLat: { lng: 0, lat: 0 },
      });
    });
    expect(screen.getByText("1 report about this location")).toBeInTheDocument();
  });
});
