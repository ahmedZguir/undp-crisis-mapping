import { type ReactNode, useCallback, useEffect, useMemo, useRef, useState } from "react";
import { fetchCrisisGeometry, listAdminCrises, listAdminReports } from "../../api/admin";
import type { ChatContextReport, SearchHit, SearchRequest, Strictness } from "../../api/search";
import { useAdminTimezone } from "../../hooks/useAdminTimezone";
import { formatCompact, formatFull, shortZoneLabel } from "../../lib/adminTimezone";
import { readWorkspace, writeWorkspace } from "../../lib/adminWorkspace";
import {
  DEFAULT_REPORT_FILTERS,
  type DebrisFilter,
  type LocationFilter,
  type ReportFilters,
  type TimeWindow,
  applyClientFilters,
  collectInfraTypes,
  timeFilterActive,
} from "../../lib/reportFilters";
import { STORAGE_KEYS } from "../../lib/storageKeys";
import type {
  AdminCrisis,
  AdminReportDamageClass,
  AdminReportListItem,
  AreaSearchHit,
  GeoJSONMultiPolygon,
  GeoJSONPolygon,
} from "../../types/admin";
import { AdminReportInspector } from "./AdminReportInspector";
import {
  type AdminMapDisplayMode,
  AdminReportsMap,
  type AdminReportsMapHandle,
} from "./AdminReportsMap";
import { AnalysisReportsSection } from "./AnalysisReportsSection";
import { CrisisExportSection } from "./CrisisExportSection";
import { Autocomplete } from "./ai/Autocomplete";
import { ChatPanel } from "./ai/ChatPanel";
import { SummaryPanel } from "./ai/SummaryPanel";
import {
  type LocationChip,
  areaHitToChip,
  buildSearchPayload,
  searchAreasForCountries,
  useAiSearch,
} from "./ai/useAiSearch";
import { CrisisGlyph } from "./crisisIcons";
import { AnalyticsPanel } from "./dashboard/AnalyticsPanel";
import { KpiRibbon } from "./dashboard/KpiRibbon";
import { Icon } from "./icons";
import { GuidedTour } from "./onboarding/GuidedTour";
import { OnboardingPanel } from "./onboarding/OnboardingPanel";
import { buildDashboardSteps } from "./onboarding/tourContent";

function crisisPillText(status: AdminCrisis["status"]): string {
  if (status === "active") return "live";
  if (status === "inactive") return "inactive";
  return "archived";
}

type ViewMode = "map" | "list";

type CrisisStatusFilter = "all" | "active" | "inactive" | "archived";

const CRISIS_STATUS_FILTERS: readonly { id: CrisisStatusFilter; label: string }[] = [
  { id: "all", label: "All" },
  { id: "active", label: "Live" },
  { id: "inactive", label: "Inactive" },
  { id: "archived", label: "Archived" },
];

const LIST_PAGE_SIZE = 50;

// --- URL hash plumbing ---------------------------------------------------
// `#/dashboard?crisis=<uuid>`: read on mount, mirrored on change. View mode
// and the damage-class filter are intentionally not in the hash.

const UUID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

