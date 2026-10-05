/**
 * Dashboard search state: semantic query, strictness, location chips and the
 * resulting `SearchResponse` from `/admin/reports/search/{crisis_id}`.
 *
 * Query and location chips are filters, not gates: they sit alongside damage
 * class, time, infra type, data quality and the viewport bbox, and the result
 * set is the single source of truth for Summary/Chat readiness and the stats.
 *
 * Location chips supersede the viewport bbox. `filterVersion` bumps on every
 * committed payload change except bbox drift (pan/zoom is cosmetic for chat
 * scope; chat detects bbox drift via the server's `filter_signature`).
 */
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { fetchAreaGeometry, searchAreas } from "../../../api/admin";
import {
  type SearchRequest,
  type SearchResponse,
  type SearchStats,
  type Strictness,
  searchReports,
} from "../../../api/search";
import { readWorkspace, writeWorkspace } from "../../../lib/adminWorkspace";
import { type ReportFilters, resolveTimeRange } from "../../../lib/reportFilters";
import type {
  AdminReportDamageClass,
  AreaSearchHit,
  GeoJSONMultiPolygon,
  GeoJSONPolygon,
} from "../../../types/admin";

/** GeoJSON Polygon / MultiPolygon for a selected area, keyed by chip id. */
export type LocationPolygon = {
  id: string;
  name: string;
  geometry: GeoJSONPolygon | GeoJSONMultiPolygon;
};

/** Most recently picked chip; the map flies to it. `seq` makes a re-pick of the same area re-fly. */
export interface FocusedLocation {
  seq: number;
  chip: LocationChip;
  polygon: LocationPolygon | null;
}

/** An administrative area (Overture division or OSM polygon) or a drawn polygon.
 *  Overture areas filter via `division_ids` and fetch their outline lazily;
 *  OSM and drawn shapes carry the polygon inline and filter via `polygon`. */
export type LocationChip =
  | {
      kind: "area";
      source: "overture";
      id: string;
      name: string;
      centroid: { lat: number; lng: number };
    }
  | {
      kind: "area";
      source: "osm";
      id: string;
      name: string;
      centroid: { lat: number; lng: number };
      polygon: GeoJSONPolygon | GeoJSONMultiPolygon;
    }
  | {
      kind: "polygon";
      id: string;
      name: string;
      centroid: { lat: number; lng: number };
      polygon: GeoJSONPolygon;
    };

function bboxCentroid(bbox: [number, number, number, number]): { lat: number; lng: number } {
  const [w, s, e, n] = bbox;
  return { lat: (s + n) / 2, lng: (w + e) / 2 };
}

export function areaHitToChip(h: AreaSearchHit): LocationChip {
  const centroid = bboxCentroid(h.bbox);
  if (h.source === "osm" && h.geometry) {
    return {
      kind: "area",
      source: "osm",
      id: h.id,
      name: h.name,
      centroid,
      polygon: h.geometry,
    };
  }
  return {
    kind: "area",
    source: "overture",
    id: h.id,
    name: h.name,
    centroid,
  };
}

export interface AiSearchState {
  query: string;
  strictness: Strictness;
  locations: LocationChip[];
  anomaliesOnly: boolean;
}

const DEFAULT_AI: AiSearchState = {
  query: "",
  strictness: "balanced",
  locations: [],
  anomaliesOnly: false,
};

// Persisted per tab. Location chips are excluded: they are bound to one crisis's geography.
const AI_WORKSPACE_KEY = "ai";

interface AiWorkspace {
  query: string;
  strictness: Strictness;
  anomaliesOnly: boolean;
}

function seedAiState(): AiSearchState {
  const saved = readWorkspace<AiWorkspace | null>(AI_WORKSPACE_KEY, null);
  if (!saved) return DEFAULT_AI;
  return {
    ...DEFAULT_AI,
    query: typeof saved.query === "string" ? saved.query : "",
    strictness: saved.strictness ?? "balanced",
    anomaliesOnly: saved.anomaliesOnly ?? false,
  };
}

// Unfiltered search feeding the KPI ribbon's "of N" crisis totals. `limit: 1`
// keeps rows tiny; stats are computed over the full matched set regardless.
const WHOLE_CRISIS_PAYLOAD: SearchRequest = {
  strictness: "balanced",
  damage_class: "any",
  debris: "any",
  location_kind: "any",
  limit: 1,
};

// No realtime push: the baseline is fetched on crisis load, then refreshed at most
// this often, piggybacked on committed view searches, so it stays consistent
// with the filtered view without an unfiltered query per pan.
const BASELINE_TTL_MS = 20_000;

/** Pure, so Summary/Chat can mirror exactly what the map shows. Non-empty
 *  `ai.locations` drop the viewport `bbox`: an explicit choice trumps an
 *  incidental viewport. */
