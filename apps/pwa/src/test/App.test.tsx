import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { deleteDB } from "idb";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("react-i18next", async () => {
  // Use the real EN translation bundle so component copy renders verbatim in
  // tests — keeps assertions resilient to translation key renames.
  const mod = (await import("../../public/locales/en/translation.json")) as {
    default: Record<string, string>;
  };
  const en = mod.default;
  const interpolate = (s: string, opts?: Record<string, unknown>) =>
    opts ? s.replace(/{{\s*(\w+)\s*}}/g, (_, k) => String(opts[k] ?? "")) : s;
  const t = (k: string, opts?: Record<string, unknown> & { defaultValue?: string }) => {
    const raw = en[k] ?? (opts?.defaultValue as string | undefined) ?? k;
    return interpolate(raw, opts);
  };
  return {
    useTranslation: () => ({
      t,
      i18n: {
        changeLanguage: () => Promise.resolve(),
        language: "en",
        dir: () => "ltr",
      },
    }),
    Trans: ({ i18nKey }: { i18nKey: string }) => en[i18nKey] ?? i18nKey,
    initReactI18next: { type: "3rdParty", init: () => {} },
  };
});

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

vi.mock("../api/reports", async () => {
  const actual = await vi.importActual<typeof import("../api/reports")>("../api/reports");
  return {
    ...actual,
    getCrises: vi.fn().mockResolvedValue([
      {
        id: "c1",
        name: "Earthquake Response 2026",
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
    submitReport: vi.fn().mockResolvedValue({ id: "r-1" }),
  };
});

import App from "../App";
import { POLICY_VERSION } from "../lib/consent";
import { _resetForTests, loadDrafts, saveDraft } from "../lib/db";

const LANG_KEY = "rid-lang-picked";
const CRISIS_KEY = "rid-crisis-picked";
const CONSENT_KEY = "rid-consent-version";

beforeEach(async () => {
  localStorage.clear();
  sessionStorage.clear();
  // These tests exercise screens past the consent gate (added with the privacy
  // notice); seed acceptance so the gate doesn't intercept. The gate's own
  // behaviour is covered by the consent tests.
  localStorage.setItem(CONSENT_KEY, String(POLICY_VERSION));
  await _resetForTests();
  await deleteDB("undp-app");
  await deleteDB("keyval-store");
});

afterEach(async () => {
  localStorage.clear();
  sessionStorage.clear();
  vi.clearAllMocks();
});

describe("App initial screen resolution", () => {
  it("renders LangPickerScreen when no language is stored", () => {
    render(<App />);
    expect(screen.getByText(/Pick your language/i)).toBeInTheDocument();
  });

  it("renders HomeChoiceScreen when language is stored but crisis is not", () => {
    localStorage.setItem(LANG_KEY, "en");
    render(<App />);
    expect(screen.getByText(/Report damage/i)).toBeInTheDocument();
  });

  it("renders HomeChoiceScreen when both lang and crisis are stored", () => {
    localStorage.setItem(LANG_KEY, "en");
    localStorage.setItem(
      CRISIS_KEY,
      JSON.stringify({
        id: "c1",
        name: "Earthquake Response 2026",
        pmtiles_url: null,
        overture_release_pinned: null,
        public_visibility: "aggregate_view",
      }),
    );
    render(<App />);
    expect(screen.getByText(/Report damage/i)).toBeInTheDocument();
    expect(screen.getByText(/Earthquake Response 2026/i)).toBeInTheDocument();
  });
});

describe("Crisis picker flow", () => {
  it("saves selection to localStorage and navigates to home-choice", async () => {
    localStorage.setItem(LANG_KEY, "en");
    const user = userEvent.setup();
    render(<App />);

    await waitFor(() => {
      expect(screen.getAllByRole("button", { name: /select crisis/i }).length).toBeGreaterThan(0);
    });
    await user.click(screen.getAllByRole("button", { name: /select crisis/i })[0]);
    await waitFor(() => {
      expect(screen.getByRole("button", { name: /earthquake response 2026/i })).toBeInTheDocument();
    });
    await user.click(screen.getByRole("button", { name: /earthquake response 2026/i }));
    await user.click(screen.getByRole("button", { name: /confirm/i }));

    await waitFor(() => {
      expect(screen.getByText(/Report damage/i)).toBeInTheDocument();
    });
    const stored = JSON.parse(localStorage.getItem(CRISIS_KEY) ?? "null");
    expect(stored).toEqual({
      id: "c1",
      name: "Earthquake Response 2026",
      pmtiles_url: null,
      overture_release_pinned: null,
      public_visibility: "aggregate_view",
    });
  });

  it('"Change crisis" navigates to picker and clears stored crisis', async () => {
    localStorage.setItem(LANG_KEY, "en");
    localStorage.setItem(
      CRISIS_KEY,
      JSON.stringify({
        id: "c1",
        name: "Earthquake Response 2026",
        pmtiles_url: null,
        overture_release_pinned: null,
        public_visibility: "aggregate_view",
      }),
    );
    const user = userEvent.setup();
    render(<App />);

    await user.click(screen.getByRole("button", { name: /change crisis/i }));
    await waitFor(() => {
      expect(screen.getByText(/which crises are you reporting on/i)).toBeInTheDocument();
    });
  });
});

describe("Multi-draft Home rendering", () => {
  it("shows the Continue-draft control without a count badge for a single draft", async () => {
    localStorage.setItem(LANG_KEY, "en");
    await saveDraft({
      id: "d1",
      state: {
        damage_class: "minimal",
        infra_type: [],
        infra_type_other: "",
        infra_name: "Bridge A",
        description: "",
        crisis_nature: null,
        crisis_nature_type: null,
        crisis_nature_other: "",
        debris: null,
        electricity: null,
        health_services: null,
        pressing_needs: [],
        crisis_id: "",
        latitude: null,
        longitude: null,
        route_description: "",
        photo_metadata: null,
        generic_answers: {},
      },
      photo_id: null,
      step: 0,
      updated_at: Date.now(),
    });
    render(<App />);
    // A lone draft resumes directly, so the pill carries no count badge.
    const btn = await screen.findByRole("button", { name: /Continue draft/i });
    expect(btn.textContent).not.toMatch(/\d/);
  });

  it("shows the draft count on the Continue-draft control for 3+ drafts", async () => {
    localStorage.setItem(LANG_KEY, "en");
    const baseState = {
      damage_class: null,
      infra_type: [],
      infra_type_other: "",
      description: "",
      crisis_nature: null,
      crisis_nature_type: null,
      crisis_nature_other: "",
      debris: null,
      electricity: null,
      health_services: null,
      pressing_needs: [],
      crisis_id: "",
      latitude: null,
      longitude: null,
      route_description: "",
      photo_metadata: null,
      generic_answers: {},
    };
    await saveDraft({
      id: "d1",
      state: { ...baseState, infra_name: "A" },
      photo_id: null,
      step: 0,
      updated_at: 1,
    });
    await saveDraft({
      id: "d2",
      state: { ...baseState, infra_name: "B" },
      photo_id: null,
      step: 0,
      updated_at: 2,
    });
    await saveDraft({
      id: "d3",
      state: { ...baseState, infra_name: "C" },
      photo_id: null,
      step: 0,
      updated_at: 3,
    });
    render(<App />);
    // With several drafts the pill surfaces the count and routes to the
    // drafts list rather than resuming directly. Poll until the async draft
    // load settles (the count badge only appears once all 3 are loaded).
    await waitFor(() => {
      const btn = screen.getByRole("button", { name: /Continue draft/i });
      expect(btn.textContent).toContain("3");
    });
  });
});

describe("Draft persistence", () => {
  it("the drafts store is empty after a fresh boot", async () => {
    localStorage.setItem(LANG_KEY, "en");
    render(<App />);
    await waitFor(() => expect(screen.getByText(/Report damage/i)).toBeInTheDocument());
    expect(await loadDrafts()).toEqual([]);
  });
});