function crisisFromHash(): string | null {
  const raw = window.location.hash.replace(/^#\/?[^?]*/, "");
  const params = new URLSearchParams(raw.startsWith("?") ? raw.slice(1) : raw);
  const c = params.get("crisis");
  return c && UUID_RE.test(c) ? c : null;
}

function setCrisisInHash(crisisId: string | null): void {
  const base = window.location.hash.match(/^#\/?[^?]*/)?.[0] ?? "#/dashboard";
  const next = crisisId ? `${base}?crisis=${crisisId}` : base;
  if (window.location.hash !== next) {
    window.history.replaceState(null, "", next);
  }
}

// Last selected crisis, per browser tab. Top-bar nav drops `?crisis=` from the
// hash, so this is the fallback; the hash stays the source of truth on refresh.
const CRISIS_STORAGE_KEY = STORAGE_KEYS.adminLastCrisis.key;

function storedCrisis(): string | null {
  try {
    const c = sessionStorage.getItem(CRISIS_STORAGE_KEY);
    return c && UUID_RE.test(c) ? c : null;
  } catch {
    return null;
  }
}

function rememberCrisis(crisisId: string | null): void {
  try {
    if (crisisId) sessionStorage.setItem(CRISIS_STORAGE_KEY, crisisId);
    else sessionStorage.removeItem(CRISIS_STORAGE_KEY);
  } catch {
    // Private-mode / storage-disabled: fall back to hash-only behaviour.
  }
}

const DAMAGE_CLASSES: AdminReportDamageClass[] = ["complete", "partial", "minimal"];

// Tags are stored snake_case; space them out for display.
function fmtInfraType(tags: string[] | null): string | null {
  if (!tags || tags.length === 0) return null;
  return tags.map((t) => t.replace(/_/g, " ")).join(", ");
}

// A list-view cell clamped to `lines` lines with an ellipsis. The full value is
// in the title tooltip and the inspector.
function ClampCell({
  text,
  lines = 2,
  capitalize = false,
}: {
  text: string | null;
  lines?: number;
  capitalize?: boolean;
}) {
  const value = text?.trim() ?? "";
  if (!value) return <span className="lv-empty">—</span>;
  return (
    <span
      className={`lv-cell${capitalize ? " cap" : ""}`}
      title={value}
      style={{ WebkitLineClamp: lines }}
    >
      {value}
    </span>
  );
}

// --- AI inspector tab state --------------------------------------------
// Tabs: Summary, Chat, Analysis (id "snapshots": live-analysis link plus the
// PDF report generator), Export, and Detail (the inspector for a selected pin).
// Detail is the default when a report is selected, Summary otherwise.
type InspectorTab = "detail" | "summary" | "chat" | "snapshots" | "export";

// Dashboard UI state persisted per browser tab (sessionStorage) so a refresh or
// an Analysis-tab round-trip restores it. Crisis-bound state (selected report,
// infra-type chips, chat context, location chips) is deliberately excluded.
interface DashboardWorkspace {
  view: ViewMode;
  mapDisplay: AdminMapDisplayMode;
  damageClass: AdminReportDamageClass | null;
  filters: ReportFilters;
  inspectorTab: InspectorTab;
}

const DASHBOARD_WORKSPACE_KEY = "dashboard";

const DEFAULT_DASHBOARD_WORKSPACE: DashboardWorkspace = {
  view: "map",
  mapDisplay: "points",
  damageClass: null,
  filters: DEFAULT_REPORT_FILTERS,
  inspectorTab: "summary",
};

export function ReportsPage({
  onboardingActive = false,
  onOnboardingDashboardDone,
  dashboardExplored = true,
  onDashboardExplored,
  analysisVisited = false,
}: {
  /** Set by the first-run walkthrough (AdminApp): selects the demo crisis
   *  and starts the dashboard tour chapter. */
  onboardingActive?: boolean;
  /** Called when that chapter finishes or is skipped. */
  onOnboardingDashboardDone?: () => void;
  /** Once the Explore checklist is completed, the OnboardingPanel retires for
   *  good. Defaults true so the panel stays hidden while the flag loads. */
  dashboardExplored?: boolean;
  /** Called when the Explore checklist completes, to persist the retirement. */
  onDashboardExplored?: () => void;
  /** True once the coordinator has visited the live analysis page (ticks a
   *  checklist task). */
  analysisVisited?: boolean;
} = {}) {
  const [crises, setCrises] = useState<AdminCrisis[]>([]);
  const [crisesError, setCrisesError] = useState<string | null>(null);
  const [crisisId, setCrisisIdState] = useState<string | null>(
    () => crisisFromHash() ?? storedCrisis(),
  );
  const [crisisStatusFilter, setCrisisStatusFilter] = useState<CrisisStatusFilter>("active");
  // Restored once from the persisted per-tab workspace, via a ref so it happens
  // exactly once.
  const wsSeedRef = useRef<DashboardWorkspace | null>(null);
  if (wsSeedRef.current === null) {
    // Merge over defaults so a partial stored blob can't seed an undefined field.
    wsSeedRef.current = {
      ...DEFAULT_DASHBOARD_WORKSPACE,
      ...readWorkspace<Partial<DashboardWorkspace>>(DASHBOARD_WORKSPACE_KEY, {}),
    };
  }
  const wsSeed = wsSeedRef.current;
  const [view, setView] = useState<ViewMode>(wsSeed.view);
  const [mapDisplay, setMapDisplay] = useState<AdminMapDisplayMode>(wsSeed.mapDisplay);
  const [damageClass, setDamageClass] = useState<AdminReportDamageClass | null>(wsSeed.damageClass);
  const [filters, setFilters] = useState<ReportFilters>(
    // Infra-type tokens are per-crisis; never restore a stale set.
    () => ({ ...DEFAULT_REPORT_FILTERS, ...wsSeed.filters, infraTypes: [] }),
  );
  // Infra-type tokens seen so far; drives the dynamic infra multiselect chips.
  const [infraOptions, setInfraOptions] = useState<string[]>([]);
  const [selectedReportId, setSelectedReportId] = useState<string | null>(null);
  const [inspectorTab, setInspectorTab] = useState<InspectorTab>(wsSeed.inspectorTab);
  // Reports the AI chat has in context (its locked K-set) plus the report a
  // citation click asks the map to focus. `chatFocus.seq` bumps on every click so
  // re-clicking the same citation re-flies. Cleared on crisis change.
  const [chatContext, setChatContext] = useState<ChatContextReport[]>([]);
  const [chatFocus, setChatFocus] = useState<{ id: string; seq: number } | null>(null);
  const chatFocusSeqRef = useRef(0);
  const handleCiteFocus = useCallback((id: string) => {
    chatFocusSeqRef.current += 1;
    setChatFocus({ id, seq: chatFocusSeqRef.current });
  }, []);
  // Latched true on the first Summarise run (not on opening the tab), so the
  // "Try it yourself" task reflects a real action.
  const [summaryViewed, setSummaryViewed] = useState(false);
  const markSummarised = useCallback(() => setSummaryViewed(true), []);
  const showInspectorTab = useCallback((t: InspectorTab) => {
    setInspectorTab(t);
  }, []);
  // Polygon-draw mode: the map intercepts clicks and calls back with the
  // finalized GeoJSON, which becomes a chip and clears the mode.
  const [drawingActive, setDrawingActive] = useState(false);
  const drawIdRef = useRef(0);
  // Imperative map handle, used by the tour's zoom demo.
  const mapHandleRef = useRef<AdminReportsMapHandle | null>(null);
  // Guided dashboard tour, opened only by the onboarding effect below; forces the
  // map view so every step's anchor is present. `obRunRef` marks whether the open
  // tour is the onboarding chapter, so closing it reports back.
  const [tourOpen, setTourOpen] = useState(false);
  const obRunRef = useRef(false);
  const obStartedRef = useRef(false);
  const handleTourClose = useCallback(() => {
    setTourOpen(false);
    if (obRunRef.current) {
      obRunRef.current = false;
      onOnboardingDashboardDone?.();
    }
  }, [onOnboardingDashboardDone]);

  // Viewport bbox `[w, s, e, n]` for `useAiSearch` (location chips supersede
  // it). Reset in list view so a stale bbox doesn't silently narrow the search.
  const [mapBbox, setMapBbox] = useState<[number, number, number, number] | null>(null);
  useEffect(() => {
    if (view !== "map") setMapBbox(null);
  }, [view]);

  // AI search auto-fires whenever any filter (incl. the map bbox) changes.
  // Query and location chips are filters, not gates.
  const ai = useAiSearch({ crisisId, damageClass, filters, bbox: mapBbox });

  const setCrisisId = useCallback((next: string | null) => {
    setCrisisIdState(next);
    setCrisisInHash(next);
    rememberCrisis(next);
    // View mode, map display and filters carry across crisis switches. Only
    // crisis-bound state resets: selected report, infra-type token pool (and chips
    // drawn from it), and chat context.
    setSelectedReportId(null);
    setInfraOptions([]);
    setFilters((f) => (f.infraTypes.length ? { ...f, infraTypes: [] } : f));
    setChatContext([]);
    setChatFocus(null);
  }, []);

  // Persist the workspace slice (best-effort). Infra-type tokens are per-crisis
  // and intentionally not persisted.
  useEffect(() => {
    writeWorkspace<DashboardWorkspace>(DASHBOARD_WORKSPACE_KEY, {
      view,
      mapDisplay,
      damageClass,
      filters: { ...filters, infraTypes: [] },
      inspectorTab,
    });
  }, [view, mapDisplay, damageClass, filters, inspectorTab]);

  // Selecting a report jumps to the Detail tab; clearing returns to Summary.
  const handleSelectReport = useCallback((id: string | null) => {
    setSelectedReportId(id);
    if (id) setInspectorTab("detail");
  }, []);

  // --- Guided walkthrough wiring -----------------------------------------
  // Each tour demo drives the page and returns a cleanup that reverts it when
  // its step is left.
  // The filter demo's damage class is resolved up front so the step copy can name
  // it. Most-populated class so the view is never empty; "partial" before stats load.
  const demoDamage = useMemo<AdminReportDamageClass>(() => {
    const sev = ai.baselineStats?.severity;
    const counts: Record<AdminReportDamageClass, number> = {
      complete: sev?.complete ?? 0,
      partial: sev?.partial ?? 0,
      minimal: sev?.minimal ?? 0,
    };
    const pick = (Object.keys(counts) as AdminReportDamageClass[]).reduce(
      (best, k) => (counts[k] > counts[best] ? k : best),
      "partial" as AdminReportDamageClass,
    );
    return counts[pick] > 0 ? pick : "partial";
  }, [ai.baselineStats]);

  const runFilterDemo = useCallback(() => {
    setDamageClass(demoDamage);
    return () => setDamageClass(null);
  }, [demoDamage]);

  const runZoomDemo = useCallback(() => {
    mapHandleRef.current?.demoZoomIn();
    return () => mapHandleRef.current?.resetView();
  }, []);

  const dashboardSteps = useMemo(
    () =>
      buildDashboardSteps(
        { runFilterDemo, runZoomDemo, showTool: setInspectorTab },
        {
          hasAnomalies: ai.anomalyIds.size > 0,
          filterLabel: demoDamage.charAt(0).toUpperCase() + demoDamage.slice(1),
        },
      ),
    [runFilterDemo, runZoomDemo, ai.anomalyIds.size, demoDamage],
  );

  // On activation of the dashboard chapter, select the demo crisis ("Turkey
  // Earthquake" if present) and open the tour once crises load. Once per activation.
  useEffect(() => {
    if (!onboardingActive) {
      obStartedRef.current = false;
      return;
    }
    if (obStartedRef.current || crises.length === 0) return;
    obStartedRef.current = true;
    obRunRef.current = true;
    const demo =
      crises.find((c) => c.name?.toLowerCase().includes("turkey earthquake")) ??
      crises.find((c) => c.status === "active") ??
      crises[0];
    if (demo) setCrisisId(demo.id);
    setView("map");
    setTourOpen(true);
  }, [onboardingActive, crises, setCrisisId]);

  // Grow the infra-type chip pool from the server stats' infra breakdown
  // (baseline + filtered view). The map is server-clustered and ships no
  // per-report items, and the breakdown covers the full set, not a sample.
  useEffect(() => {
    const seen = new Set<string>();
    for (const k of Object.keys(ai.baselineStats?.infra_breakdown ?? {})) seen.add(k);
    for (const k of Object.keys(ai.displayResponse?.stats?.infra_breakdown ?? {})) seen.add(k);
    if (seen.size === 0) return;
    setInfraOptions((prev) => {
      const merged = new Set(prev);
      for (const t of seen) merged.add(t);
      if (merged.size === prev.length) return prev;
      return [...merged].sort();
    });
  }, [ai.baselineStats, ai.displayResponse]);

  // The list view also grows the chip pool from its loaded sample. Counts never
  // come from here: the AI search response is the single source of truth.
  const handleDataChange = useCallback(
    (next: {
      items: ListRow[];
      totalInBbox: number | null;
      truncated: boolean;
      loading: boolean;
    }) => {
      const seen = collectInfraTypes(next.items);
      if (seen.length === 0) return;
      setInfraOptions((prev) => {
        const merged = new Set(prev);
        for (const t of seen) merged.add(t);
        if (merged.size === prev.length) return prev;
        return [...merged].sort();
      });
    },
    [],
  );

  useEffect(() => {
    let alive = true;
    listAdminCrises()
      .then((list) => {
        if (!alive) return;
        setCrises(list);
      })
      .catch((err) => {
        if (!alive) return;
        setCrisesError(err instanceof Error ? err.message : String(err));
      });
    return () => {
      alive = false;
    };
  }, []);

  // Resolve a default crisis once crises load. A crisisId restored from storage
  // isn't in the hash yet, so mirror it there to keep the URL shareable.
  useEffect(() => {
    if (crises.length === 0) return;
    if (crisisId && crises.some((c) => c.id === crisisId)) {
      setCrisisInHash(crisisId);
      rememberCrisis(crisisId);
      return;
    }
    // Prefer the Turkey Earthquake crisis (the primary demo crisis), then any
    // active crisis, then the first. Only when nothing was restored.
    const def =
      crises.find((c) => c.name?.toLowerCase().includes("turkey earthquake")) ??
      crises.find((c) => c.status === "active") ??
      crises[0];
    setCrisisId(def?.id ?? null);
  }, [crises, crisisId, setCrisisId]);

  // React to external `?crisis=` changes (a deep-link opened in a running tab).
  // In-app selection uses replaceState, which fires no `hashchange`, so this never
  // echoes our own writes. A bare `#/dashboard` keeps the current selection.
  useEffect(() => {
    const onHash = () => {
      const fromHash = crisisFromHash();
      if (fromHash && fromHash !== crisisId && crises.some((c) => c.id === fromHash)) {
        setCrisisId(fromHash);
      }
    };
    window.addEventListener("hashchange", onHash);
    return () => window.removeEventListener("hashchange", onHash);
  }, [crises, crisisId, setCrisisId]);

  const currentCrisis = useMemo(
    () => crises.find((c) => c.id === crisisId) ?? null,
    [crises, crisisId],
  );

  // The crisis AOI polygon for the boundary outline, fetched lazily only when the
  // crisis has geometry; cleared between selections so no stale outline shows.
  const [crisisGeometry, setCrisisGeometry] = useState<GeoJSONPolygon | GeoJSONMultiPolygon | null>(
    null,
  );
  useEffect(() => {
    setCrisisGeometry(null);
    if (!crisisId || !currentCrisis?.has_geometry) return;
    const ctrl = new AbortController();
    fetchCrisisGeometry(crisisId, { signal: ctrl.signal })
      .then((geom) => setCrisisGeometry(geom))
      .catch(() => {
        // Non-fatal: the map renders without the boundary outline.
      });
    return () => ctrl.abort();
  }, [crisisId, currentCrisis?.has_geometry]);

  const anomaliesOnlyActive = ai.state.anomaliesOnly && ai.anomalyIds.size > 0;

  const locationMarkers = useMemo(
    () =>
      ai.state.locations.map((l) => ({
        kind: l.kind,
        id: l.id,
        name: l.name,
        centroid: l.centroid,
      })),
    [ai.state.locations],
  );

  // Finalize callback for polygon-draw mode.
  const handlePolygonFinish = useCallback(
    (polygon: GeoJSON.Polygon) => {
      drawIdRef.current += 1;
      const id = `drawn-${drawIdRef.current}`;
      let minLat = Number.POSITIVE_INFINITY;
      let maxLat = Number.NEGATIVE_INFINITY;
      let minLng = Number.POSITIVE_INFINITY;
      let maxLng = Number.NEGATIVE_INFINITY;
      for (const [lng, lat] of polygon.coordinates[0]) {
        if (lat < minLat) minLat = lat;
        if (lat > maxLat) maxLat = lat;
        if (lng < minLng) minLng = lng;
        if (lng > maxLng) maxLng = lng;
      }
      const centroid = { lat: (minLat + maxLat) / 2, lng: (minLng + maxLng) / 2 };
      ai.addLocation({
        kind: "polygon",
        id,
        name: `Drawn area ${drawIdRef.current}`,
        centroid,
        polygon,
      });
      setDrawingActive(false);
    },
    [ai.addLocation],
  );

  const scopeTotal = ai.displayResponse?.total_match_count ?? null;

  const activeFilters =
    activeStructuredCount(filters) +
    (damageClass ? 1 : 0) +
    (ai.state.locations.length > 0 ? 1 : 0) +
    (ai.state.query.trim() ? 1 : 0);

  // The full server-side filter payload minus the viewport bbox (the map supplies
  // its own bbox and zoom), so the map and KPI ribbon filter identically.
  const mapFilterPayload = useMemo(
    () => buildSearchPayload(ai.state, damageClass, filters, null),
    [ai.state, damageClass, filters],
  );

  const resetAll = useCallback(() => {
    setFilters(DEFAULT_REPORT_FILTERS);
    setDamageClass(null);
    ai.setQuery("");
    ai.setAnomaliesOnly(false);
    ai.clearLocations();
    setDrawingActive(false);
  }, [ai]);

  return (
    <div className="dash-root">
      <div className="page-hero is-compact">
        <div className="page-hero-inner">
          <div className="hero-title" style={{ flex: 1 }}>
            <h1>Analyze Crises</h1>
          </div>
        </div>
      </div>
      {/* Hidden during the guided tour so the checklist and coachmarks don't compete. */}
      {!tourOpen && (
        <OnboardingPanel
          crises={crises}
          currentCrisis={currentCrisis}
          selectedCrisisId={crisisId}
          reportCount={ai.baselineStats?.total ?? null}
          queryActive={ai.state.query.trim().length > 0}
          view={view}
          selectedReportId={selectedReportId}
          summaryViewed={summaryViewed}
          analysisViewed={analysisVisited}
          dashboardExplored={dashboardExplored}
          onDashboardExplored={onDashboardExplored}
        />
      )}
      <FilterStrip
        crises={crises}
        crisesError={crisesError}
        crisisId={crisisId}
        onCrisisChange={setCrisisId}
        crisisStatusFilter={crisisStatusFilter}
        onCrisisStatusFilterChange={setCrisisStatusFilter}
        damageClass={damageClass}
        onDamageClassChange={setDamageClass}
        filters={filters}
        onFiltersChange={setFilters}
        infraOptions={infraOptions}
        query={ai.state.query}
        onQueryChange={ai.setQuery}
        strictness={ai.state.strictness}
        onStrictnessChange={ai.setStrictness}
        crisisCountries={currentCrisis?.countries ?? []}
        onAddLocation={ai.addLocation}
        locations={ai.state.locations}
        onRemoveLocation={ai.removeLocation}
        anomalyCount={ai.anomalyIds.size}
        anomaliesOnly={ai.state.anomaliesOnly}
        onAnomaliesOnlyChange={ai.setAnomaliesOnly}
        activeFilters={activeFilters}
        onReset={resetAll}
      />

      {crisisId != null && (
        // Same server-computed aggregates as the chat surface, over the full filtered
        // set. `baseline` holds the whole-crisis "of N" denominators.
        <KpiRibbon
          stats={ai.displayResponse?.stats ?? null}
          baseline={ai.baselineStats}
          loading={ai.loading}
        />
      )}

      {crisisId != null && view === "map" && (ai.baselineStats?.unmapped ?? 0) > 0 && (
        <OffMapNotice count={ai.baselineStats?.unmapped ?? 0} />
      )}

      {crisisId != null && view === "list" && (ai.baselineStats?.unmapped ?? 0) > 0 && (
        <ListLocationNote count={ai.baselineStats?.unmapped ?? 0} />
      )}

      {crisisId == null ? (
        <div className="body" style={{ gridTemplateColumns: "1fr" }}>
          <div className="panel center">
            <EmptyState
              title={
                crisesError
                  ? "Couldn't load crises"
                  : crises.length === 0
                    ? "No crises yet"
                    : "Pick a crisis to load reports"
              }
              hint={
                crisesError
                  ? crisesError
                  : crises.length === 0
                    ? "Create one from the Crises tab to start collecting reports."
                    : "Use the crisis selector above. Only reports for the chosen crisis are shown."
              }
              tone={crisesError ? "danger" : "neutral"}
            />
          </div>
        </div>
      ) : (
        <div className="body">
          <AnalyticsPanel stats={ai.displayResponse?.stats ?? null} loading={ai.loading} />

          <div className="panel center">
            <div className="stage-bar">
              <ViewSeg view={view} onChange={setView} />
              <div className="stage-right">
                {view === "map" && <MapModes value={mapDisplay} onChange={setMapDisplay} />}
              </div>
            </div>

            {view === "map" ? (
              <div className="mapwrap">
                <AdminReportsMap
                  ref={mapHandleRef}
                  crisisId={crisisId}
                  crisisBbox={currentCrisis?.bbox ?? null}
                  crisisGeometry={crisisGeometry}
                  pmtilesUrl={currentCrisis?.pmtiles_url ?? null}
                  displayMode={mapDisplay}
                  filterPayload={mapFilterPayload}
                  hasActiveFilters={activeFilters > 0}
                  selectedReportId={selectedReportId}
                  onSelect={handleSelectReport}
                  onBboxChange={setMapBbox}
                  anomaliesOnly={anomaliesOnlyActive}
                  locationMarkers={locationMarkers}
                  locationPolygons={ai.locationPolygons}
                  focusedLocation={ai.focused}
                  drawingActive={drawingActive}
                  onDrawingFinish={handlePolygonFinish}
                  chatContextReports={chatContext}
                  chatFocusReport={chatFocus}
                />
                <MapResetControl
                  onReset={() => {
                    // Back to the full-event view: drop searched areas and reframe on the crisis.
                    ai.clearLocations();
                    mapHandleRef.current?.resetView();
                  }}
                />
                <MapDrawControl
                  active={drawingActive}
                  onToggle={() => setDrawingActive((v) => !v)}
                />
                <MapLegendOverlay mode={mapDisplay} chatContextCount={chatContext.length} />
              </div>
            ) : (
              <ReportsList
                crisisId={crisisId}
                damageClass={damageClass}
                filters={filters}
                selectedReportId={selectedReportId}
                onSelect={handleSelectReport}
                onDataChange={handleDataChange}
                searchHits={ai.searchIds ? (ai.response?.rows ?? null) : null}
                searchLoading={ai.loading}
                searchOverlayIds={ai.searchIds}
                anomalyIds={ai.anomalyIds}
                anomaliesOnly={anomaliesOnlyActive}
                totalMatchCount={scopeTotal}
              />
            )}
          </div>

          <InspectorPane
            tab={inspectorTab}
            onTabChange={showInspectorTab}
            selectedReportId={selectedReportId}
            onCiteClick={handleSelectReport}
            onChatContextChange={setChatContext}
            onCiteFocus={handleCiteFocus}
            crisisId={crisisId}
            crisisName={currentCrisis?.name ?? null}
            searchPayload={ai.committedPayload}
            searchReady={ai.candidateNonEmpty}
            filterVersion={ai.filterVersion}
            currentTotal={ai.response?.total_match_count ?? null}
            scopeTotal={scopeTotal}
            // Export the exact payload behind the on-screen results, including the viewport
            // bbox (`mapFilterPayload` drops it), so the bundle matches `scopeTotal`.
            exportFilter={ai.committedPayload}
            crisisTotal={ai.baselineStats?.total ?? null}
            hasActiveFilters={activeFilters > 0}
            onSummarised={markSummarised}
          />
        </div>
      )}
      <GuidedTour
        open={tourOpen}
        steps={dashboardSteps}
        chapter="Chapter 2 of 2"
        onClose={handleTourClose}
      />
    </div>
  );
}

// --- On-map reset-view control ------------------------------------------
// Re-frames the map on the crisis AOI, undoing any pan/zoom.
function MapResetControl({ onReset }: { onReset: () => void }) {
  return (
    <button
      type="button"
      className="ov map-reset"
      onClick={onReset}
      title="Frame the whole crisis area and clear any searched areas"
    >
      <svg
        viewBox="0 0 24 24"
        fill="none"
        stroke="currentColor"
        strokeWidth="2"
        strokeLinecap="round"
        strokeLinejoin="round"
        aria-hidden="true"
      >
        <path d="M4 9V5a1 1 0 0 1 1-1h4" />
        <path d="M20 9V5a1 1 0 0 0-1-1h-4" />
        <path d="M4 15v4a1 1 0 0 0 1 1h4" />
        <path d="M20 15v4a1 1 0 0 1-1 1h-4" />
      </svg>
      Reset view
    </button>
  );
}

// --- On-map draw control ------------------------------------------------
// A bottom-right pill toggles polygon-draw mode; while armed it becomes
// "Cancel" and a top-center banner explains the gesture.
function MapDrawControl({ active, onToggle }: { active: boolean; onToggle: () => void }) {
  return (
    <>
      {active && (
        <output className="ov draw-banner">
          Click the map to drop points, then close the shape to apply.
        </output>
      )}
      <button
        type="button"
        className={`ov draw-ctrl${active ? " armed" : ""}`}
        onClick={onToggle}
        aria-pressed={active}
        title={active ? "Cancel drawing" : "Draw a polygon on the map to filter by area"}
      >
        <svg
          viewBox="0 0 24 24"
          fill="none"
          stroke="currentColor"
          strokeWidth="2"
          strokeLinecap="round"
          strokeLinejoin="round"
          aria-hidden="true"
        >
          <path d="M12 19l7-7 3 3-7 7-3-3z" />
          <path d="M18 13l-1.5-7.5L2 2l3.5 14.5L13 18z" />
          <path d="m2 2 7.586 7.586" />
          <circle cx="11" cy="11" r="2" />
        </svg>
        {active ? "Cancel" : "Draw area"}
      </button>
    </>
  );
}

// --- On-map legend overlay ----------------------------------------------
// Mirrors the map's fixed severity palette; a density ramp for heatmap.
// Cyan chip matching the chat-context overlay, shown only while the AI chat has
// reports in context.
function ChatContextLegend({ count }: { count: number }) {
  if (count <= 0) return null;
  return (
    <>
      <div className="lgrp" style={{ marginTop: 6 }}>
        AI chat
      </div>
      <div className="lg">
        <span
          className="cd"
          style={{
            background: "#06b6d4",
            border: "2px solid #fff",
            boxShadow: "0 0 0 3px rgba(6,182,212,0.18)",
          }}
        />
        In chat context ({count})
      </div>
    </>
  );
}

function MapLegendOverlay({
  mode,
  chatContextCount = 0,
}: {
  mode: AdminMapDisplayMode;
  chatContextCount?: number;
}) {
  if (mode === "heatmap") {
    return (
      <div className="ov legend-map" aria-label="Map legend">
        <div className="lgrp">Report density</div>
        <div
          style={{
            height: 8,
            width: 132,
            borderRadius: 2,
            background: "linear-gradient(90deg,#fde68a,#fcd34d,#fb923c,#dc2626,#7f1d1d)",
          }}
        />
        <div
          style={{
            display: "flex",
            justifyContent: "space-between",
            fontSize: 10,
            color: "var(--faint)",
            marginTop: 4,
          }}
        >
          <span>Low</span>
          <span>High</span>
        </div>
        <ChatContextLegend count={chatContextCount} />
      </div>
    );
  }
  const items: { c: string; label: string }[] = [
    { c: "var(--complete)", label: "Complete damage" },
    { c: "var(--partial)", label: "Partial damage" },
    { c: "var(--minimal)", label: "Minimal damage" },
  ];
  return (
    <div className="ov legend-map" aria-label="Map legend">
      <div className="lgrp">Damage class</div>
      {items.map((it) => (
        <div className="lg" key={it.label}>
          <span className="cd" style={{ background: it.c }} />
          {it.label}
        </div>
      ))}
      <ChatContextLegend count={chatContextCount} />
    </div>
  );
}

// --- Filter bar ----------------------------------------------------------
// Crisis scope, semantic search + strictness, filter facets, location + draw,
// live count, reset.

interface FilterStripProps {
  crises: AdminCrisis[];
  crisesError: string | null;
  crisisId: string | null;
  onCrisisChange: (id: string) => void;
  crisisStatusFilter: CrisisStatusFilter;
  onCrisisStatusFilterChange: (s: CrisisStatusFilter) => void;
  damageClass: AdminReportDamageClass | null;
  onDamageClassChange: (d: AdminReportDamageClass | null) => void;
  filters: ReportFilters;
  onFiltersChange: (f: ReportFilters) => void;
  infraOptions: string[];
  query: string;
  onQueryChange: (q: string) => void;
  strictness: Strictness;
  onStrictnessChange: (s: Strictness) => void;
  crisisCountries: string[];
  onAddLocation: (chip: LocationChip) => void;
  locations: LocationChip[];
  onRemoveLocation: (kind: LocationChip["kind"], id: string) => void;
  anomalyCount: number;
  anomaliesOnly: boolean;
  onAnomaliesOnlyChange: (on: boolean) => void;
  activeFilters: number;
  onReset: () => void;
}

function FilterStrip({
  crises,
  crisesError,
  crisisId,
  onCrisisChange,
  crisisStatusFilter,
  onCrisisStatusFilterChange,
  damageClass,
  onDamageClassChange,
  filters,
  onFiltersChange,
  infraOptions,
  query,
  onQueryChange,
  strictness,
  onStrictnessChange,
  crisisCountries,
  onAddLocation,
  locations,
  onRemoveLocation,
  anomalyCount,
  anomaliesOnly,
  onAnomaliesOnlyChange,
  activeFilters,
  onReset,
}: FilterStripProps) {
  // The semantic query commits on Enter/clear, not per keystroke (semantic search
  // is expensive). `searchDraft` is the input; `query` feeds the payload. The draft
  // resyncs when the committed value changes externally (reset, programmatic clears).
  const [searchDraft, setSearchDraft] = useState(query);
  useEffect(() => {
    setSearchDraft(query);
  }, [query]);
  const searchDirty = searchDraft.trim() !== query.trim();
  const commitSearch = () => {
    if (searchDirty) onQueryChange(searchDraft.trim());
  };
  const clearSearch = () => {
    setSearchDraft("");
    onQueryChange("");
  };

  const update = <K extends keyof ReportFilters>(key: K, value: ReportFilters[K]) => {
    onFiltersChange({ ...filters, [key]: value });
  };
  const toggleInfra = (t: string) => {
    if (filters.infraTypes.includes(t)) {
      update(
        "infraTypes",
        filters.infraTypes.filter((x) => x !== t),
      );
    } else {
      update("infraTypes", [...filters.infraTypes, t]);
    }
  };

  const statusCounts = useMemo(() => {
    const c = { all: crises.length, active: 0, inactive: 0, archived: 0 } as Record<
      CrisisStatusFilter,
      number
    >;
    for (const cr of crises) {
      if (cr.status === "active") c.active++;
      else if (cr.status === "inactive") c.inactive++;
      else c.archived++;
    }
    return c;
  }, [crises]);

  const visibleCrises = useMemo(() => {
    if (crisisStatusFilter === "all") return crises;
    return crises.filter((c) => c.status === crisisStatusFilter);
  }, [crises, crisisStatusFilter]);

  const selectedCrisis = useMemo(
    () => crises.find((c) => c.id === crisisId) ?? null,
    [crises, crisisId],
  );

  const damageVal =
    damageClass === "complete"
      ? "Complete"
      : damageClass === "partial"
        ? "Partial"
        : damageClass === "minimal"
          ? "Minimal"
          : null;
  const timeVal = timeFacetLabel(filters);
  const debrisVal = shortFor(DEBRIS_OPTS, filters.debris);
  const locVal = shortFor(LOCATION_OPTS, filters.location);
  const infraVal =
    filters.infraTypes.length > 0
      ? filters.infraTypes.length === 1
        ? filters.infraTypes[0].replace(/_/g, " ")
        : `${filters.infraTypes.length} types`
      : null;

  return (
    <div className="filterbar">
      <div className="filt-row">
        <CrisisChooser
          crisisId={crisisId}
          crises={crises}
          visibleCrises={visibleCrises}
          selectedCrisis={selectedCrisis}
          onCrisisChange={onCrisisChange}
          statusFilter={crisisStatusFilter}
          onStatusFilterChange={onCrisisStatusFilterChange}
          statusCounts={statusCounts}
        />
        {crisesError && (
          <span
            style={{ fontSize: 11, color: "var(--c-danger)", maxWidth: 200 }}
            title={crisesError}
          >
            {crisesError}
          </span>
        )}

        <span className="fsep" />

        <div className={`search${searchDirty ? " dirty" : ""}`} data-tour="search">
          <span className="se-mode has-tip">
            <Icon.brain width="13" height="13" />
            <span>Semantic</span>
            <span className="tip-pop" role="tooltip">
              <span className="tip-h">Semantic search</span>
              <span className="tip-text">
                Finds reports by meaning, not exact words: searching “flooded schools” also surfaces
                “water damage to the classroom.” Because it weighs meaning rather than matching
                text, it gets the occasional call wrong, so read the ranking as a strong hint, not a
                guarantee.
              </span>
            </span>
          </span>
          <input
            type="text"
            className="search-input"
            value={searchDraft}
            onChange={(e) => setSearchDraft(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter") {
                e.preventDefault();
                commitSearch();
              }
            }}
            placeholder="Describe what you want to find, then press Enter…"
            aria-label="Semantic search across reports"
          />
          {searchDirty && searchDraft.trim().length > 0 && (
            <span className="se-enter" aria-hidden="true">
              ↵ Enter
            </span>
          )}
          {searchDraft.length > 0 && (
            <button
              type="button"
              className="se-clear"
              aria-label="Clear search"
              onClick={clearSearch}
            >
              <svg
                aria-hidden="true"
                viewBox="0 0 24 24"
                fill="none"
                stroke="currentColor"
                strokeWidth="2.2"
                strokeLinecap="round"
              >
                <path d="M6 6l12 12M18 6 6 18" />
              </svg>
            </button>
          )}
        </div>

        <StrictSeg value={strictness} onChange={onStrictnessChange} />

        <span className="fsep" />
        <span className="filt-facets" data-tour="filters">
          <span className="filt-lead">Filters</span>

          <FacetPill
            label="Damage"
            valueLabel={damageVal}
            renderPopover={(close) => (
              <DamageList
                value={damageClass}
                onChange={(d) => {
                  onDamageClassChange(d);
                  close();
                }}
              />
            )}
          />
          <FacetPill
            label="Time"
            valueLabel={timeVal}
            renderPopover={(close) => (
              <TimeFilterList
                filters={filters}
                onSelectRelative={(v) => {
                  // A relative window discards custom bounds so the two modes never coexist.
                  onFiltersChange({ ...filters, timeWindow: v, customFrom: null, customTo: null });
                  close();
                }}
                onCustomChange={(from, to) =>
                  onFiltersChange({
                    ...filters,
                    timeWindow: "custom",
                    customFrom: from,
                    customTo: to,
                  })
                }
              />
            )}
          />
          <FacetPill
            label="Debris"
            valueLabel={debrisVal}
            renderPopover={(close) => (
              <SingleSelectList
                options={DEBRIS_OPTS}
                value={filters.debris}
                onChange={(v) => {
                  update("debris", v);
                  close();
                }}
              />
            )}
          />
          <FacetPill
            label="Location source"
            valueLabel={locVal}
            renderPopover={(close) => (
              <SingleSelectList
                options={LOCATION_OPTS}
                value={filters.location}
                onChange={(v) => {
                  update("location", v);
                  close();
                }}
              />
            )}
          />
          <FacetPill
            label="Infrastructure"
            valueLabel={infraVal}
            align="end"
            renderPopover={() => (
              <InfraTypeList
                options={infraOptions}
                values={filters.infraTypes}
                onToggle={toggleInfra}
                onClear={filters.infraTypes.length > 0 ? () => update("infraTypes", []) : undefined}
              />
            )}
          />
        </span>

        <span className="fsep" />

        <MapAreaSearch crisisCountries={crisisCountries} onAddLocation={onAddLocation} />

        {locations.map((loc) => (
          <span className="facet geo on" key={`${loc.kind}:${loc.id}`}>
            <svg
              className="ic"
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              strokeWidth="2"
              strokeLinecap="round"
              strokeLinejoin="round"
              aria-hidden="true"
            >
              <path d="M12 21s-7-5.7-7-11a7 7 0 0 1 14 0c0 5.3-7 11-7 11Z" />
              <circle cx="12" cy="10" r="2.4" />
            </svg>
            <b title={loc.name}>{loc.name}</b>
            <button
              type="button"
              className="x"
              aria-label={`Remove ${loc.name}`}
              onClick={() => onRemoveLocation(loc.kind, loc.id)}
            >
              ×
            </button>
          </span>
        ))}

        {anomalyCount > 0 && (
          <button
            type="button"
            className={`facet${anomaliesOnly ? " on" : ""}`}
            onClick={() => onAnomaliesOnlyChange(!anomaliesOnly)}
            aria-pressed={anomaliesOnly}
            title="Reports semantically far from the matched centroid"
            data-tour="anomaly"
          >
            <span aria-hidden="true">⚑</span>
            <b>
              {anomalyCount} anomal{anomalyCount === 1 ? "y" : "ies"}
            </b>
          </button>
        )}

        <div className="count">
          <b className="num">{activeFilters}</b> active filter{activeFilters === 1 ? "" : "s"}
        </div>

        {activeFilters > 0 && (
          <button type="button" className="reset" onClick={onReset}>
            <svg
              aria-hidden="true"
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              strokeWidth="2"
              strokeLinecap="round"
              strokeLinejoin="round"
            >
              <path d="M3 12a9 9 0 1 0 9-9 9 9 0 0 0-6.7 3L3 8M3 3v5h5" />
            </svg>
            Reset
          </button>
        )}
      </div>
    </div>
  );
}

// --- Strictness segment -------------------------------------------------
// Each option carries a `.tip-pop` card naming the trade-off, matching the
// other styled tooltips.
const STRICTNESS_OPTIONS: { id: Strictness; label: string; tipH: string; tip: string }[] = [
  {
    id: "loose",
    label: "Loose",
    tipH: "Loose match",
    tip: "Casts the widest net, surfacing reports only loosely related to your query. More results, but more off-topic noise.",
  },
  {
    id: "balanced",
    label: "Balanced",
    tipH: "Balanced match",
    tip: "The default. Trades breadth against precision, and suits most searches.",
  },
  {
    id: "strict",
    label: "Strict",
    tipH: "Strict match",
    tip: "Keeps only the closest matches. Fewer results and less noise, but it can drop reports that are still relevant.",
  },
];

function StrictSeg({ value, onChange }: { value: Strictness; onChange: (s: Strictness) => void }) {
  return (
    <div
      className="strict-seg"
      role="radiogroup"
      aria-label="Semantic match strictness"
      data-tour="strictness"
    >
      {STRICTNESS_OPTIONS.map((o, i) => {
        const sel = value === o.id;
        // The rightmost card flips to `.right` so it doesn't run off-screen.
        const last = i === STRICTNESS_OPTIONS.length - 1;
        return (
          <button
            key={o.id}
            type="button"
            // biome-ignore lint/a11y/useSemanticElements: a styled segmented control can't be a radio input while keeping the chip layout.
            role="radio"
            aria-checked={sel}
            // Keep the name as just the label; the tip-pop copy is decorative.
            aria-label={o.label}
            className={`has-tip${sel ? " on" : ""}`}
            onClick={() => onChange(o.id)}
          >
            {o.label}
            <span className={`tip-pop${last ? " right" : ""}`} role="tooltip">
              <span className="tip-h">{o.tipH}</span>
              <span className="tip-text">{o.tip}</span>
            </span>
          </button>
        );
      })}
    </div>
  );
}

// --- Facet pill ----------------------------------------------------------
function FacetPill({
  label,
  valueLabel,
  icon,
  renderPopover,
  align = "start",
  minWidth,
}: {
  label: string;
  valueLabel: string | null;
  icon?: ReactNode;
  renderPopover: (close: () => void) => ReactNode;
  align?: "start" | "end";
  minWidth?: number;
}) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    if (!open) return;
    const onDown = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setOpen(false);
    };
    document.addEventListener("mousedown", onDown);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onDown);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);

  const active = valueLabel !== null;
  return (
    <div ref={ref} style={{ position: "relative", display: "inline-block" }}>
      <button
        type="button"
        className={`facet${active ? " on" : ""}`}
        aria-expanded={open}
        aria-haspopup="menu"
        onClick={() => setOpen((v) => !v)}
      >
        {icon}
        <span className="lead">{label}</span>
        <b>{valueLabel ?? "Any"}</b>
        <svg
          aria-hidden="true"
          className="car"
          viewBox="0 0 24 24"
          fill="none"
          stroke="currentColor"
          strokeWidth="2.2"
          strokeLinecap="round"
          strokeLinejoin="round"
          style={{ transform: open ? "rotate(180deg)" : undefined, transition: "transform .15s" }}
        >
          <path d="m6 9 6 6 6-6" />
        </svg>
      </button>
      {open && (
        <div
          role="menu"
          className={`facet-pop${align === "end" ? " align-end" : ""}`}
          style={minWidth ? { minWidth } : undefined}
        >
          {renderPopover(() => setOpen(false))}
        </div>
      )}
    </div>
  );
}