export function buildSearchPayload(
  ai: AiSearchState,
  damageClass: AdminReportDamageClass | null,
  filters: ReportFilters,
  bbox: [number, number, number, number] | null,
): SearchRequest {
  // The server doesn't accept the client-only "unknown" debris value; don't narrow on it.
  const serverDebris: SearchRequest["debris"] =
    filters.debris === "yes" || filters.debris === "no" || filters.debris === "any"
      ? filters.debris
      : "any";
  const payload: SearchRequest = {
    strictness: ai.strictness,
    damage_class: damageClass ?? "any",
    debris: serverDebris,
    location_kind: filters.location,
    limit: 2000,
  };
  const q = ai.query.trim();
  if (q) payload.query = q;
  if (filters.infraTypes.length > 0) payload.infra_types = filters.infraTypes;
  const { fromMs, toMs } = resolveTimeRange(filters, Date.now());
  if (fromMs !== null || toMs !== null) {
    payload.time_window = {
      from: fromMs !== null ? new Date(fromMs).toISOString() : null,
      to: toMs !== null ? new Date(toMs).toISOString() : null,
    };
  }

  const overtureDivisionIds: string[] = [];
  const polygonPieces: Array<GeoJSONPolygon | GeoJSONMultiPolygon> = [];
  for (const loc of ai.locations) {
    if (loc.kind === "area" && loc.source === "overture") {
      overtureDivisionIds.push(loc.id);
    } else if (loc.kind === "area" && loc.source === "osm") {
      polygonPieces.push(loc.polygon);
    } else if (loc.kind === "polygon") {
      polygonPieces.push(loc.polygon);
    }
  }
  if (overtureDivisionIds.length > 0 || polygonPieces.length > 0) {
    payload.location = {};
    if (overtureDivisionIds.length > 0) payload.location.division_ids = overtureDivisionIds;
    // `LocationFilter.polygon` is single-valued, so several chips go as one MultiPolygon.
    if (polygonPieces.length === 1) {
      payload.location.polygon = polygonPieces[0] as unknown as object;
    } else if (polygonPieces.length > 1) {
      const multi: GeoJSONMultiPolygon = {
        type: "MultiPolygon",
        coordinates: polygonPieces.flatMap((g) =>
          g.type === "Polygon" ? [g.coordinates] : g.coordinates,
        ),
      };
      payload.location.polygon = multi as unknown as object;
    }
  } else if (bbox) {
    payload.location = { bbox };
  }
  return payload;
}

interface UseAiSearchArgs {
  crisisId: string | null;
  damageClass: AdminReportDamageClass | null;
  filters: ReportFilters;
  // Viewport `[w, s, e, n]`; superseded by location chips. Null while mounting or in list view.
  bbox?: [number, number, number, number] | null;
  // Auto-fire debounce, ms.
  debounceMs?: number;
}

