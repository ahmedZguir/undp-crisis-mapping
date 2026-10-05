/**
 * Public `full`-mode renderer.
 *
 * Asserts that `BrowseMap` wires the live full-mode layer when the
 * persisted crisis is in `public_visibility="full"` mode:
 *   * a `geojson` source initialised empty is added,
 *   * a `circle` layer painted by `damage_class` is added on top,
 *   * a `moveend` listener is registered and triggers
 *     `getPublicReportsByBbox` (debounced),
 *   * tapping a feature fires `getPublicReportDetail` and the bottom
 *     sheet renders a lazy-loaded `<img>` + the description.
 *
 * Mocks MapLibre so we can introspect the layer config + event
 * handlers without a real WebGL canvas — mirrors
 * `BrowseMap.buildingsRenderer.test.tsx`.
 */
import { act, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const addSource = vi.fn();
const addLayer = vi.fn();
const onFn = vi.fn();
const getSource = vi.fn();
const setData = vi.fn();
const getBounds = vi.fn();

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
            getSource,
            getLayer: vi.fn(() => null),
            queryRenderedFeatures: vi.fn(() => []),
            flyTo: vi.fn(),
            fitBounds: vi.fn(),
            getBounds,
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

// Mock the public reports API helpers — assert on call arguments + feed
// the canned responses into the component.
const getPublicReportsByBbox = vi.fn();
const getPublicReportDetail = vi.fn();
vi.mock("../api/reports", async () => {
  // Re-export the real types + the error class so the runtime
  // `instanceof PublicReportDetailError` check inside BrowseMap still
  // works against the mocked fetch helpers.
  const actual = await vi.importActual<typeof import("../api/reports")>("../api/reports");
  return {
    ...actual,
    getCitizenReportHistory: vi.fn().mockResolvedValue({ items: [], total: 0 }),
    getPublicReportsByBbox: (...args: unknown[]) =>
      getPublicReportsByBbox(...(args as Parameters<typeof actual.getPublicReportsByBbox>)),
    getPublicReportDetail: (...args: unknown[]) =>
      getPublicReportDetail(...(args as Parameters<typeof actual.getPublicReportDetail>)),
  };
});

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
  getSource.mockReset();
  setData.mockClear();
  getBounds.mockReset();
  getPublicReportsByBbox.mockReset();
  getPublicReportDetail.mockReset();
  mockUseCrisisPersistence.mockReset();
  mockUseCrisisPersistence.mockReturnValue({
    stored: {
      id: "c1",
      name: "Test Crisis",
      pmtiles_url: null,
      overture_release_pinned: null,
      public_visibility: "full",
    },
    save: vi.fn(),
    clear: vi.fn(),
  });
  // The bbox endpoint default — two features in the bbox, nothing
  // truncated. Individual tests override.
  getPublicReportsByBbox.mockResolvedValue({
    items: [
      {
        id: "r1",
        crisis_id: "c1",
        damage_class: "complete",
        description: "Visible damage",
        infra_type: null,
        infra_name: "Main mosque",
        crisis_type: null,
        crisis_type_detailed: null,
        debris: null,
        building_id: null,
        location: { lat: 25.3, lng: 51.5 },
        map_point: { lat: 25.3, lng: 51.5 },
        created_at: "2026-05-17T00:00:00Z",
      },
      {
        id: "r2",
        crisis_id: "c1",
        damage_class: "partial",
        description: null,
        infra_type: null,
        infra_name: null,
        crisis_type: null,
        crisis_type_detailed: null,
        debris: null,
        building_id: null,
        location: { lat: 25.31, lng: 51.51 },
        map_point: { lat: 25.31, lng: 51.51 },
        created_at: "2026-05-17T00:00:00Z",
      },
    ],
    next_cursor: null,
    truncated: false,
    total_in_bbox: 2,
  });
  // getSource(PUBLIC_REPORTS_SOURCE_ID) — return an object with a
  // setData spy so the test can assert the features that were pushed.
  getSource.mockImplementation((id: string) => {
    if (id === "public-reports") return { setData };
    return null;
  });
  getBounds.mockReturnValue({
    getWest: () => 51.0,
    getSouth: () => 25.0,
    getEast: () => 52.0,
    getNorth: () => 26.0,
  });
  // Stats fetch — keeps the render path happy.
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
  vi.useRealTimers();
  vi.clearAllMocks();
});