function DamageList({
  value,
  onChange,
}: {
  value: AdminReportDamageClass | null;
  onChange: (d: AdminReportDamageClass | null) => void;
}) {
  const dot = (d: AdminReportDamageClass) =>
    d === "complete" ? "var(--complete)" : d === "partial" ? "var(--partial)" : "var(--minimal)";
  return (
    <div style={{ display: "flex", flexDirection: "column", minWidth: 200 }}>
      <button
        type="button"
        role="menuitemradio"
        aria-checked={value === null}
        onClick={() => onChange(null)}
        style={optionRowStyle(value === null)}
      >
        <span
          aria-hidden="true"
          style={{
            width: 14,
            display: "inline-flex",
            color: value === null ? "var(--c-blue-700)" : "transparent",
          }}
        >
          <CheckIcon />
        </span>
        <span>All damage</span>
      </button>
      {DAMAGE_CLASSES.map((d) => {
        const sel = value === d;
        return (
          <button
            key={d}
            type="button"
            role="menuitemradio"
            aria-checked={sel}
            onClick={() => onChange(d)}
            style={optionRowStyle(sel)}
          >
            <span
              aria-hidden="true"
              style={{
                width: 14,
                display: "inline-flex",
                color: sel ? "var(--c-blue-700)" : "transparent",
              }}
            >
              <CheckIcon />
            </span>
            <span
              style={{
                display: "inline-flex",
                alignItems: "center",
                gap: 7,
                textTransform: "capitalize",
              }}
            >
              <span style={{ width: 8, height: 8, borderRadius: 2, background: dot(d) }} />
              {d}
            </span>
          </button>
        );
      })}
    </div>
  );
}