export function useAiSearch({
  crisisId,
  damageClass,
  filters,
  bbox = null,
  debounceMs = 350,
}: UseAiSearchArgs) {
  const [state, setState] = useState<AiSearchState>(seedAiState);
  const [response, setResponse] = useState<SearchResponse | null>(null);
  // Only moves forward when a fresh response commits, never through null mid-flight,
  // so the toolbar count doesn't flicker. Reset to null only on crisis change.
  const [displayResponse, setDisplayResponse] = useState<SearchResponse | null>(null);
  // See `WHOLE_CRISIS_PAYLOAD` / `BASELINE_TTL_MS`.
  const [baselineStats, setBaselineStats] = useState<SearchStats | null>(null);
  const baselineAbortRef = useRef<AbortController | null>(null);
  const baselineFetchedAtRef = useRef(0);
  const crisisIdRef = useRef<string | null>(crisisId);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // Bumped on every committed response so the Chat panel can detect
  // "filter changed since open" and invalidate its session.
  const [filterVersion, setFilterVersion] = useState(0);
  // Keyed by chip id.
  const [polygons, setPolygons] = useState<Record<string, LocationPolygon>>({});
  const [focused, setFocused] = useState<FocusedLocation | null>(null);
  const focusSeqRef = useRef(0);
  const abortRef = useRef<AbortController | null>(null);
  const debounceRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  // Only the latest request commits, in case an older fetch resolves last.
  const requestIdRef = useRef(0);

  // Reset the crisis-bound state on crisis change; query, strictness and anomaly toggle carry over.
  // biome-ignore lint/correctness/useExhaustiveDependencies: crisisId is the trigger; the body reads only refs + setters
  useEffect(() => {
    setState((s) => (s.locations.length === 0 ? s : { ...s, locations: [] }));
    setResponse(null);
    setDisplayResponse(null);
    setError(null);
    setLoading(false);
    setPolygons({});
    setFocused(null);
    abortRef.current?.abort();
    // Force a filterVersion bump so a chat session on the old crisis reads as stale.
    lastNonBboxSignatureRef.current = null;
  }, [crisisId]);

  useEffect(() => {
    writeWorkspace<AiWorkspace>(AI_WORKSPACE_KEY, {
      query: state.query,
      strictness: state.strictness,
      anomaliesOnly: state.anomaliesOnly,
    });
  }, [state.query, state.strictness, state.anomaliesOnly]);

  // `force` bypasses the BASELINE_TTL_MS throttle. Dropped if the crisis changed mid-flight.
  const loadBaseline = useCallback((cid: string, force: boolean) => {
    if (!force && Date.now() - baselineFetchedAtRef.current < BASELINE_TTL_MS) return;
    baselineFetchedAtRef.current = Date.now();
    baselineAbortRef.current?.abort();
    const ctrl = new AbortController();
    baselineAbortRef.current = ctrl;
    searchReports(cid, WHOLE_CRISIS_PAYLOAD, ctrl.signal)
      .then((resp) => {
        if (ctrl.signal.aborted || cid !== crisisIdRef.current) return;
        if (resp.stats) setBaselineStats(resp.stats);
      })
      .catch(() => {
        // Allow a retry on the next commit if this baseline fetch failed.
        if (!ctrl.signal.aborted && cid === crisisIdRef.current) baselineFetchedAtRef.current = 0;
      });
  }, []);

  useEffect(() => {
    crisisIdRef.current = crisisId;
    setBaselineStats(null);
    baselineFetchedAtRef.current = 0;
    baselineAbortRef.current?.abort();
    if (crisisId) loadBaseline(crisisId, true);
  }, [crisisId, loadBaseline]);

  const committedPayload = useMemo(
    () => buildSearchPayload(state, damageClass, filters, bbox),
    [state, damageClass, filters, bbox],
  );

  // The fetch effect keys on content, not reference, so identical payloads don't refetch.
  const payloadSignature = useMemo(() => JSON.stringify(committedPayload), [committedPayload]);

  // Bbox excluded: pan/zoom doesn't bump `filterVersion` (see header).
  const nonBboxSignature = useMemo(() => {
    const { location, ...rest } = committedPayload;
    if (!location) return JSON.stringify(rest);
    const { bbox: _bbox, ...locWithoutBbox } = location as {
      bbox?: unknown;
      division_ids?: string[];
      polygon?: unknown;
    };
    const hasLoc =
      (locWithoutBbox.division_ids?.length ?? 0) > 0 || locWithoutBbox.polygon !== undefined;
    return JSON.stringify(hasLoc ? { ...rest, location: locWithoutBbox } : rest);
  }, [committedPayload]);

  const lastNonBboxSignatureRef = useRef<string | null>(null);

  // An empty result is valid; Summary/Chat readiness is `response.rows.length > 0`.
  // biome-ignore lint/correctness/useExhaustiveDependencies: payloadSignature is the dedupe key; committedPayload + nonBboxSignature read inside the timer
  useEffect(() => {
    if (!crisisId) return;
    abortRef.current?.abort();
    if (debounceRef.current) clearTimeout(debounceRef.current);
    // Set loading before the debounce so the KPI boxes grey out with the gesture, not the fetch.
    setLoading(true);
    setError(null);
    debounceRef.current = setTimeout(() => {
      const ctrl = new AbortController();
      abortRef.current = ctrl;
      requestIdRef.current += 1;
      const myId = requestIdRef.current;
      searchReports(crisisId, committedPayload, ctrl.signal)
        .then((resp) => {
          if (ctrl.signal.aborted || myId !== requestIdRef.current) return;
          setResponse(resp);
          setDisplayResponse(resp);
          const sig = nonBboxSignature;
          if (lastNonBboxSignatureRef.current !== sig) {
            lastNonBboxSignatureRef.current = sig;
            setFilterVersion((v) => v + 1);
          }
          setLoading(false);
          if (crisisIdRef.current) loadBaseline(crisisIdRef.current, false);
        })
        .catch((e) => {
          if (ctrl.signal.aborted || myId !== requestIdRef.current) return;
          setError(e instanceof Error ? e.message : String(e));
          setLoading(false);
        });
    }, debounceMs);
    return () => {
      if (debounceRef.current) clearTimeout(debounceRef.current);
    };
  }, [crisisId, payloadSignature, debounceMs]);

  const setQuery = useCallback((q: string) => setState((s) => ({ ...s, query: q })), []);
  const setStrictness = useCallback(
    (st: Strictness) =>
      setState((s) => {
        if (s.strictness === st) return s;
        return { ...s, strictness: st };
      }),
    [],
  );
  const addLocation = useCallback((chip: LocationChip) => {
    setState((s) => {
      if (s.locations.some((l) => l.kind === chip.kind && l.id === chip.id)) return s;
      return { ...s, locations: [...s.locations, chip] };
    });
    focusSeqRef.current += 1;
    if ((chip.kind === "area" && chip.source === "osm") || chip.kind === "polygon") {
      const poly: LocationPolygon = {
        id: chip.id,
        name: chip.name,
        geometry: chip.polygon,
      };
      setPolygons((prev) => ({ ...prev, [chip.id]: poly }));
      setFocused({ seq: focusSeqRef.current, chip, polygon: poly });
      return;
    }
    // Overture: centroid marker now, outline fetched in the background.
    setFocused({ seq: focusSeqRef.current, chip, polygon: null });
    void fetchAreaGeometry(chip.id)
      .then((geom) => {
        if (!geom) return;
        const poly: LocationPolygon = { id: chip.id, name: chip.name, geometry: geom };
        setPolygons((prev) => ({ ...prev, [chip.id]: poly }));
        setFocused((prev) =>
          prev && prev.chip.id === chip.id ? { ...prev, polygon: poly } : prev,
        );
      })
      .catch(() => {
        // Non-fatal: the marker and filter still apply, only the outline is missing.
      });
  }, []);
  const removeLocation = useCallback((kind: LocationChip["kind"], id: string) => {
    setState((s) => ({
      ...s,
      locations: s.locations.filter((l) => !(l.kind === kind && l.id === id)),
    }));
    setPolygons((prev) => {
      if (!(id in prev)) return prev;
      const next = { ...prev };
      delete next[id];
      return next;
    });
    setFocused((prev) => (prev && prev.chip.kind === kind && prev.chip.id === id ? null : prev));
  }, []);
  // Backs the map's "Reset view".
  const clearLocations = useCallback(() => {
    setState((s) => (s.locations.length === 0 ? s : { ...s, locations: [] }));
    setPolygons((prev) => (Object.keys(prev).length === 0 ? prev : {}));
    setFocused(null);
  }, []);
  const setAnomaliesOnly = useCallback(
    (v: boolean) => setState((s) => ({ ...s, anomaliesOnly: v })),
    [],
  );

  const anomalyIds = useMemo(() => {
    if (!response) return new Set<string>();
    return new Set(response.rows.filter((r) => r.is_anomaly).map((r) => r.id));
  }, [response]);

  // With a query or chips, narrow the map's bbox fetch to the server's matches. Without
  // them the search mirrors the bbox, and an overlay would only hide rows past the cap.
  const overlayActive = useMemo(
    () => state.query.trim().length > 0 || state.locations.length > 0,
    [state.query, state.locations],
  );
  const searchIds = useMemo<Set<string> | null>(() => {
    if (!response || !overlayActive) return null;
    return new Set(response.rows.map((r) => r.id));
  }, [response, overlayActive]);

  const candidateNonEmpty = (response?.rows.length ?? 0) > 0;

  const locationPolygons = useMemo(() => Object.values(polygons), [polygons]);

  return {
    state,
    setQuery,
    setStrictness,
    addLocation,
    removeLocation,
    clearLocations,
    setAnomaliesOnly,
    response,
    // Use for visible counts. Map overlays and the anomaly chip read the live `response`.
    displayResponse,
    // Null until the first baseline fetch resolves.
    baselineStats,
    loading,
    error,
    candidateNonEmpty,
    committedPayload,
    filterVersion,
    anomalyIds,
    searchIds,
    locationPolygons,
    focused,
  };
}

// Per-country searches merged round-robin so each country is represented in the top hits.
// Same shape as `searchAreasScoped` in `CrisisAreaPicker.tsx`.
export async function searchAreasForCountries(
  q: string,
  countries: string[],
  signal: AbortSignal,
  limit = 8,
): Promise<AreaSearchHit[]> {
  if (countries.length <= 1) {
    return searchAreas(q, { country: countries[0], signal, limit });
  }
  const perCountry = await Promise.all(
    countries.map((c) =>
      searchAreas(q, { country: c, signal, limit }).catch(() => [] as AreaSearchHit[]),
    ),
  );
  const merged: AreaSearchHit[] = [];
  const seen = new Set<string>();
  const longest = Math.max(...perCountry.map((r) => r.length));
  for (let i = 0; i < longest && merged.length < limit; i++) {
    for (const list of perCountry) {
      const hit = list[i];
      if (!hit || seen.has(hit.id)) continue;
      seen.add(hit.id);
      merged.push(hit);
      if (merged.length >= limit) break;
    }
  }
  return merged;
}