/** Yield to the microtask + setTimeout queue once. The bbox endpoint
 *  is awaited inside an IIFE the load handler kicked off; tests
 *  resolve it by simply awaiting the next tick. */
async function flush(): Promise<void> {
  await new Promise((resolve) => setTimeout(resolve, 0));
  await new Promise((resolve) => setTimeout(resolve, 0));
}

import { BrowseMap } from "../components/BrowseMap";

async function fireMapLoadAndFlush() {
  const loadCall = onFn.mock.calls.find((c) => c[0] === "load");
  if (loadCall && typeof loadCall[1] === "function") loadCall[1]();
  // The initial bbox fetch fires synchronously from the load handler;
  // its promise chain resolves on the microtask queue. Two flushes
  // settle the await + setData + setState chain.
  await flush();
}

describe("BrowseMap — full-mode renderer (PRD C)", () => {
  it("adds an empty GeoJSON source named `public-reports`", async () => {
    render(<BrowseMap onBack={vi.fn()} />);
    await waitFor(() => expect(onFn).toHaveBeenCalled());
    await fireMapLoadAndFlush();

    const call = addSource.mock.calls.find((c) => c[0] === "public-reports");
    expect(call).toBeDefined();
    const [, src] = call as [string, { type: string; data: unknown }];
    expect(src.type).toBe("geojson");
    expect(src.data).toEqual({ type: "FeatureCollection", features: [] });
  });

  it("adds a circle layer painted by damage_class with the my-reports palette", async () => {
    render(<BrowseMap onBack={vi.fn()} />);
    await waitFor(() => expect(onFn).toHaveBeenCalled());
    await fireMapLoadAndFlush();

    const layerCall = addLayer.mock.calls.find(
      (c) => (c[0] as { id?: string })?.id === "public-reports-circle",
    );
    expect(layerCall).toBeDefined();
    const layer = layerCall?.[0] as {
      id: string;
      type: string;
      source: string;
      paint: { "circle-color": unknown };
    };
    expect(layer.type).toBe("circle");
    expect(layer.source).toBe("public-reports");
    const paintColor = JSON.stringify(layer.paint["circle-color"]);
    expect(paintColor).toContain("#16a34a");
    expect(paintColor).toContain("#f59e0b");
    expect(paintColor).toContain("#dc2626");
  });

  it("fetches the bbox endpoint on the initial load and pushes features", async () => {
    render(<BrowseMap onBack={vi.fn()} />);
    await waitFor(() => expect(onFn).toHaveBeenCalled());
    await fireMapLoadAndFlush();

    // Initial kick fires immediately (not debounced).
    expect(getPublicReportsByBbox).toHaveBeenCalledTimes(1);
    const [crisisId, bbox, limit] = getPublicReportsByBbox.mock.calls[0] ?? [];
    expect(crisisId).toBe("c1");
    expect(bbox).toBe("51,25,52,26");
    expect(limit).toBe(200);

    // The features pushed into the GeoJSON source are well-formed.
    expect(setData).toHaveBeenCalled();
    const last = setData.mock.calls.at(-1)?.[0] as {
      type: string;
      features: { geometry: { coordinates: [number, number] }; properties: { id: string } }[];
    };
    expect(last.type).toBe("FeatureCollection");
    expect(last.features).toHaveLength(2);
    expect(last.features[0]?.properties.id).toBe("r1");
    // GeoJSON ordering: [lng, lat].
    expect(last.features[0]?.geometry.coordinates).toEqual([51.5, 25.3]);
  });

  it("debounces moveend fetches", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    try {
      render(<BrowseMap onBack={vi.fn()} />);
      await waitFor(() => expect(onFn).toHaveBeenCalled());
      // Fire the load handler synchronously without flush — we'll
      // advance time manually below to settle the initial kick.
      const loadCall = onFn.mock.calls.find((c) => c[0] === "load");
      (loadCall?.[1] as () => void)();
      await vi.advanceTimersByTimeAsync(0);
      getPublicReportsByBbox.mockClear();
      setData.mockClear();

      const moveendCall = onFn.mock.calls.find((c) => c[0] === "moveend");
      expect(moveendCall).toBeDefined();
      const handler = moveendCall?.[1] as () => void;

      // Move the viewport so the bbox key changes — otherwise the
      // duplicate-key guard skips the fetch entirely.
      getBounds.mockReturnValue({
        getWest: () => 51.1,
        getSouth: () => 25.1,
        getEast: () => 52.1,
        getNorth: () => 26.1,
      });

      // Fire three rapid `moveend`s. The debounce coalesces them
      // into a single fetch after the 300 ms window.
      handler();
      handler();
      handler();
      // Immediately after the burst, no fetch has been issued.
      expect(getPublicReportsByBbox).not.toHaveBeenCalled();
      await vi.advanceTimersByTimeAsync(350);
      expect(getPublicReportsByBbox).toHaveBeenCalledTimes(1);
    } finally {
      vi.useRealTimers();
    }
  });

  it("on tap, fetches detail and the sheet renders a lazy-loaded photo + description", async () => {
    getPublicReportDetail.mockResolvedValueOnce({
      id: "r1",
      crisis_id: "c1",
      damage_class: "complete",
      description: "Building roof collapsed during the night",
      infra_type: null,
      infra_name: "Main mosque",
      crisis_type: null,
      crisis_type_detailed: null,
      debris: null,
      building_id: null,
      location: { lat: 25.3, lng: 51.5 },
      building_centroid: null,
      photo_url: "https://signed.test/reports/r1.jpg?ttl=900",
      created_at: "2026-05-17T00:00:00Z",
    });
    render(<BrowseMap onBack={vi.fn()} />);
    await waitFor(() => expect(onFn).toHaveBeenCalled());
    await fireMapLoadAndFlush();

    const clickCall = onFn.mock.calls.find(
      (c) => c[0] === "click" && c[1] === "public-reports-circle",
    );
    expect(clickCall).toBeDefined();
    const handler = clickCall?.[2] as (e: unknown) => void;
    act(() => {
      handler({
        features: [{ properties: { id: "r1", damage_class: "complete" } }],
        lngLat: { lng: 51.5, lat: 25.3 },
      });
    });

    // Loading state shows immediately in the sheet.
    expect(screen.getByText(/Loading report/)).toBeInTheDocument();
    // Detail fetch is fired with the right id.
    expect(getPublicReportDetail).toHaveBeenCalledWith("r1");

    // The async detail resolves and the panel renders the report.
    const img = await screen.findByRole("img", { name: "Damage report photo" });
    expect(img).toHaveAttribute("loading", "lazy");
    expect(img).toHaveAttribute("src", "https://signed.test/reports/r1.jpg?ttl=900");
    // The damage label uses the existing `damage.*` keys. The sheet shows the
    // photo + damage headline only; the description/infra_name were dropped in
    // the browse-map UI redesign (commit 79b02de).
    expect(screen.getByText(/Damage: Complete/)).toBeInTheDocument();
  });

  it("on detail 404, the sheet shows the no-longer-visible copy", async () => {
    // Resolve to a 404 — the helper throws `PublicReportDetailError`.
    const { PublicReportDetailError } = await import("../api/reports");
    getPublicReportDetail.mockRejectedValueOnce(new PublicReportDetailError("404", 404));

    render(<BrowseMap onBack={vi.fn()} />);
    await waitFor(() => expect(onFn).toHaveBeenCalled());
    await fireMapLoadAndFlush();

    const clickCall = onFn.mock.calls.find(
      (c) => c[0] === "click" && c[1] === "public-reports-circle",
    );
    const handler = clickCall?.[2] as (e: unknown) => void;
    act(() => {
      handler({
        features: [{ properties: { id: "r1", damage_class: "complete" } }],
        lngLat: { lng: 51.5, lat: 25.3 },
      });
    });
    expect(await screen.findByText(/no longer publicly visible/)).toBeInTheDocument();
  });
});