// --- Crisis chooser ------------------------------------------------------
// One pill + dropdown; the status filter (All / Live / Inactive / Archived)
// lives inside the dropdown.
const PILL_CLASS: Record<AdminCrisis["status"], string> = {
  active: "live",
  inactive: "inactive",
  archived: "archived",
};

function crisisRegion(c: AdminCrisis): string {
  return c.countries.join(", ");
}

function CrisisChooser({
  crisisId,
  crises,
  visibleCrises,
  selectedCrisis,
  onCrisisChange,
  statusFilter,
  onStatusFilterChange,
  statusCounts,
}: {
  crisisId: string | null;
  crises: AdminCrisis[];
  visibleCrises: AdminCrisis[];
  selectedCrisis: AdminCrisis | null;
  onCrisisChange: (id: string) => void;
  statusFilter: CrisisStatusFilter;
  onStatusFilterChange: (s: CrisisStatusFilter) => void;
  statusCounts: Record<CrisisStatusFilter, number>;
}) {
  const [open, setOpen] = useState(false);
  // Name filter: the crisis list grows without bound. Reset when the menu closes.
  const [query, setQuery] = useState("");
  const ref = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    if (!open) {
      setQuery("");
      return;
    }
    const onDown = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setOpen(false);
    };
    document.addEventListener("mousedown", onDown);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onDown);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);

  const region = selectedCrisis ? crisisRegion(selectedCrisis) : "";

  const q = query.trim().toLowerCase();
  const shownCrises = useMemo(
    () => (q ? visibleCrises.filter((c) => c.name.toLowerCase().includes(q)) : visibleCrises),
    [visibleCrises, q],
  );

  return (
    <div className="crisis-wrap" ref={ref} data-tour="crisis">
      <button
        type="button"
        className="crisis"
        aria-haspopup="menu"
        aria-expanded={open}
        disabled={crises.length === 0}
        onClick={() => setOpen((v) => !v)}
      >
        <span className="ic">
          <CrisisGlyph type={selectedCrisis?.type} />
        </span>
        <span className="nm">
          <b>
            {selectedCrisis?.name ?? (crises.length === 0 ? "No crises yet" : "Choose a crisis")}
          </b>
          {region && <span className="reg">{region}</span>}
        </span>
        {selectedCrisis && (
          <span className={`pill ${PILL_CLASS[selectedCrisis.status]}`}>
            <span className="d" />
            {crisisPillText(selectedCrisis.status)}
          </span>
        )}
        <svg
          className="car"
          viewBox="0 0 24 24"
          fill="none"
          stroke="currentColor"
          strokeWidth="2"
          strokeLinecap="round"
          strokeLinejoin="round"
          aria-hidden="true"
        >
          <path d="m6 9 6 6 6-6" />
        </svg>
      </button>
      {open && (
        <div className="crisis-menu" role="menu" aria-label="Choose a crisis">
          <div className="cm-seg" role="radiogroup" aria-label="Filter crises by status">
            {CRISIS_STATUS_FILTERS.map((s) => {
              const sel = statusFilter === s.id;
              return (
                <button
                  key={s.id}
                  type="button"
                  // biome-ignore lint/a11y/useSemanticElements: a styled segmented control can't be an <input type="radio"> while keeping the label + count layout.
                  role="radio"
                  aria-checked={sel}
                  className={sel ? "on" : undefined}
                  onClick={() => onStatusFilterChange(s.id)}
                >
                  {s.label} <span className="num">{statusCounts[s.id]}</span>
                </button>
              );
            })}
          </div>
          <div className="cm-search">
            <svg
              aria-hidden="true"
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              strokeWidth="2"
              strokeLinecap="round"
              strokeLinejoin="round"
            >
              <circle cx="11" cy="11" r="7" />
              <path d="m21 21-4.3-4.3" />
            </svg>
            <input
              type="text"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder="Search crises by name…"
              aria-label="Search crises by name"
              // biome-ignore lint/a11y/noAutofocus: focusing the search on open is the expected affordance for a type-to-filter menu.
              autoFocus
            />
          </div>
          <div className="cm-list">
            {shownCrises.length === 0 ? (
              <div className="cm-empty">
                {q ? "No crises match your search." : "No crises match this status."}
              </div>
            ) : (
              shownCrises.map((c) => {
                const sel = c.id === crisisId;
                const typeLabel = c.type ? c.type.charAt(0).toUpperCase() + c.type.slice(1) : "";
                const meta = [crisisRegion(c), typeLabel].filter(Boolean).join(" · ");
                return (
                  <button
                    key={c.id}
                    type="button"
                    role="menuitemradio"
                    aria-checked={sel}
                    className={`cm-item${sel ? " sel" : ""}`}
                    onClick={() => {
                      onCrisisChange(c.id);
                      setOpen(false);
                    }}
                  >
                    <span className="ic">
                      <CrisisGlyph type={c.type} />
                    </span>
                    <span className="t">
                      <b>{c.name}</b>
                      {meta && <small>{meta}</small>}
                    </span>
                    <span className={`pill ${PILL_CLASS[c.status]}`}>
                      {c.status === "active" && <span className="d" />}
                      {crisisPillText(c.status)}
                    </span>
                    <svg
                      className="chk"
                      viewBox="0 0 24 24"
                      fill="none"
                      stroke="currentColor"
                      strokeWidth="3"
                      strokeLinecap="round"
                      strokeLinejoin="round"
                      aria-hidden="true"
                    >
                      <path d="M5 13l4 4L19 7" />
                    </svg>
                  </button>
                );
              })
            )}
          </div>
        </div>
      )}
    </div>
  );
}

