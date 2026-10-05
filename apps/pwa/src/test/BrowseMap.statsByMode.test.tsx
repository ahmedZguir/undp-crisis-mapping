/**
 * Public visibility mode switching.
 *
 * Verifies `refreshStats` (the `GET /crises/{id}/stats` fetch) fires in
 * every live mode (`aggregate_view`, `buildings`, `full`) and is skipped
 * for `none` mode — the latter would 404 by design and the bottom-sheet
 * stats panel should never render an empty/stale state.
 */
import { render, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("maplibre-gl", () => ({
  default: {
    addProtocol: vi.fn(),
    removeProtocol: vi.fn(),
    Map: class {
      constructor() {
        Object.assign(this, {
          on: vi.fn(),
          off: vi.fn(),
          remove: vi.fn(),
          addControl: vi.fn(),
          addSource: vi.fn(),
          addLayer: vi.fn(),
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
}));

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

const fetchMock = vi.fn();
beforeEach(() => {
  fetchMock.mockReset();
  mockUseCrisisPersistence.mockReset();
  fetchMock.mockResolvedValue({
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
  });
  vi.stubGlobal("fetch", fetchMock);
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

function statsCalls(): string[] {
  return fetchMock.mock.calls.map((c) => String(c[0] ?? "")).filter((u) => u.includes("/stats"));
}

describe("BrowseMap — stats fetch by mode", () => {
  it.each(["aggregate_view", "buildings", "full"] as const)(
    "calls /stats in %s mode",
    async (mode) => {
      mockUseCrisisPersistence.mockReturnValue(pickedCrisis(mode));
      render(<BrowseMap onBack={vi.fn()} />);
      await waitFor(() => expect(statsCalls().length).toBeGreaterThanOrEqual(1));
    },
  );

  it("does NOT call /stats in none mode", async () => {
    mockUseCrisisPersistence.mockReturnValue(pickedCrisis("none"));
    render(<BrowseMap onBack={vi.fn()} />);
    // Give the effect a chance to run (it would have fired by now if it was going to).
    await new Promise((r) => setTimeout(r, 20));
    expect(statsCalls()).toHaveLength(0);
  });
});