// --- Center stage controls ----------------------------------------------
function ViewSeg({ view, onChange }: { view: ViewMode; onChange: (v: ViewMode) => void }) {
  return (
    <div className="view-toggle" role="tablist" aria-label="Center view" data-tour="view">
      <button
        type="button"
        role="tab"
        aria-selected={view === "map"}
        className={`vt${view === "map" ? " on" : ""}`}
        onClick={() => onChange("map")}
      >
        <svg
          aria-hidden="true"
          viewBox="0 0 24 24"
          fill="none"
          stroke="currentColor"
          strokeWidth="2"
          strokeLinecap="round"
          strokeLinejoin="round"
        >
          <path d="M9 5 3 7v12l6-2 6 2 6-2V5l-6 2-6-2Z" />
          <path d="M9 5v12M15 7v12" />
        </svg>
        Map
      </button>
      <button
        type="button"
        role="tab"
        aria-selected={view === "list"}
        className={`vt${view === "list" ? " on" : ""}`}
        onClick={() => onChange("list")}
      >
        <svg
          aria-hidden="true"
          viewBox="0 0 24 24"
          fill="none"
          stroke="currentColor"
          strokeWidth="2"
          strokeLinecap="round"
          strokeLinejoin="round"
        >
          <path d="M8 6h13M8 12h13M8 18h13M3 6h.01M3 12h.01M3 18h.01" />
        </svg>
        List
      </button>
    </div>
  );
}

const MAP_MODE_ICONS: Record<AdminMapDisplayMode, ReactNode> = {
  points: (
    <svg
      aria-hidden="true"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      strokeLinejoin="round"
    >
      <circle cx="7" cy="8" r="2" />
      <circle cx="16" cy="6" r="2" />
      <circle cx="12" cy="14" r="2" />
      <circle cx="18" cy="16" r="2" />
      <circle cx="6" cy="17" r="2" />
    </svg>
  ),
  heatmap: (
    <svg
      aria-hidden="true"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.7"
      strokeLinejoin="round"
    >
      <path d="M9 3l3 4 3-4M5 9l2.5 3.5L5 16M19 9l-2.5 3.5L19 16M9 21l3-4 3 4" />
      <circle cx="12" cy="12" r="3.2" />
    </svg>
  ),
  buildings: (
    <svg
      aria-hidden="true"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      strokeLinejoin="round"
    >
      <path d="M3 21h18M5 21V7l7-4 7 4v14" />
      <path d="M9 21v-5h6v5M9 8h.01M15 8h.01M9 12h.01M15 12h.01" />
    </svg>
  ),
  all: (
    <svg
      aria-hidden="true"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      strokeLinejoin="round"
    >
      <path d="m12 2 9 5-9 5-9-5 9-5Z" />
      <path d="m3 12 9 5 9-5M3 17l9 5 9-5" />
    </svg>
  ),
};

function MapModes({
  value,
  onChange,
}: {
  value: AdminMapDisplayMode;
  onChange: (m: AdminMapDisplayMode) => void;
}) {
  const opts: { id: AdminMapDisplayMode; label: string }[] = [
    { id: "points", label: "Points" },
    { id: "heatmap", label: "Heatmap" },
    { id: "buildings", label: "Buildings" },
    { id: "all", label: "All" },
  ];
  return (
    <div className="modes" role="tablist" aria-label="Map display mode" data-tour="map-modes">
      {opts.map((o) => (
        <button
          key={o.id}
          type="button"
          role="tab"
          aria-selected={value === o.id}
          className={value === o.id ? "on" : undefined}
          onClick={() => onChange(o.id)}
        >
          {MAP_MODE_ICONS[o.id]}
          {o.label}
        </button>
      ))}
    </div>
  );
}

// Area autocomplete for the stage-bar location search. A pick adds a location
// chip that frames the area and narrows the reports. Searches Overture + OSM
// divisions scoped to the crisis's countries.
function AreaSearchField({
  crisisCountries,
  onPick,
}: {
  crisisCountries: string[];
  onPick: (chip: LocationChip) => void;
}) {
  const searchLocations = useCallback(
    async (q: string, signal: AbortSignal): Promise<AreaSearchHit[]> => {
      if (!q.trim()) return [];
      return searchAreasForCountries(q, crisisCountries, signal, 8);
    },
    [crisisCountries],
  );
  return (
    <Autocomplete<AreaSearchHit>
      placeholder="Search a place or area…"
      ariaLabel="Search by location"
      search={searchLocations}
      onPick={(hit) => onPick(areaHitToChip(hit))}
      keyFor={(hit) => `area:${hit.id}`}
      renderItem={(hit) => (
        <div style={{ display: "flex", alignItems: "center", gap: 8, fontSize: 12 }}>
          <span
            style={{
              fontSize: 9,
              fontWeight: 700,
              textTransform: "uppercase",
              letterSpacing: 0.06,
              padding: "2px 6px",
              borderRadius: 999,
              background: "var(--c-blue-50)",
              color: "var(--c-blue-800, var(--c-blue-700))",
              border: "1px solid var(--c-blue-300, #c5dcf5)",
            }}
          >
            {hit.source === "osm" ? "OSM" : hit.subtype}
          </span>
          <span
            style={{
              fontWeight: 600,
              color: "var(--c-ink)",
              whiteSpace: "nowrap",
              overflow: "hidden",
              textOverflow: "ellipsis",
            }}
          >
            {hit.name || "(unnamed)"}
          </span>
          {hit.parents.length > 0 && (
            <span style={{ fontSize: 10, color: "var(--c-ink-3)" }}>
              · {hit.parents.join(", ")}
            </span>
          )}
        </div>
      )}
    />
  );
}

// Stage-bar location search pill. Right-aligned popover so it never spills
// past the map's right edge.
function MapAreaSearch({
  crisisCountries,
  onAddLocation,
}: {
  crisisCountries: string[];
  onAddLocation: (chip: LocationChip) => void;
}) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement | null>(null);
  useEffect(() => {
    if (!open) return;
    const onDown = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setOpen(false);
    };
    document.addEventListener("mousedown", onDown);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onDown);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);
  return (
    <div ref={ref} style={{ position: "relative", display: "inline-block" }}>
      <button
        type="button"
        className="facet"
        data-tour="location-search"
        aria-expanded={open}
        aria-haspopup="menu"
        onClick={() => setOpen((v) => !v)}
      >
        <svg
          className="ic"
          viewBox="0 0 24 24"
          fill="none"
          stroke="currentColor"
          strokeWidth="2"
          strokeLinecap="round"
          strokeLinejoin="round"
          aria-hidden="true"
        >
          <path d="M12 21s-7-5.7-7-11a7 7 0 0 1 14 0c0 5.3-7 11-7 11Z" />
          <circle cx="12" cy="10" r="2.4" />
        </svg>
        <span className="lead">Search area</span>
        <svg
          aria-hidden="true"
          className="car"
          viewBox="0 0 24 24"
          fill="none"
          stroke="currentColor"
          strokeWidth="2.2"
          strokeLinecap="round"
          strokeLinejoin="round"
          style={{ transform: open ? "rotate(180deg)" : undefined, transition: "transform .15s" }}
        >
          <path d="m6 9 6 6 6-6" />
        </svg>
      </button>
      {open && (
        <div role="menu" className="facet-pop align-end" style={{ minWidth: 280 }}>
          <AreaSearchField
            crisisCountries={crisisCountries}
            onPick={(chip) => {
              onAddLocation(chip);
              setOpen(false);
            }}
          />
        </div>
      )}
    </div>
  );
}

// Map-view-only notice: the viewport filter excludes position-less reports, so
// view counts sit below the whole-crisis "/ N" by exactly `count`
// (`baseline.unmapped`, the same figure as the Locations cell's "no location").
// List view drops the bbox, so there is no gap there.
function OffMapNotice({ count }: { count: number }) {
  const isOne = count === 1;
  return (
    <div className="offbar" role="note">
      <span className="ic">
        <svg
          viewBox="0 0 24 24"
          fill="none"
          stroke="currentColor"
          strokeWidth="2"
          strokeLinecap="round"
          strokeLinejoin="round"
          aria-hidden="true"
        >
          <circle cx="12" cy="12" r="9" />
          <path d="M12 11v5" />
          <path d="M12 7.5h.01" />
        </svg>
      </span>
      <span>
        <b className="num">{count.toLocaleString()}</b> {isOne ? "report has" : "reports have"} no
        location, so {isOne ? "it is" : "they are"} not shown on the map or counted in this view.{" "}
        <span className="more">{isOne ? "It is" : "They are"} still counted in Total reports.</span>
      </span>
    </div>
  );
}

// List-view counterpart to OffMapNotice: the list includes position-less
// reports, which explains why the count jumps versus map view.
function ListLocationNote({ count }: { count: number }) {
  const isOne = count === 1;
  return (
    <div className="offbar" role="note">
      <span className="ic">
        <svg
          viewBox="0 0 24 24"
          fill="none"
          stroke="currentColor"
          strokeWidth="2"
          strokeLinecap="round"
          strokeLinejoin="round"
          aria-hidden="true"
        >
          <circle cx="12" cy="12" r="9" />
          <path d="M12 11v5" />
          <path d="M12 7.5h.01" />
        </svg>
      </span>
      <span>
        Includes <b className="num">{count.toLocaleString()}</b>{" "}
        {isOne ? "report with" : "reports with"} no location that the map view leaves out, so this
        count runs higher than the map's.
      </span>
    </div>
  );
}

// Entry into the live analysis tab (deterministic layer computed in ~2-3s,
// versus the slower PDF below). Carries the selected crisis via the hash.
function LiveAnalysisEntry({ crisisId }: { crisisId: string }) {
  return (
    <a
      href={`#/analysis?crisis=${crisisId}`}
      style={{
        display: "flex",
        alignItems: "center",
        gap: 12,
        padding: "12px 13px",
        borderRadius: 10,
        background: "var(--c-blue-50)",
        border: "1px solid var(--c-blue-200)",
        textDecoration: "none",
        color: "inherit",
      }}
    >
      <span
        aria-hidden="true"
        style={{
          flexShrink: 0,
          width: 34,
          height: 34,
          borderRadius: 8,
          display: "grid",
          placeItems: "center",
          background: "var(--c-card)",
          color: "var(--c-blue-700)",
          border: "1px solid var(--c-blue-200)",
        }}
      >
        <svg
          viewBox="0 0 24 24"
          width="18"
          height="18"
          fill="none"
          stroke="currentColor"
          strokeWidth="2"
          strokeLinecap="round"
          strokeLinejoin="round"
          aria-hidden="true"
        >
          <path d="M3 6v13l6-2 6 2 6-2V4l-6 2-6-2-6 2Z" />
          <path d="M9 4v13M15 6v13" />
        </svg>
      </span>
      <span style={{ flex: 1, minWidth: 0 }}>
        <span
          style={{
            display: "flex",
            alignItems: "center",
            gap: 6,
            fontSize: 14,
            fontWeight: 600,
            color: "var(--c-blue-700)",
          }}
        >
          View live analysis
          <svg
            viewBox="0 0 24 24"
            width="14"
            height="14"
            fill="none"
            stroke="currentColor"
            strokeWidth="2.4"
            strokeLinecap="round"
            strokeLinejoin="round"
            aria-hidden="true"
          >
            <path d="M7 17 17 7M9 7h8v8" />
          </svg>
        </span>
        <span
          style={{
            display: "block",
            fontSize: 13,
            color: "var(--c-ink-2)",
            marginTop: 2,
            lineHeight: 1.4,
          }}
        >
          Priority areas, blind spots and an impact map across the whole crisis, computed live. Your
          dashboard filters and map view don't apply.
        </span>
      </span>
    </a>
  );
}

// --- Right AI panel ------------------------------------------------------
function InspectorPane({
  tab,
  onTabChange,
  selectedReportId,
  onCiteClick,
  onChatContextChange,
  onCiteFocus,
  crisisId,
  crisisName,
  searchPayload,
  searchReady,
  filterVersion,
  currentTotal,
  scopeTotal,
  exportFilter,
  crisisTotal,
  hasActiveFilters,
  onSummarised,
}: {
  tab: InspectorTab;
  onTabChange: (t: InspectorTab) => void;
  selectedReportId: string | null;
  onCiteClick: (reportId: string) => void;
  onChatContextChange: (reports: ChatContextReport[]) => void;
  onCiteFocus: (reportId: string) => void;
  crisisId: string | null;
  crisisName: string | null;
  searchPayload: ReturnType<typeof useAiSearch>["committedPayload"];
  searchReady: boolean;
  filterVersion: number;
  currentTotal: number | null;
  scopeTotal: number | null;
  // Current view payload (no viewport bbox), its matched count and the baseline
  // count, for the view-aware Export tab.
  exportFilter: SearchRequest;
  crisisTotal: number | null;
  hasActiveFilters: boolean;
  onSummarised: () => void;
}) {
  return (
    <div className="panel right" data-tour="inspector">
      <div className="ai-head">
        <div className="ttl-row">
          <span className="ttl">Advanced tools</span>
        </div>
      </div>

      <div className="seg" role="tablist" aria-label="Advanced tools">
        <button
          type="button"
          role="tab"
          data-tour="tool-summary"
          aria-selected={tab === "summary"}
          className={tab === "summary" ? "on" : undefined}
          onClick={() => onTabChange("summary")}
        >
          Summary
        </button>
        <button
          type="button"
          role="tab"
          data-tour="tool-chat"
          aria-selected={tab === "chat"}
          className={tab === "chat" ? "on" : undefined}
          onClick={() => onTabChange("chat")}
        >
          Chat
        </button>
        <button
          type="button"
          role="tab"
          data-tour="tool-analysis"
          aria-selected={tab === "snapshots"}
          className={tab === "snapshots" ? "on" : undefined}
          onClick={() => onTabChange("snapshots")}
        >
          Analysis
        </button>
        <button
          type="button"
          role="tab"
          data-tour="tool-export"
          aria-selected={tab === "export"}
          className={tab === "export" ? "on" : undefined}
          onClick={() => onTabChange("export")}
        >
          Export
        </button>
        <button
          type="button"
          role="tab"
          data-tour="tool-detail"
          aria-selected={tab === "detail"}
          className={tab === "detail" ? "on" : undefined}
          disabled={!selectedReportId}
          onClick={() => onTabChange("detail")}
        >
          Detail
        </button>
      </div>

      {/* Below the tab bar, only for Summary and Chat (the tabs that work over the
          in-view set), so toggling it never reflows the header or tabs. */}
      {(tab === "summary" || tab === "chat") && (
        <div className="scope-strip">
          <span className="d" aria-hidden="true" />
          Scope: <b className="num">{(scopeTotal ?? 0).toLocaleString()}</b> reports in your current
          view
        </div>
      )}

      <div style={{ flex: 1, minHeight: 0, overflow: "hidden", display: "flex" }}>
        {tab === "detail" && (
          <div style={{ flex: 1, minHeight: 0, overflowY: "auto" }}>
            <AdminReportInspector reportId={selectedReportId} />
          </div>
        )}
        {/* These tabs stay mounted while a crisis is active so switching preserves
            streamed frames, chat history, and the in-flight PDF and photo-bundle
            polls; remounting would reset and flash an idle state. */}
        {crisisId ? (
          <>
            <div
              style={{
                flex: 1,
                minHeight: 0,
                overflowY: "auto",
                padding: "16px 18px",
                display: tab === "snapshots" ? "block" : "none",
              }}
            >
              <LiveAnalysisEntry crisisId={crisisId} />
              <div className="label-eyebrow" style={{ marginTop: 18, marginBottom: 8 }}>
                PDF snapshot reports
              </div>
              <AnalysisReportsSection crisisId={crisisId} />
            </div>
            <div
              style={{
                flex: 1,
                minHeight: 0,
                overflowY: "auto",
                display: tab === "summary" ? "block" : "none",
              }}
            >
              <SummaryPanel
                crisisId={crisisId}
                filter={searchPayload}
                ready={searchReady}
                onCiteClick={onCiteClick}
                onSummarised={onSummarised}
              />
            </div>
            <div
              style={{
                flex: 1,
                minHeight: 0,
                display: tab === "chat" ? "flex" : "none",
                flexDirection: "column",
              }}
            >
              <ChatPanel
                crisisId={crisisId}
                filter={searchPayload}
                filterVersion={filterVersion}
                ready={searchReady}
                currentTotal={currentTotal}
                onContextChange={onChatContextChange}
                onCiteFocus={onCiteFocus}
              />
            </div>
            {/* `scrollbarGutter: stable` keeps the width fixed when the taller
                "Data + photos" panel brings in the scrollbar. */}
            <div
              style={{
                flex: 1,
                minHeight: 0,
                overflowY: "auto",
                scrollbarGutter: "stable",
                padding: "16px 18px",
                display: tab === "export" ? "block" : "none",
              }}
            >
              <CrisisExportSection
                crisisId={crisisId}
                crisisName={crisisName}
                filterPayload={exportFilter}
                viewTotal={scopeTotal}
                crisisTotal={crisisTotal}
                hasActiveFilters={hasActiveFilters}
              />
            </div>
          </>
        ) : (
          (tab === "snapshots" || tab === "summary" || tab === "chat" || tab === "export") && (
            <div style={{ flex: 1, minHeight: 0, overflowY: "auto" }}>
              <InspectorEmptyHint
                title={
                  tab === "snapshots"
                    ? "Pick a crisis to analyse"
                    : tab === "summary"
                      ? "Pick a crisis to summarise"
                      : tab === "export"
                        ? "Pick a crisis to export"
                        : "Pick a crisis to start chatting"
                }
                body={
                  tab === "snapshots"
                    ? "The live analysis and the point-in-time PDF snapshots both run over the selected crisis's reports."
                    : tab === "summary"
                      ? "The summary speaks only about the reports currently filtered on the map."
                      : tab === "export"
                        ? "Export the filtered view or the whole crisis, as data or a photo bundle."
                        : "The chat is scoped to whatever the filter currently selects on the map."
                }
              />
            </div>
          )
        )}
      </div>
    </div>
  );
}

function InspectorEmptyHint({ title, body }: { title: string; body: string }) {
  return (
    <div style={{ flex: 1, display: "grid", placeItems: "center", padding: 24 }}>
      <div style={{ maxWidth: 280, textAlign: "center" }}>
        <div style={{ fontSize: 14, fontWeight: 600, marginBottom: 6 }}>{title}</div>
        <div style={{ fontSize: 12, color: "var(--c-ink-3)", lineHeight: 1.5 }}>{body}</div>
      </div>
    </div>
  );
}

// --- Filter option data + helpers ---------------------------------------
interface TimeOption {
  id: TimeWindow;
  label: string;
  short: string;
}
interface DebrisOption {
  id: DebrisFilter;
  label: string;
  short: string;
}
interface LocationOption {
  id: LocationFilter;
  label: string;
  short: string;
}

const TIME_WINDOWS: readonly TimeOption[] = [
  { id: "any", label: "Any time", short: "any" },
  { id: "24h", label: "Last 24 hours", short: "24h" },
  { id: "7d", label: "Last 7 days", short: "7d" },
  { id: "30d", label: "Last 30 days", short: "30d" },
];
const DEBRIS_OPTS: readonly DebrisOption[] = [
  { id: "any", label: "Any debris", short: "any" },
  { id: "yes", label: "Debris present", short: "yes" },
  { id: "no", label: "No debris", short: "no" },
  { id: "unknown", label: "Unknown", short: "unknown" },
];
const LOCATION_OPTS: readonly LocationOption[] = [
  { id: "any", label: "All locations", short: "any" },
  { id: "exact", label: "Exact (GPS or building)", short: "exact" },
  { id: "ai_geocode", label: "AI-located (approximate)", short: "AI-located" },
];

function shortFor<T extends string>(
  opts: readonly { id: T; short: string }[],
  id: T,
): string | null {
  const m = opts.find((o) => o.id === id);
  return m && m.id !== "any" ? m.short : null;
}

// Echo the picked date verbatim (UTC, so the label matches what was typed in
// any display timezone). `null` for an empty or unparseable value.
function fmtDateInput(d: string | null): string | null {
  if (!d) return null;
  const t = new Date(`${d}T00:00:00Z`);
  if (Number.isNaN(t.getTime())) return null;
  return t.toLocaleDateString(undefined, {
    day: "numeric",
    month: "short",
    year: "numeric",
    timeZone: "UTC",
  });
}

/** Time facet value text. `null` collapses the pill to "Any". */
function timeFacetLabel(f: ReportFilters): string | null {
  if (f.timeWindow === "custom") {
    const from = fmtDateInput(f.customFrom);
    const to = fmtDateInput(f.customTo);
    if (from && to) return `${from} to ${to}`;
    if (from) return `from ${from}`;
    if (to) return `until ${to}`;
    return null;
  }
  return shortFor(TIME_WINDOWS, f.timeWindow);
}

function activeStructuredCount(f: ReportFilters): number {
  let n = 0;
  if (timeFilterActive(f)) n++;
  if (f.debris !== "any") n++;
  if (f.location !== "any") n++;
  if (f.infraTypes.length > 0) n++;
  return n;
}

function SingleSelectList<T extends string>({
  options,
  value,
  onChange,
}: {
  options: readonly { id: T; label: string }[];
  value: T;
  onChange: (v: T) => void;
}) {
  return (
    <div style={{ display: "flex", flexDirection: "column", minWidth: 200 }}>
      {options.map((o) => {
        const sel = value === o.id;
        return (
          <button
            key={o.id}
            type="button"
            role="menuitemradio"
            aria-checked={sel}
            onClick={() => onChange(o.id)}
            style={optionRowStyle(sel)}
          >
            <span
              aria-hidden="true"
              style={{
                width: 14,
                display: "inline-flex",
                color: sel ? "var(--c-blue-700)" : "transparent",
              }}
            >
              <CheckIcon />
            </span>
            <span>{o.label}</span>
          </button>
        );
      })}
    </div>
  );
}

// Time facet popover: relative windows, then a custom range with two native
// date pickers. Editing a date switches to `custom` and applies live (the
// popover stays open); a relative row clears the custom bounds and closes. Needed
// for crises whose reports predate the longest relative window.
function TimeFilterList({
  filters,
  onSelectRelative,
  onCustomChange,
}: {
  filters: ReportFilters;
  onSelectRelative: (v: TimeWindow) => void;
  onCustomChange: (from: string | null, to: string | null) => void;
}) {
  const isCustom = filters.timeWindow === "custom";
  return (
    <div style={{ display: "flex", flexDirection: "column", minWidth: 240 }}>
      {TIME_WINDOWS.map((o) => {
        const sel = !isCustom && filters.timeWindow === o.id;
        return (
          <button
            key={o.id}
            type="button"
            role="menuitemradio"
            aria-checked={sel}
            onClick={() => onSelectRelative(o.id)}
            style={optionRowStyle(sel)}
          >
            <span
              aria-hidden="true"
              style={{
                width: 14,
                display: "inline-flex",
                color: sel ? "var(--c-blue-700)" : "transparent",
              }}
            >
              <CheckIcon />
            </span>
            <span>{o.label}</span>
          </button>
        );
      })}
      <div style={{ borderTop: "1px solid var(--c-line)", marginTop: 4, paddingTop: 8 }}>
        <div
          style={{
            display: "flex",
            alignItems: "center",
            gap: 8,
            padding: "0 10px 6px",
            fontSize: 11,
            fontWeight: 600,
            letterSpacing: ".04em",
            textTransform: "uppercase",
            color: isCustom ? "var(--c-blue-700)" : "var(--c-ink-3)",
          }}
        >
          <span
            aria-hidden="true"
            style={{
              width: 14,
              display: "inline-flex",
              color: isCustom ? "var(--c-blue-700)" : "transparent",
            }}
          >
            <CheckIcon />
          </span>
          Custom range
        </div>
        <div
          style={{
            display: "flex",
            flexDirection: "column",
            gap: 8,
            padding: "0 10px 2px 32px",
          }}
        >
          <DateField
            label="From"
            value={filters.customFrom ?? ""}
            max={filters.customTo ?? undefined}
            onChange={(v) => onCustomChange(v || null, filters.customTo)}
          />
          <DateField
            label="To"
            value={filters.customTo ?? ""}
            min={filters.customFrom ?? undefined}
            onChange={(v) => onCustomChange(filters.customFrom, v || null)}
          />
          {isCustom && (filters.customFrom !== null || filters.customTo !== null) && (
            <button
              type="button"
              onClick={() => onCustomChange(null, null)}
              style={{
                ...optionRowStyle(false),
                width: "auto",
                alignSelf: "flex-start",
                padding: "2px 0",
                color: "var(--c-blue-700)",
                fontWeight: 600,
              }}
            >
              Clear dates
            </button>
          )}
        </div>
      </div>
    </div>
  );
}

// A date field where the whole row is the hit target and a re-click closes the
// picker. There is `showPicker()` but no `hidePicker()`, so open state is tracked
// here and dismissal blurs; preventDefault keeps native click-to-open from
// fighting the toggle.
function DateField({
  label,
  value,
  min,
  max,
  onChange,
}: {
  label: string;
  value: string;
  min?: string;
  max?: string;
  onChange: (v: string) => void;
}) {
  const ref = useRef<HTMLInputElement | null>(null);
  const openRef = useRef(false);
  const open = () => {
    const el = ref.current;
    if (!el) return;
    el.focus();
    try {
      el.showPicker();
    } catch {
      /* older browser: focus alone is the best we can do */
    }
    openRef.current = true;
  };
  const close = () => {
    openRef.current = false;
    ref.current?.blur();
  };
  const toggle = () => (openRef.current ? close() : open());
  return (
    <div
      style={dateFieldStyle}
      // Stop native focus/auto-open so the toggle decides open vs close.
      onMouseDown={(e) => {
        e.preventDefault();
        toggle();
      }}
      onKeyDown={(e) => {
        if (e.key === "Enter" || e.key === " ") {
          e.preventDefault();
          toggle();
        }
      }}
      // biome-ignore lint/a11y/useSemanticElements: a <button> can't wrap the date <input> (interactive-in-interactive); this row is the picker's hit target.
      role="button"
      tabIndex={0}
      aria-label={`${label} date`}
    >
      <span style={dateLabelStyle}>{label}</span>
      <input
        ref={ref}
        type="date"
        value={value}
        min={min}
        max={max}
        // Every dismiss path must drop the open flag: change, blur, and Escape (which
        // closes the picker but keeps focus, so neither change nor blur fires).
        onChange={(e) => {
          openRef.current = false;
          onChange(e.target.value);
        }}
        onBlur={() => {
          openRef.current = false;
        }}
        onKeyDown={(e) => {
          if (e.key === "Escape") openRef.current = false;
        }}
        style={dateInputStyle}
        tabIndex={-1}
      />
    </div>
  );
}

const dateFieldStyle: React.CSSProperties = {
  display: "flex",
  alignItems: "center",
  gap: 8,
  fontSize: 12,
  color: "var(--c-ink-2)",
  cursor: "pointer",
  border: "1px solid var(--c-line)",
  borderRadius: 6,
  padding: "5px 8px",
  background: "var(--c-card)",
};
const dateLabelStyle: React.CSSProperties = {
  width: 34,
  flexShrink: 0,
  cursor: "pointer",
};
const dateInputStyle: React.CSSProperties = {
  flex: 1,
  fontFamily: "inherit",
  fontSize: 12,
  color: "var(--c-ink)",
  background: "transparent",
  border: "none",
  padding: 0,
  cursor: "pointer",
  outline: "none",
};

function InfraTypeList({
  options,
  values,
  onToggle,
  onClear,
}: {
  options: string[];
  values: string[];
  onToggle: (t: string) => void;
  onClear?: () => void;
}) {
  if (options.length === 0) {
    return (
      <div
        style={{
          padding: "10px 12px",
          fontSize: 11,
          color: "var(--c-ink-3)",
          minWidth: 220,
          lineHeight: 1.5,
        }}
      >
        No infrastructure types in the loaded sample yet. Pan the map or load more to see values
        here.
      </div>
    );
  }
  return (
    <div style={{ display: "flex", flexDirection: "column", minWidth: 220 }}>
      <div style={{ maxHeight: 260, overflowY: "auto" }}>
        {options.map((t) => {
          const sel = values.includes(t);
          return (
            <button
              key={t}
              type="button"
              role="menuitemcheckbox"
              aria-checked={sel}
              onClick={() => onToggle(t)}
              style={optionRowStyle(sel)}
            >
              <span aria-hidden="true" style={{ width: 14, display: "inline-flex" }}>
                <CheckboxBox checked={sel} />
              </span>
              <span style={{ textTransform: "capitalize" }}>{t.replace(/_/g, " ")}</span>
            </button>
          );
        })}
      </div>
      {values.length > 0 && onClear && (
        <div style={{ borderTop: "1px solid var(--c-line)", padding: 4 }}>
          <button
            type="button"
            onClick={onClear}
            style={{ ...optionRowStyle(false), color: "var(--c-blue-700)", fontWeight: 600 }}
          >
            <span style={{ width: 14, display: "inline-block" }} />
            <span>Clear {values.length} selected</span>
          </button>
        </div>
      )}
    </div>
  );
}

function optionRowStyle(selected: boolean): React.CSSProperties {
  return {
    display: "flex",
    alignItems: "center",
    gap: 8,
    width: "100%",
    padding: "6px 10px",
    fontSize: 12,
    fontFamily: "inherit",
    color: "var(--c-ink)",
    background: selected ? "var(--c-blue-50)" : "transparent",
    border: "none",
    borderRadius: 6,
    textAlign: "start",
    cursor: "pointer",
    fontWeight: selected ? 600 : 500,
  };
}

function CheckIcon() {
  return (
    <svg width="12" height="12" viewBox="0 0 14 14" aria-hidden="true">
      <path
        d="M3 7.5l3 3 5-7"
        stroke="currentColor"
        strokeWidth="1.8"
        fill="none"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  );
}

function CheckboxBox({ checked }: { checked: boolean }) {
  return (
    <span
      style={{
        width: 13,
        height: 13,
        borderRadius: 3,
        border: `1.5px solid ${checked ? "var(--c-blue-700)" : "var(--c-line)"}`,
        background: checked ? "var(--c-blue-700)" : "transparent",
        display: "inline-flex",
        alignItems: "center",
        justifyContent: "center",
        color: "#fff",
        flexShrink: 0,
      }}
    >
      {checked && (
        <svg width="9" height="9" viewBox="0 0 14 14" aria-hidden="true">
          <path
            d="M3 7.5l3 3 5-7"
            stroke="currentColor"
            strokeWidth="2.2"
            fill="none"
            strokeLinecap="round"
            strokeLinejoin="round"
          />
        </svg>
      )}
    </span>
  );
}

function EmptyState({
  title,
  hint,
  tone = "neutral",
}: {
  title: string;
  hint?: string;
  tone?: "neutral" | "danger";
}) {
  const iconBg = tone === "danger" ? "var(--c-danger-bg)" : "var(--c-blue-100)";
  const iconColor = tone === "danger" ? "var(--c-danger)" : "var(--c-blue-700)";
  return (
    <div
      style={{
        display: "grid",
        placeItems: "center",
        height: "100%",
        background: "var(--c-card)",
        padding: 32,
        textAlign: "center",
      }}
    >
      <div
        style={{
          display: "flex",
          flexDirection: "column",
          alignItems: "center",
          gap: 10,
          maxWidth: 360,
        }}
      >
        <span
          aria-hidden="true"
          style={{
            width: 44,
            height: 44,
            borderRadius: 999,
            background: iconBg,
            color: iconColor,
            display: "grid",
            placeItems: "center",
          }}
        >
          <Icon.pin width="22" height="22" />
        </span>
        <div style={{ fontSize: 15, fontWeight: 700, color: "var(--c-ink)" }}>{title}</div>
        {hint && (
          <div style={{ fontSize: 13, color: "var(--c-ink-3)", lineHeight: 1.5 }}>{hint}</div>
        )}
      </div>
    </div>
  );
}

// --- List view -----------------------------------------------------------

// The fields the list table renders. Both `AdminReportListItem` and a search
// `SearchHit` satisfy it.
type ListRow = Pick<
  AdminReportListItem,
  | "id"
  | "damage_class"
  | "infra_name"
  | "infra_type"
  | "created_at"
  | "route_description"
  | "description"
>;

interface ListProps {
  crisisId: string;
  damageClass: AdminReportDamageClass | null;
  filters: ReportFilters;
  selectedReportId: string | null;
  onSelect: (reportId: string) => void;
  onDataChange: (next: {
    items: ListRow[];
    totalInBbox: number | null;
    truncated: boolean;
    loading: boolean;
  }) => void;
  // With a search overlay the matched set spans the whole crisis and rarely
  // overlaps the recency page, so the list is fed from the server-matched rows.
  searchHits: SearchHit[] | null;
  searchLoading: boolean;
  searchOverlayIds: Set<string> | null;
  anomalyIds: Set<string>;
  anomaliesOnly: boolean;
  totalMatchCount: number | null;
}

function ReportsList({
  crisisId,
  damageClass,
  filters,
  selectedReportId,
  onSelect,
  onDataChange,
  searchHits,
  searchLoading,
  searchOverlayIds,
  anomalyIds,
  anomaliesOnly,
  totalMatchCount,
}: ListProps) {
  const tz = useAdminTimezone();
  const [rows, setRows] = useState<AdminReportListItem[]>([]);
  const [nextCursor, setNextCursor] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const abortRef = useRef<AbortController | null>(null);
  const onDataChangeRef = useRef(onDataChange);
  onDataChangeRef.current = onDataChange;

  // Feed the table from the server-matched rows (intersecting the recency page
  // with the ID set comes up empty). Only the client-only anomaly overlay still applies.
  const searchActive = searchOverlayIds != null;
  const busy = searchActive ? searchLoading : loading;

  const displayRows = useMemo<ListRow[]>(() => {
    if (searchActive) {
      let next: ListRow[] = (searchHits ?? []).map((h) => ({
        id: h.id,
        damage_class: h.damage_class,
        infra_name: h.infra_name,
        infra_type: h.infra_type,
        created_at: h.created_at,
        route_description: h.route_description,
        description: h.description,
      }));
      if (anomaliesOnly) next = next.filter((r) => anomalyIds.has(r.id));
      // Search rows arrive by relevance; re-sort to honor "most recent first".
      return [...next].sort((a, b) => (a.created_at < b.created_at ? 1 : -1));
    }
    let next: ListRow[] = applyClientFilters(rows, filters);
    if (anomaliesOnly) next = next.filter((r) => anomalyIds.has(r.id));
    return next;
  }, [searchActive, searchHits, rows, filters, anomaliesOnly, anomalyIds]);

  useEffect(() => {
    onDataChangeRef.current({
      items: displayRows,
      totalInBbox: null,
      truncated: false,
      loading: false,
    });
  }, [displayRows]);

  useEffect(() => {
    abortRef.current?.abort();
    const ctrl = new AbortController();
    abortRef.current = ctrl;
    setLoading(true);
    setError(null);
    setRows([]);
    setNextCursor(null);
    onDataChangeRef.current({ items: [], totalInBbox: null, truncated: false, loading: true });
    listAdminReports(crisisId, { damageClass, limit: LIST_PAGE_SIZE, signal: ctrl.signal })
      .then((resp) => {
        setRows(resp.items);
        setNextCursor(resp.next_cursor);
        setLoading(false);
      })
      .catch((err) => {
        if (ctrl.signal.aborted) return;
        setError(err instanceof Error ? err.message : String(err));
        setLoading(false);
        onDataChangeRef.current({ items: [], totalInBbox: null, truncated: false, loading: false });
      });
    return () => ctrl.abort();
  }, [crisisId, damageClass]);

  const loadMore = useCallback(() => {
    if (!nextCursor || loading) return;
    const ctrl = new AbortController();
    abortRef.current = ctrl;
    setLoading(true);
    setError(null);
    listAdminReports(crisisId, {
      cursor: nextCursor,
      damageClass,
      limit: LIST_PAGE_SIZE,
      signal: ctrl.signal,
    })
      .then((resp) => {
        setRows((prev) => [...prev, ...resp.items]);
        setNextCursor(resp.next_cursor);
        setLoading(false);
      })
      .catch((err) => {
        if (ctrl.signal.aborted) return;
        setError(err instanceof Error ? err.message : String(err));
        setLoading(false);
      });
  }, [crisisId, damageClass, nextCursor, loading]);

  const total = totalMatchCount ?? displayRows.length;

  return (
    <div className="listview">
      <div className="lv-bar">
        <div className="lv-count">
          <b className="num">{total.toLocaleString()}</b> report{total === 1 ? "" : "s"} in view
        </div>
        <div className="lv-sortstate">
          Sorted by <b>recency</b>, most recent first
        </div>
      </div>

      <div className="lv-scroll">
        {error && (
          <div
            style={{
              padding: "12px 14px",
              fontSize: 12,
              color: "var(--c-danger)",
              background: "var(--c-danger-bg)",
            }}
          >
            {error}
          </div>
        )}

        {!error && !busy && displayRows.length === 0 && (
          <div
            style={{
              padding: "40px 20px",
              display: "flex",
              flexDirection: "column",
              alignItems: "center",
              gap: 8,
              textAlign: "center",
            }}
          >
            <span
              aria-hidden="true"
              style={{
                width: 36,
                height: 36,
                borderRadius: 999,
                background: "var(--c-blue-100)",
                color: "var(--c-blue-700)",
                display: "grid",
                placeItems: "center",
              }}
            >
              <Icon.search width="18" height="18" />
            </span>
            <div style={{ fontSize: 13, fontWeight: 600, color: "var(--c-ink)" }}>
              {!searchActive && rows.length === 0
                ? "No reports yet for this crisis"
                : "No reports match the current filters"}
            </div>
            <div style={{ fontSize: 12, color: "var(--c-ink-3)" }}>
              {!searchActive && rows.length === 0
                ? "Reports will appear here as citizens submit them."
                : "Try widening the time window, loosening the search, or clearing some filter chips."}
            </div>
          </div>
        )}

        {(displayRows.length > 0 || busy) && (
          <table className="lv-table">
            <thead>
              <tr>
                <th className="cw-id">ID</th>
                <th className="cw-dmg">Damage</th>
                <th className="cw-bld">Building</th>
                <th className="cw-type">Type</th>
                <th className="cw-when">
                  Reported{" "}
                  <span style={{ fontWeight: 400, color: "var(--c-ink-3)", textTransform: "none" }}>
                    ({shortZoneLabel(tz)})
                  </span>
                </th>
                <th className="cw-loc">Location description</th>
                <th>Description</th>
              </tr>
            </thead>
            <tbody>
              {displayRows.map((r) => {
                const sel = r.id === selectedReportId;
                return (
                  <tr
                    key={r.id}
                    className={`sev-${r.damage_class}${sel ? " sel" : ""}`}
                    tabIndex={0}
                    onClick={() => onSelect(r.id)}
                    onKeyDown={(e) => {
                      if (e.key === "Enter" || e.key === " ") {
                        e.preventDefault();
                        onSelect(r.id);
                      }
                    }}
                  >
                    <td className="cw-id">
                      <span className="lv-id" title={r.id}>
                        {r.id.slice(0, 8)}
                      </span>
                    </td>
                    <td className="cw-dmg">
                      <span className={`ri-pill ${r.damage_class}`}>{r.damage_class}</span>
                    </td>
                    <td className="cw-bld">
                      <ClampCell text={r.infra_name} lines={2} />
                    </td>
                    <td className="cw-type">
                      <ClampCell text={fmtInfraType(r.infra_type)} lines={2} capitalize />
                    </td>
                    <td className="cw-when">
                      <span className="lv-when" title={formatFull(r.created_at, tz)}>
                        {formatCompact(r.created_at, tz)}
                      </span>
                    </td>
                    <td className="cw-loc">
                      <ClampCell text={r.route_description} lines={3} />
                    </td>
                    <td>
                      {r.description ? (
                        <ClampCell text={r.description} lines={3} />
                      ) : (
                        <span className="lv-empty">No description</span>
                      )}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        )}
      </div>

      <div className="lv-foot">
        {busy && displayRows.length === 0 ? (
          "Loading reports…"
        ) : (
          <>
            Showing <span className="num">{displayRows.length}</span> of{" "}
            <span className="num">{total.toLocaleString()}</span> in view
            {!searchActive && nextCursor && (
              <>
                {" · "}
                <button
                  type="button"
                  onClick={loadMore}
                  disabled={loading}
                  style={{
                    color: "var(--c-blue-700)",
                    fontWeight: 600,
                    cursor: loading ? "default" : "pointer",
                    background: "none",
                    border: "none",
                    font: "inherit",
                  }}
                >
                  {loading ? "Loading…" : "Load more"}
                </button>
              </>
            )}
          </>
        )}
      </div>
    </div>
  );
}
