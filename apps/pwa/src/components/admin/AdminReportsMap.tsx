import maplibregl, { type ExpressionSpecification } from "maplibre-gl";
import "maplibre-gl/dist/maplibre-gl.css";
import {
  type ForwardedRef,
  forwardRef,
  memo,
  useCallback,
  useEffect,
  useImperativeHandle,
  useMemo,
  useRef,
  useState,
} from "react";
import {
  type AdminMapPoint,
  type AdminMapResponse,
  fetchAdminMap,
  listAdminBuildingStats,
} from "../../api/admin";
import { getAccessToken } from "../../api/auth";
import type { ChatContextReport, SearchRequest } from "../../api/search";
import { useAppliedTheme } from "../../hooks/useAppliedTheme";
import { API_BASE } from "../../lib/apiBase";
import { BASEMAP_ATTRIBUTION, basemapTiles } from "../../lib/basemap";
import type { AdminBuildingsFeatureCollection, AdminReportDamageClass } from "../../types/admin";

export type AdminMapDisplayMode = "points" | "heatmap" | "buildings" | "all";

/** Imperative hooks the guided walkthrough uses to demonstrate map reactivity:
 *  zoom into part of the crisis, then re-fit the whole area. No-ops until the
 *  map has initialized. */
export interface AdminReportsMapHandle {
  demoZoomIn: () => void;
  resetView: () => void;
}

/** Location-autocomplete chip, drawn as a small ring at `centroid`. */
export interface AdminMapLocationMarker {
  kind: "area" | "polygon";
  id: string;
  name: string;
  centroid: { lat: number; lng: number };
}

interface Props {
  crisisId: string;
  /** Crisis AOI envelope `[w, s, e, n]`; the map fits to it once per change.
   *  Null for crises without geometry (the map keeps its view). */
  crisisBbox?: [number, number, number, number] | null;
  /** Crisis AOI polygon, drawn as a dashed outline beneath the data layers.
   *  Null for crises without geometry (e.g. "Other / Unspecified"). */
  crisisGeometry?: GeoJSON.Polygon | GeoJSON.MultiPolygon | null;
  pmtilesUrl: string | null;
  displayMode: AdminMapDisplayMode;
  /** Server-side filter payload WITHOUT the viewport bbox (the map adds its
   *  own bbox + zoom). All filtering and clustering happens server-side. */
  filterPayload: SearchRequest;
  /** Dims the heatmap (an all-data layer) so it isn't misread as filtered. */
  hasActiveFilters?: boolean;
  selectedReportId: string | null;
  onSelect: (reportId: string) => void;
  /** Viewport bbox `[w, s, e, n]`, fired on every committed fetch. The parent
   *  feeds it into the AI search payload so summary and chat cover exactly
   *  what the coordinator can see. */
  onBboxChange?: (bbox: [number, number, number, number]) => void;
  /** Narrow to semantic anomalies (server-side; no-op without a query). */
  anomaliesOnly?: boolean;
  locationMarkers?: AdminMapLocationMarker[];
  /** Polygons for selected divisions, drawn as a translucent fill + outline. */
  locationPolygons?: Array<{
    id: string;
    name: string;
    geometry: GeoJSON.Polygon | GeoJSON.MultiPolygon;
  }>;
  /** Most recently picked location chip; the map flies to it. `seq` bumps on
   *  every pick so re-selecting the same chip still re-flies. */
  focusedLocation?: {
    seq: number;
    chip: { kind: "area" | "polygon"; id: string; centroid: { lat: number; lng: number } };
    polygon: { geometry: GeoJSON.Polygon | GeoJSON.MultiPolygon } | null;
  } | null;
  /** Polygon-draw mode: clicks add corners instead of selecting reports.
   *  Clicking the first vertex (3+ corners) closes the ring and calls
   *  `onDrawingFinish`; the parent then exits the mode. */
  drawingActive?: boolean;
  onDrawingFinish?: (polygon: GeoJSON.Polygon) => void;
  /** Reports the AI chat has in context (its locked K-set), drawn as a cyan
   *  overlay above the clusters so they stay visible inside cluster bubbles. */
  chatContextReports?: ChatContextReport[];
  /** Report a chat citation asked to focus; its marker is highlighted and
   *  pulsed. `seq` bumps on every click so re-clicking re-triggers it. */
  chatFocusReport?: { id: string; seq: number } | null;
}

// Per building, not per report. Must not exceed the server's `le=10000` bound.
const BUILDING_STATS_LIMIT = 10_000;
// Short on purpose: enough to coalesce a quick double-pan, still responsive.
const FETCH_DEBOUNCE_MS = 150;

// Opening world view; also the reset-view fallback for a crisis with no AOI.
const DEFAULT_CENTER: [number, number] = [20, 25];
const DEFAULT_ZOOM = 2.4;

/** Camera `(center, zoom)` snapshot. Refetch only when the camera moved, not
 *  when the container resized: comparing bboxes instead caused a feedback
 *  loop, since toolbar reflow during a search resizes the map, `trackResize`
 *  fires `moveend`, and the bbox changes without any navigation. */
interface CameraSnapshot {
  lng: number;
  lat: number;
  zoom: number;
}

function cameraApproxEqual(a: CameraSnapshot, b: CameraSnapshot): boolean {
  // Far below any user-intended move; smaller deltas are resize jitter.
  const COORD_EPS = 1e-7;
  const ZOOM_EPS = 1e-3;
  return (
    Math.abs(a.lng - b.lng) < COORD_EPS &&
    Math.abs(a.lat - b.lat) < COORD_EPS &&
    Math.abs(a.zoom - b.zoom) < ZOOM_EPS
  );
}

// `getBounds()` can exceed [-180, 180] / [-90, 90] when zoomed far out or while
// the canvas is unsized, and the API 400s on such a bbox. Crisis areas never
// straddle the antimeridian, so a straight clamp is safe.
function clampBbox([w, s, e, n]: [number, number, number, number]): [
  number,
  number,
  number,
  number,
] {
  const lon = (x: number) => Math.max(-180, Math.min(180, x));
  const lat = (y: number) => Math.max(-90, Math.min(90, y));
  return [lon(w), lat(s), lon(e), lat(n)];
}

// Padded AOI envelope used as the camera's max bounds. The margin absorbs the
// fit padding and keeps the boundary ring off the viewport edge.
function maxBoundsForBbox([w, s, e, n]: [
  number,
  number,
  number,
  number,
]): maplibregl.LngLatBoundsLike {
  const dx = Math.max((e - w) * 0.4, 0.02);
  const dy = Math.max((n - s) * 0.4, 0.02);
  return [
    [w - dx, s - dy],
    [e + dx, n + dy],
  ];
}

const CRISIS_AOI_SRC = "admin-crisis-aoi";
const CRISIS_AOI_CASING_LAYER = "admin-crisis-aoi-casing";
const CRISIS_AOI_LINE_LAYER = "admin-crisis-aoi-line";
const REPORTS_SRC = "admin-reports";
const REPORTS_POINT_LAYER = "admin-reports-point";
// Soft translucent halo drawn beneath AI-geocoded (approximate) points to
// read as an uncertainty area; wider when the geocode is `area_only`.
const REPORTS_APPROX_HALO_LAYER = "admin-reports-approx-halo";
const REPORTS_CLUSTER_LAYER = "admin-reports-cluster";
const REPORTS_CLUSTER_COUNT_LAYER = "admin-reports-cluster-count";
const BUILDINGS_SRC = "buildings";
const BUILDINGS_LAYER = "building-footprints";
const BUILDING_STATS_SRC = "admin-building-stats";
const BUILDING_STATS_FILL_LAYER = "admin-building-stats-fill";
const BUILDING_STATS_LINE_LAYER = "admin-building-stats-line";
const HEAT_SRC = "admin-heat";
const HEAT_FILL_LAYER = "admin-heat-fill";
const HEAT_LINE_LAYER = "admin-heat-line";
// The anomaly ring sits under the point layer so the coloured pin stays on
// top; heat-mode anomaly dots sit above the heat fill.
const ANOMALY_RING_LAYER = "admin-reports-anomaly-ring";
const ANOMALY_HEAT_DOTS_LAYER = "admin-reports-anomaly-heat-dots";
const LOCATIONS_SRC = "admin-locations";
const LOCATIONS_CIRCLE_LAYER = "admin-locations-circle";
const LOCATIONS_RING_LAYER = "admin-locations-ring";
const LOCATIONS_LABEL_LAYER = "admin-locations-label";
const LOCATIONS_POLY_SRC = "admin-locations-poly";
const LOCATIONS_POLY_FILL_LAYER = "admin-locations-poly-fill";
const LOCATIONS_POLY_LINE_LAYER = "admin-locations-poly-line";
// Draw-mode layers: in-progress vertices as dots, edges as a dashed line.
const DRAW_SRC = "admin-draw";
const DRAW_LINE_LAYER = "admin-draw-line";
const DRAW_POINTS_LAYER = "admin-draw-points";
// Chat-context overlay. Cyan is distinct from the severity palette, the
// cluster ramp and the amber anomaly ring. The `focused` feature-state (a
// clicked citation) enlarges and recolours the marker.
const CHAT_CTX_SRC = "admin-chat-ctx";
const CHAT_CTX_HALO_LAYER = "admin-chat-ctx-halo";
const CHAT_CTX_DOT_LAYER = "admin-chat-ctx-dot";
// Pulsing ring around a clicked citation's report.
const CHAT_CTX_FOCUS_RING_LAYER = "admin-chat-ctx-focus-ring";
const CHAT_CTX_FOCUS_RING_HUE = "#facc15";
const CHAT_CTX_HUE = "#06b6d4";
const CHAT_CTX_FOCUS_HUE = "#ec4899";

interface HeatHoverInfo {
  x: number;
  y: number;
  reportCount: number;
  minimal: number;
  partial: number;
  complete: number;
  weightedSeverity: number;
  latestAt: string;
}

interface BuildingHoverInfo {
  x: number;
  y: number;
  buildingId: string;
  name: string | null;
  reportCount: number;
  minimal: number;
  partial: number;
  complete: number;
  latestDamageClass: AdminReportDamageClass;
  latestAt: string;
}

function polygonBounds(
  geom: GeoJSON.Polygon | GeoJSON.MultiPolygon,
): [[number, number], [number, number]] | null {
  const rings: GeoJSON.Position[][] =
    geom.type === "Polygon" ? geom.coordinates : geom.coordinates.flat();
  let minLng = Number.POSITIVE_INFINITY;
  let minLat = Number.POSITIVE_INFINITY;
  let maxLng = Number.NEGATIVE_INFINITY;
  let maxLat = Number.NEGATIVE_INFINITY;
  for (const ring of rings) {
    for (const [lng, lat] of ring) {
      if (lng < minLng) minLng = lng;
      if (lng > maxLng) maxLng = lng;
      if (lat < minLat) minLat = lat;
      if (lat > maxLat) maxLat = lat;
    }
  }
  if (!Number.isFinite(minLng)) return null;
  return [
    [minLng, minLat],
    [maxLng, maxLat],
  ];
}

function relativeAgo(iso: string): string {
  if (!iso) return "—";
  const ms = Date.now() - new Date(iso).getTime();
  if (Number.isNaN(ms) || ms < 0) return iso;
  const sec = Math.floor(ms / 1000);
  if (sec < 60) return `${sec}s ago`;
  const min = Math.floor(sec / 60);
  if (min < 60) return `${min}m ago`;
  const hr = Math.floor(min / 60);
  if (hr < 48) return `${hr}h ago`;
  return `${Math.floor(hr / 24)}d ago`;
}

// Numeric feature ids let selection use `feature-state` instead of rebuilding
// the collection; the original UUID rides in properties for click lookup.
function hashId(id: string): number {
  // 32-bit FNV-1a. A collision only mis-highlights one dot.
  let h = 2166136261;
  for (let i = 0; i < id.length; i++) {
    h ^= id.charCodeAt(i);
    h = (h * 16777619) >>> 0;
  }
  return h;
}

// Matches MapLibre's native `point_count_abbreviated` (1234 -> "1.2k").
function abbreviateCount(n: number): string {
  if (n < 1000) return String(n);
  if (n < 10_000) return `${(n / 1000).toFixed(1).replace(/\.0$/, "")}k`;
  if (n < 1_000_000) return `${Math.round(n / 1000)}k`;
  return `${(n / 1_000_000).toFixed(1).replace(/\.0$/, "")}M`;
}

type ReportFeatureProps = {
  id?: string;
  damage_class?: string;
  is_anomaly: 0 | 1;
  location_source?: string;
  is_approx: 0 | 1;
  area_only: 0 | 1;
  // Server cluster bubbles only; named like MapLibre's native cluster props.
  point_count?: number;
  point_count_abbreviated?: string;
  distinct_point_count?: number;
};

// Cluster bubbles carry `point_count` (cluster layers); points carry
// `damage_class` + provenance (point layer). The server does all clustering,
// so the source stays `cluster: false`.
function mapResponseToFeatureCollection(
  data: AdminMapResponse | null,
): GeoJSON.FeatureCollection<GeoJSON.Point, ReportFeatureProps> {
  if (data === null) return { type: "FeatureCollection", features: [] };
  if (data.mode === "clusters") {
    return {
      type: "FeatureCollection",
      features: data.cells.map((c, i) => {
        // A one-report cell renders as its severity pin, never a "1" bubble.
        if (c.total === 1 && c.report_id) {
          return {
            id: hashId(c.report_id),
            type: "Feature",
            geometry: { type: "Point", coordinates: [c.lng, c.lat] },
            properties: {
              id: c.report_id,
              damage_class: c.damage_class ?? "minimal",
              is_anomaly: 0,
              location_source: c.location_source ?? "submitted_pin",
              is_approx: c.location_source === "ai_geocode" ? (1 as const) : (0 as const),
              area_only: c.location_area_only ? (1 as const) : (0 as const),
            },
          };
        }
        return {
          // Stable across refetches of the same cell, so feature-state doesn't churn.
          id: hashId(`${c.lat.toFixed(5)},${c.lng.toFixed(5)},${i}`),
          type: "Feature",
          geometry: { type: "Point", coordinates: [c.lng, c.lat] },
          properties: {
            point_count: c.total,
            point_count_abbreviated: abbreviateCount(c.total),
            distinct_point_count: c.distinct_point_count,
            is_anomaly: 0,
            is_approx: 0,
            area_only: 0,
          },
        };
      }),
    };
  }
  return {
    type: "FeatureCollection",
    features: data.items.map((r: AdminMapPoint) => ({
      id: hashId(r.id),
      type: "Feature",
      geometry: { type: "Point", coordinates: [r.map_point.lng, r.map_point.lat] },
      properties: {
        id: r.id,
        damage_class: r.damage_class,
        // 0/1 so style expressions can match exactly.
        is_anomaly: r.is_anomaly ? (1 as const) : (0 as const),
        location_source: r.location_source ?? "submitted_pin",
        is_approx: r.location_source === "ai_geocode" ? (1 as const) : (0 as const),
        area_only: r.location_area_only ? (1 as const) : (0 as const),
      },
    })),
  };
}

// Sequential blue-to-fuchsia ramp, kept off the severity and heatmap palettes.
const CLUSTER_COLORS = ["#2563eb", "#4f46e5", "#7c3aed", "#9333ea", "#a21caf"] as const;
const CLUSTER_RADIUS_MIN = 12;
const CLUSTER_RADIUS_MAX = 26;
// Counts are heavily right-skewed, so colour and radius interpolate on ln(count)
// over a per-viewport domain (`clusterCountLnDomain`). The floor is ln(2):
// singleton cells render as pins, so the smallest bubble holds 2.
const CLUSTER_LN_FLOOR = Math.log(2);
// Domain for the initial paint, before data arrives.
const CLUSTER_LN_DEFAULT_HI = Math.log(200);

// Evenly spaced `stop, value, ...` pairs over [lo, hi] for `interpolate`.
function lnRampStops<T>(lo: number, hi: number, values: readonly T[]): (number | T)[] {
  const span = hi - lo;
  const last = values.length - 1;
  const stops: (number | T)[] = [];
  for (let i = 0; i < values.length; i++) {
    stops.push(lo + (span * i) / last, values[i]);
  }
  return stops;
}

function clusterColorExpr(lo: number, hi: number): ExpressionSpecification {
  return [
    "interpolate",
    ["linear"],
    ["ln", ["get", "point_count"]],
    ...lnRampStops(lo, hi, CLUSTER_COLORS),
  ] as unknown as ExpressionSpecification;
}

function clusterRadiusExpr(lo: number, hi: number): ExpressionSpecification {
  return [
    "interpolate",
    ["linear"],
    ["ln", ["get", "point_count"]],
    lo,
    CLUSTER_RADIUS_MIN,
    hi,
    CLUSTER_RADIUS_MAX,
  ] as unknown as ExpressionSpecification;
}

// Upper bound is the p95 count, not the max, so one dense outlier doesn't
// compress every other bubble. Null when there are no bubbles (points mode).
function clusterCountLnDomain(
  fc: GeoJSON.FeatureCollection<GeoJSON.Point, ReportFeatureProps>,
): { lo: number; hi: number } | null {
  const counts: number[] = [];
  for (const f of fc.features) {
    const c = f.properties.point_count;
    if (typeof c === "number" && c >= 2) counts.push(c);
  }
  if (counts.length === 0) return null;
  counts.sort((a, b) => a - b);
  const p95 = counts[Math.floor(0.95 * (counts.length - 1))];
  const hi = Math.log(Math.max(p95, 3));
  // Guarantee a strictly-ascending domain even when every bubble is a 2.
  return { lo: CLUSTER_LN_FLOOR, hi: Math.max(hi, CLUSTER_LN_FLOOR + 0.5) };
}

// Ids use `hashId` like the base layer so `focused` feature-state can be set by id.
function chatContextToFeatureCollection(
  reports: ChatContextReport[] | undefined,
): GeoJSON.FeatureCollection<GeoJSON.Point, { id: string }> {
  if (!reports || reports.length === 0) return { type: "FeatureCollection", features: [] };
  return {
    type: "FeatureCollection",
    features: reports.map((r) => ({
      id: hashId(r.id),
      type: "Feature",
      geometry: { type: "Point", coordinates: [r.lng, r.lat] },
      properties: { id: r.id },
    })),
  };
}

function AdminReportsMapImpl(
  {
    crisisId,
    crisisBbox,
    crisisGeometry,
    pmtilesUrl,
    displayMode,
    filterPayload,
    hasActiveFilters,
    selectedReportId,
    onSelect,
    onBboxChange,
    anomaliesOnly,
    locationMarkers,
    locationPolygons,
    focusedLocation,
    drawingActive,
    onDrawingFinish,
    chatContextReports,
    chatFocusReport,
  }: Props,
  ref: ForwardedRef<AdminReportsMapHandle>,
) {
  const containerRef = useRef<HTMLDivElement>(null);
  const mapRef = useRef<maplibregl.Map | null>(null);
  // Ref read by the mount-once effect, so a theme flip doesn't recreate the map.
  const theme = useAppliedTheme();
  const themeRef = useRef(theme);
  themeRef.current = theme;

  // Pre-demo camera, so reset restores exactly where the user was.
  const demoCameraRef = useRef<{ center: maplibregl.LngLat; zoom: number } | null>(null);
  useImperativeHandle(
    ref,
    () => ({
      demoZoomIn: () => {
        const map = mapRef.current;
        if (!map) return;
        demoCameraRef.current = { center: map.getCenter(), zoom: map.getZoom() };
        // Zoom to the densest cluster so the in-view count drops to a real,
        // non-zero subset, even when the data sits off-center.
        let best: { x: number; y: number; count: number } | null = null;
        let sumX = 0;
        let sumY = 0;
        let np = 0;
        try {
          for (const f of map.querySourceFeatures(REPORTS_SRC)) {
            if (f.geometry?.type !== "Point") continue;
            const [x, y] = f.geometry.coordinates;
            const count = (f.properties?.point_count as number | undefined) ?? 1;
            sumX += x;
            sumY += y;
            np += 1;
            if (!best || count > best.count) best = { x, y, count };
          }
        } catch {
          // querySourceFeatures can throw if the source isn't ready.
        }
        const target: [number, number] | null = best
          ? [best.x, best.y]
          : np > 0
            ? [sumX / np, sumY / np]
            : null;
        if (target) {
          map.easeTo({ center: target, zoom: 13, duration: 1000 });
        } else {
          map.easeTo({ zoom: Math.min(map.getZoom() + 2, 15), duration: 900 });
        }
      },
      resetView: () => {
        const map = mapRef.current;
        if (!map) return;
        const cam = demoCameraRef.current;
        if (cam) {
          demoCameraRef.current = null;
          map.easeTo({ center: cam.center, zoom: cam.zoom, duration: 700 });
        } else if (crisisBbox) {
          const [w, s, e, n] = crisisBbox;
          map.fitBounds(
            [
              [w, s],
              [e, n],
            ],
            { padding: 56, maxZoom: 14, duration: 700 },
          );
        } else {
          // No AOI envelope (e.g. "Other / Unspecified"): use the opening view.
          map.easeTo({ center: DEFAULT_CENTER, zoom: DEFAULT_ZOOM, duration: 700 });
        }
      },
    }),
    [crisisBbox],
  );
  const mapLoadedRef = useRef(false);
  const debounceRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const abortRef = useRef<AbortController | null>(null);
  // Callback/value refs read by handlers bound once in the load effect.
  const onSelectRef = useRef(onSelect);
  onSelectRef.current = onSelect;
  const onBboxChangeRef = useRef(onBboxChange);
  onBboxChangeRef.current = onBboxChange;
  const filterPayloadRef = useRef(filterPayload);
  filterPayloadRef.current = filterPayload;
  const anomaliesOnlyRef = useRef(anomaliesOnly ?? false);
  anomaliesOnlyRef.current = anomaliesOnly ?? false;

  // Server cluster bubbles (zoomed out) or individual points (zoomed in).
  const [mapData, setMapData] = useState<AdminMapResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [heatHover, setHeatHover] = useState<HeatHoverInfo | null>(null);
  const [buildingHover, setBuildingHover] = useState<BuildingHoverInfo | null>(null);
  const [buildingStatsTruncated, setBuildingStatsTruncated] = useState(false);
  // Draw vertices in state; refs mirror them for the once-bound click handler.
  const [drawPoints, setDrawPoints] = useState<Array<[number, number]>>([]);
  const drawPointsRef = useRef(drawPoints);
  drawPointsRef.current = drawPoints;
  const drawingActiveRef = useRef(drawingActive ?? false);
  drawingActiveRef.current = drawingActive ?? false;
  const onDrawingFinishRef = useRef(onDrawingFinish);
  onDrawingFinishRef.current = onDrawingFinish;
  const buildingStatsAbortRef = useRef<AbortController | null>(null);
  const buildingStatsDebounceRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  // Read by layer-creation effects that must not depend on `displayMode`, so a
  // (re)created layer gets the right visibility instead of defaulting to
  // "visible" and leaking into other modes.
  const displayModeRef = useRef(displayMode);
  displayModeRef.current = displayMode;

  // Called on mode change, on load, and when heat layers are recreated. Each
  // `setVis` no-ops for a layer not yet added, so calling it early is safe.
  const applyLayerVisibility = useCallback(() => {
    const map = mapRef.current;
    if (!map) return;
    const mode = displayModeRef.current;
    const pointsVisible = mode === "points" || mode === "all";
    const heatVisible = mode === "heatmap" || mode === "all";
    // Heavy (up to 10k buildings), so only in "buildings" mode, not "all".
    const buildingStatsVisible = mode === "buildings";
    const setVis = (layerId: string, visible: boolean) => {
      if (!map.getLayer(layerId)) return;
      map.setLayoutProperty(layerId, "visibility", visible ? "visible" : "none");
    };
    setVis(REPORTS_POINT_LAYER, pointsVisible);
    setVis(REPORTS_APPROX_HALO_LAYER, pointsVisible);
    setVis(REPORTS_CLUSTER_LAYER, pointsVisible);
    setVis(REPORTS_CLUSTER_COUNT_LAYER, pointsVisible);
    setVis(HEAT_FILL_LAYER, heatVisible);
    setVis(HEAT_LINE_LAYER, heatVisible);
    setVis(BUILDING_STATS_FILL_LAYER, buildingStatsVisible);
    setVis(BUILDING_STATS_LINE_LAYER, buildingStatsVisible);
    setVis(ANOMALY_RING_LAYER, pointsVisible);
    setVis(ANOMALY_HEAT_DOTS_LAYER, heatVisible);
  }, []);

  useEffect(() => {
    if (!containerRef.current) return;
    const map = new maplibregl.Map({
      container: containerRef.current,
      style: {
        version: 8,
        sources: {
          osm: {
            type: "raster",
            tiles: basemapTiles(themeRef.current),
            tileSize: 256,
            attribution: BASEMAP_ATTRIBUTION,
          },
        },
        layers: [{ id: "osm-tiles", type: "raster", source: "osm" }],
      },
      center: DEFAULT_CENTER,
      zoom: DEFAULT_ZOOM,
      // Admin tiles under `/admin/*` need the bearer token; MapLibre fetches
      // them directly, so add the header here.
      transformRequest: (url, resourceType) => {
        if (resourceType === "Tile" && url.startsWith(`${API_BASE}/admin/`)) {
          const token = getAccessToken();
          if (!token) return { url };
          return { url, headers: { Authorization: `Bearer ${token}` } };
        }
        return { url };
      },
    });
    map.addControl(new maplibregl.NavigationControl({ showCompass: false }), "top-right");
    mapRef.current = map;

    map.on("load", () => {
      mapLoadedRef.current = true;
      // Added first so the AOI outline sits beneath every data layer. Teal
      // stays distinct from the severity palette.
      map.addSource(CRISIS_AOI_SRC, {
        type: "geojson",
        data: { type: "FeatureCollection", features: [] },
      });
      map.addLayer({
        id: CRISIS_AOI_CASING_LAYER,
        type: "line",
        source: CRISIS_AOI_SRC,
        layout: { "line-join": "round" },
        paint: { "line-color": "#0d9488", "line-width": 8, "line-opacity": 0.3, "line-blur": 3 },
      });
      map.addLayer({
        id: CRISIS_AOI_LINE_LAYER,
        type: "line",
        source: CRISIS_AOI_SRC,
        layout: { "line-join": "round" },
        paint: {
          "line-color": "#0d9488",
          "line-width": 2.8,
          "line-opacity": 1,
          "line-dasharray": [2.5, 1.5],
        },
      });
      // Added before the reports source so building fills sit beneath markers.
      map.addSource(BUILDING_STATS_SRC, {
        type: "geojson",
        data: { type: "FeatureCollection", features: [] },
        promoteId: "building_id",
      });
      // No native clustering: the server sends cluster bubbles at low zoom and
      // points at high zoom.
      map.addSource(REPORTS_SRC, {
        type: "geojson",
        data: { type: "FeatureCollection", features: [] },
      });

      map.addSource(CHAT_CTX_SRC, {
        type: "geojson",
        data: { type: "FeatureCollection", features: [] },
      });

      map.addLayer({
        id: REPORTS_CLUSTER_LAYER,
        type: "circle",
        source: REPORTS_SRC,
        filter: ["has", "point_count"],
        // Larger cluster on top. Pairs with the count layer's `symbol-sort-key`
        // so the top bubble is the one whose label survives collision.
        layout: { "circle-sort-key": ["get", "point_count"] },
        paint: {
          // Default domain; overridden per viewport once bubbles arrive. Every
          // colour is dark enough for the white count label.
          "circle-color": clusterColorExpr(CLUSTER_LN_FLOOR, CLUSTER_LN_DEFAULT_HI),
          "circle-radius": clusterRadiusExpr(CLUSTER_LN_FLOOR, CLUSTER_LN_DEFAULT_HI),
          "circle-stroke-width": 2,
          "circle-stroke-color": "#ffffff",
          "circle-opacity": 0.9,
        },
      });

      map.addLayer({
        id: REPORTS_CLUSTER_COUNT_LAYER,
        type: "symbol",
        source: REPORTS_SRC,
        filter: ["has", "point_count"],
        layout: {
          "text-field": ["get", "point_count_abbreviated"],
          "text-size": 12,
          "text-font": ["Open Sans Bold", "Arial Unicode MS Bold"],
          // Collision stays on so overlapping bubbles show one count. Symbol
          // placement favours the LOWER sort key while the circle layer draws
          // the larger count on top, so negate: the top bubble keeps its label.
          "symbol-sort-key": ["*", -1, ["get", "point_count"]],
        },
        paint: { "text-color": "#ffffff" },
      });

      // Under the report dot so the damage colour stays readable.
      map.addLayer({
        id: ANOMALY_RING_LAYER,
        type: "circle",
        source: REPORTS_SRC,
        filter: ["all", ["!", ["has", "point_count"]], ["==", ["get", "is_anomaly"], 1]],
        paint: {
          "circle-color": "rgba(255, 200, 60, 0)",
          "circle-stroke-color": "#f59e0b",
          "circle-stroke-width": 3,
          "circle-radius": 12,
          "circle-opacity": 0.92,
        },
      });

      // Soft halo under AI-geocoded points so a machine guess never passes for
      // a precise GPS pin; wider for `area_only` geocodes. Added before the
      // point layer so it sits beneath the dot.
      map.addLayer({
        id: REPORTS_APPROX_HALO_LAYER,
        type: "circle",
        source: REPORTS_SRC,
        filter: ["all", ["!", ["has", "point_count"]], ["==", ["get", "is_approx"], 1]],
        paint: {
          "circle-color": [
            "match",
            ["get", "damage_class"],
            "complete",
            "#b1322a",
            "partial",
            "#b87914",
            "minimal",
            "#2f7d4f",
            "#58698a",
          ],
          "circle-radius": ["case", ["==", ["get", "area_only"], 1], 22, 14],
          "circle-opacity": 0.16,
          "circle-stroke-color": [
            "match",
            ["get", "damage_class"],
            "complete",
            "#b1322a",
            "partial",
            "#b87914",
            "minimal",
            "#2f7d4f",
            "#58698a",
          ],
          "circle-stroke-width": 1,
          "circle-stroke-opacity": 0.4,
        },
      });

      map.addLayer({
        id: REPORTS_POINT_LAYER,
        type: "circle",
        source: REPORTS_SRC,
        filter: ["!", ["has", "point_count"]],
        paint: {
          "circle-color": [
            "match",
            ["get", "damage_class"],
            "complete",
            "#b1322a",
            "partial",
            "#b87914",
            "minimal",
            "#2f7d4f",
            "#58698a",
          ],
          "circle-radius": ["case", ["boolean", ["feature-state", "selected"], false], 8, 6],
          "circle-stroke-width": [
            "case",
            ["boolean", ["feature-state", "selected"], false],
            3,
            1.5,
          ],
          "circle-stroke-color": "#ffffff",
          // AI-geocoded points render hollow (faded fill) so they read as
          // approximate next to a solid submitted pin.
          "circle-opacity": ["case", ["==", ["get", "is_approx"], 1], 0.4, 1],
        },
      });

      // Above the base layers so the chat's K-set shows even inside clusters.
      map.addLayer({
        id: CHAT_CTX_HALO_LAYER,
        type: "circle",
        source: CHAT_CTX_SRC,
        paint: {
          "circle-color": [
            "case",
            ["boolean", ["feature-state", "focused"], false],
            CHAT_CTX_FOCUS_HUE,
            CHAT_CTX_HUE,
          ],
          "circle-radius": ["case", ["boolean", ["feature-state", "focused"], false], 22, 13],
          "circle-opacity": ["case", ["boolean", ["feature-state", "focused"], false], 0.32, 0.16],
        },
      });
      map.addLayer({
        id: CHAT_CTX_DOT_LAYER,
        type: "circle",
        source: CHAT_CTX_SRC,
        paint: {
          "circle-color": [
            "case",
            ["boolean", ["feature-state", "focused"], false],
            CHAT_CTX_FOCUS_HUE,
            CHAT_CTX_HUE,
          ],
          "circle-radius": ["case", ["boolean", ["feature-state", "focused"], false], 8, 5.5],
          "circle-stroke-color": "#ffffff",
          "circle-stroke-width": 2,
        },
      });
      // Radius and opacity are animated by the focus effect's rAF loop.
      map.addLayer({
        id: CHAT_CTX_FOCUS_RING_LAYER,
        type: "circle",
        source: CHAT_CTX_SRC,
        paint: {
          "circle-color": "rgba(0,0,0,0)",
          "circle-radius": 0,
          "circle-stroke-color": CHAT_CTX_FOCUS_RING_HUE,
          "circle-stroke-width": 4,
          "circle-stroke-opacity": 0,
        },
      });

      // Fill colour is the latest report's damage class. Inserted below the
      // report layers so markers stay on top.
      map.addLayer(
        {
          id: BUILDING_STATS_FILL_LAYER,
          type: "fill",
          source: BUILDING_STATS_SRC,
          paint: {
            "fill-color": [
              "match",
              ["get", "latest_damage_class"],
              "complete",
              "#b1322a",
              "partial",
              "#b87914",
              "minimal",
              "#2f7d4f",
              "#58698a",
            ],
            "fill-opacity": ["case", ["boolean", ["feature-state", "hover"], false], 0.85, 0.55],
          },
        },
        REPORTS_CLUSTER_LAYER,
      );
      map.addLayer(
        {
          id: BUILDING_STATS_LINE_LAYER,
          type: "line",
          source: BUILDING_STATS_SRC,
          paint: {
            "line-color": "#1f2937",
            "line-width": ["case", ["boolean", ["feature-state", "hover"], false], 2, 0.6],
            "line-opacity": 0.85,
          },
        },
        REPORTS_CLUSTER_LAYER,
      );

      let hoveredBuildingId: string | null = null;
      map.on("mouseenter", BUILDING_STATS_FILL_LAYER, () => {
        map.getCanvas().style.cursor = "pointer";
      });
      map.on("mousemove", BUILDING_STATS_FILL_LAYER, (e) => {
        const f = e.features?.[0];
        if (!f) return;
        const p = f.properties as Record<string, unknown> | null;
        if (!p) return;
        const buildingId = String(p.building_id ?? "");
        if (hoveredBuildingId !== buildingId) {
          if (hoveredBuildingId !== null) {
            map.setFeatureState(
              { source: BUILDING_STATS_SRC, id: hoveredBuildingId },
              { hover: false },
            );
          }
          hoveredBuildingId = buildingId;
          map.setFeatureState({ source: BUILDING_STATS_SRC, id: buildingId }, { hover: true });
        }
        const rawLatest = String(p.latest_damage_class ?? "minimal");
        const latestDamageClass: AdminReportDamageClass =
          rawLatest === "complete" || rawLatest === "partial" ? rawLatest : "minimal";
        setBuildingHover({
          x: e.point.x,
          y: e.point.y,
          buildingId,
          name: typeof p.name === "string" && p.name.length > 0 ? p.name : null,
          reportCount: Number(p.report_count ?? 0),
          minimal: Number(p.minimal_count ?? 0),
          partial: Number(p.partial_count ?? 0),
          complete: Number(p.complete_count ?? 0),
          latestDamageClass,
          latestAt: String(p.latest_at ?? ""),
        });
      });
      map.on("mouseleave", BUILDING_STATS_FILL_LAYER, () => {
        if (hoveredBuildingId !== null) {
          map.setFeatureState(
            { source: BUILDING_STATS_SRC, id: hoveredBuildingId },
            { hover: false },
          );
          hoveredBuildingId = null;
        }
        map.getCanvas().style.cursor = "";
        setBuildingHover(null);
      });

      map.on("click", REPORTS_POINT_LAYER, (e) => {
        // While drawing, clicks add a vertex instead.
        if (drawingActiveRef.current) return;
        const f = e.features?.[0];
        const id = f?.properties?.id;
        if (typeof id === "string") onSelectRef.current(id);
      });

      map.on("click", CHAT_CTX_DOT_LAYER, (e) => {
        if (drawingActiveRef.current) return;
        const id = e.features?.[0]?.properties?.id;
        if (typeof id === "string") onSelectRef.current(id);
      });
      map.on("mouseenter", CHAT_CTX_DOT_LAYER, () => {
        map.getCanvas().style.cursor = "pointer";
      });
      map.on("mouseleave", CHAT_CTX_DOT_LAYER, () => {
        map.getCanvas().style.cursor = "";
      });

      map.on("click", REPORTS_CLUSTER_LAYER, (e) => {
        if (drawingActiveRef.current) return;
        const f = e.features?.[0];
        if (!f || f.geometry.type !== "Point") return;
        const center = f.geometry.coordinates as [number, number];
        // Server clusters have no expansion zoom. A cell whose reports share
        // one coordinate (a stacked building) never splits by zooming, so jump
        // straight to points mode; otherwise step in a couple of levels.
        const distinct = Number(f.properties?.distinct_point_count ?? 0);
        const target = distinct === 1 ? Math.max(map.getZoom(), 16) : map.getZoom() + 2;
        map.easeTo({ center, zoom: Math.min(target, 18) });
      });

      for (const layer of [REPORTS_POINT_LAYER, REPORTS_CLUSTER_LAYER]) {
        map.on("mouseenter", layer, () => {
          map.getCanvas().style.cursor = "pointer";
        });
        map.on("mouseleave", layer, () => {
          map.getCanvas().style.cursor = "";
        });
      }

      // Bound once: the layer id is stable across crisis changes, and MapLibre
      // suppresses events on hidden layers, so no display-mode guard is needed.
      map.on("mouseenter", HEAT_FILL_LAYER, () => {
        map.getCanvas().style.cursor = "crosshair";
      });
      map.on("mousemove", HEAT_FILL_LAYER, (e) => {
        const f = e.features?.[0];
        if (!f) return;
        const p = f.properties as Record<string, unknown> | null;
        if (!p) return;
        setHeatHover({
          x: e.point.x,
          y: e.point.y,
          reportCount: Number(p.report_count ?? 0),
          minimal: Number(p.minimal_count ?? 0),
          partial: Number(p.partial_count ?? 0),
          complete: Number(p.complete_count ?? 0),
          weightedSeverity: Number(p.weighted_severity ?? 0),
          latestAt: String(p.latest_at ?? ""),
        });
      });
      map.on("mouseleave", HEAT_FILL_LAYER, () => {
        map.getCanvas().style.cursor = "";
        setHeatHover(null);
      });

      // Keyed on the `is_anomaly` property rather than feature-state, since
      // heatmap mode hides the points layer and feature-state on a hidden
      // layer stops rendering.
      map.addLayer({
        id: ANOMALY_HEAT_DOTS_LAYER,
        type: "circle",
        source: REPORTS_SRC,
        filter: ["all", ["!", ["has", "point_count"]], ["==", ["get", "is_anomaly"], 1]],
        layout: { visibility: "none" },
        paint: {
          "circle-color": "#f59e0b",
          "circle-radius": 5,
          "circle-stroke-width": 1.5,
          "circle-stroke-color": "#ffffff",
          "circle-opacity": 0.95,
        },
      });

      // Division polygons, added before the centroid markers so they sit beneath.
      map.addSource(LOCATIONS_POLY_SRC, {
        type: "geojson",
        data: { type: "FeatureCollection", features: [] },
      });
      map.addLayer({
        id: LOCATIONS_POLY_FILL_LAYER,
        type: "fill",
        source: LOCATIONS_POLY_SRC,
        paint: {
          "fill-color": "#1d4ed8",
          "fill-opacity": 0.08,
        },
      });
      map.addLayer({
        id: LOCATIONS_POLY_LINE_LAYER,
        type: "line",
        source: LOCATIONS_POLY_SRC,
        paint: {
          "line-color": "#1d4ed8",
          "line-width": 1.5,
          "line-opacity": 0.85,
        },
      });

      map.addSource(LOCATIONS_SRC, {
        type: "geojson",
        data: { type: "FeatureCollection", features: [] },
      });
      map.addLayer({
        id: LOCATIONS_RING_LAYER,
        type: "circle",
        source: LOCATIONS_SRC,
        paint: {
          "circle-radius": 14,
          "circle-color": "rgba(33, 99, 232, 0)",
          "circle-stroke-color": "#1d4ed8",
          "circle-stroke-width": 2,
          "circle-opacity": 0.9,
        },
      });
      map.addLayer({
        id: LOCATIONS_CIRCLE_LAYER,
        type: "circle",
        source: LOCATIONS_SRC,
        paint: {
          "circle-radius": 5,
          "circle-color": "#1d4ed8",
          "circle-stroke-color": "#ffffff",
          "circle-stroke-width": 1.5,
        },
      });
      map.addLayer({
        id: LOCATIONS_LABEL_LAYER,
        type: "symbol",
        source: LOCATIONS_SRC,
        layout: {
          "text-field": ["get", "label"],
          "text-size": 11,
          "text-offset": [0, 1.4],
          "text-anchor": "top",
          "text-font": ["Open Sans Bold", "Arial Unicode MS Bold"],
        },
        paint: {
          "text-color": "#1d4ed8",
          "text-halo-color": "#ffffff",
          "text-halo-width": 1.5,
        },
      });

      map.addSource(DRAW_SRC, {
        type: "geojson",
        data: { type: "FeatureCollection", features: [] },
      });
      map.addLayer({
        id: DRAW_LINE_LAYER,
        type: "line",
        source: DRAW_SRC,
        filter: ["==", ["get", "kind"], "line"],
        paint: {
          "line-color": "#1d4ed8",
          "line-width": 2,
          "line-dasharray": [2, 2],
        },
      });
      map.addLayer({
        id: DRAW_POINTS_LAYER,
        type: "circle",
        source: DRAW_SRC,
        filter: ["==", ["get", "kind"], "point"],
        paint: {
          "circle-color": "#ffffff",
          "circle-stroke-color": "#1d4ed8",
          "circle-stroke-width": 2,
          "circle-radius": ["case", ["==", ["get", "is_first"], true], 7, 4.5],
        },
      });

      // Generic (not layer-scoped) click so a vertex can land anywhere,
      // including on a report dot; the layer handlers above bail while drawing.
      map.on("click", (e) => {
        if (!drawingActiveRef.current) return;
        const pts = drawPointsRef.current;
        if (pts.length >= 3) {
          // Click near the first vertex closes the ring.
          const first = map.project(pts[0]);
          const dx = first.x - e.point.x;
          const dy = first.y - e.point.y;
          if (dx * dx + dy * dy < 14 * 14) {
            const ring: number[][] = [...pts.map((p) => [p[0], p[1]]), [pts[0][0], pts[0][1]]];
            const polygon: GeoJSON.Polygon = {
              type: "Polygon",
              coordinates: [ring],
            };
            setDrawPoints([]);
            onDrawingFinishRef.current?.(polygon);
            return;
          }
        }
        setDrawPoints((prev) => [...prev, [e.lngLat.lng, e.lngLat.lat]]);
      });

      // Layers above default to "visible"; reconcile with the current mode.
      applyLayerVisibility();
    });

    return () => {
      mapLoadedRef.current = false;
      map.remove();
      mapRef.current = null;
    };
    // Mount-once: applyLayerVisibility has a stable identity.
  }, [applyLayerVisibility]);

  // Building-footprints underlay; absent until the crisis has run the ingest.
  useEffect(() => {
    const map = mapRef.current;
    if (!map) return;
    const apply = () => {
      if (map.getLayer(BUILDINGS_LAYER)) map.removeLayer(BUILDINGS_LAYER);
      if (map.getSource(BUILDINGS_SRC)) map.removeSource(BUILDINGS_SRC);
      if (!pmtilesUrl) return;
      map.addSource(BUILDINGS_SRC, { type: "vector", url: `pmtiles://${pmtilesUrl}` });
      map.addLayer(
        {
          id: BUILDINGS_LAYER,
          type: "fill",
          source: BUILDINGS_SRC,
          "source-layer": "building",
          paint: { "fill-color": "#0468b1", "fill-opacity": 0.18 },
        },
        REPORTS_CLUSTER_LAYER,
      );
    };
    if (mapLoadedRef.current) apply();
    else map.once("load", apply);
  }, [pmtilesUrl]);

  // Swap basemap tiles in place on theme change, without recreating the map.
  useEffect(() => {
    const map = mapRef.current;
    if (!map) return;
    const apply = () => {
      const src = map.getSource("osm") as maplibregl.RasterTileSource | undefined;
      src?.setTiles(basemapTiles(theme));
    };
    if (mapLoadedRef.current) apply();
    else map.once("load", apply);
  }, [theme]);

  // H3-aggregated heat MVT. The URL is crisis-bound, so source and layers are
  // recreated on every crisis change.
  useEffect(() => {
    const map = mapRef.current;
    if (!map) return;
    const apply = () => {
      if (map.getLayer(HEAT_LINE_LAYER)) map.removeLayer(HEAT_LINE_LAYER);
      if (map.getLayer(HEAT_FILL_LAYER)) map.removeLayer(HEAT_FILL_LAYER);
      if (map.getSource(HEAT_SRC)) map.removeSource(HEAT_SRC);
      map.addSource(HEAT_SRC, {
        type: "vector",
        tiles: [`${API_BASE}/admin/crises/${crisisId}/tiles/heat/{z}/{x}/{y}.pbf`],
        minzoom: 0,
        maxzoom: 18,
      });
      map.addLayer(
        {
          id: HEAT_FILL_LAYER,
          type: "fill",
          source: HEAT_SRC,
          "source-layer": "heat",
          paint: {
            "fill-color": [
              "interpolate",
              ["linear"],
              ["get", "report_count"],
              1,
              "#fde68a",
              5,
              "#fcd34d",
              15,
              "#fb923c",
              40,
              "#dc2626",
              100,
              "#7f1d1d",
            ],
            "fill-opacity": 0.8,
          },
        },
        REPORTS_CLUSTER_LAYER,
      );
      map.addLayer(
        {
          id: HEAT_LINE_LAYER,
          type: "line",
          source: HEAT_SRC,
          "source-layer": "heat",
          paint: { "line-color": "#00000022", "line-width": 1 },
        },
        REPORTS_CLUSTER_LAYER,
      );
      // Recreated layers default to "visible"; reconcile so heat doesn't leak
      // into other modes after a crisis switch.
      applyLayerVisibility();
    };
    if (mapLoadedRef.current) apply();
    else map.once("load", apply);
  }, [crisisId, applyLayerVisibility]);

  // Frame the crisis AOI once per crisis, so later pan/zoom is preserved.
  // Keyed per map instance (not a plain ref) so StrictMode's discarded first
  // map can't mark the crisis as fitted for the live remount.
  const fittedByMapRef = useRef(new WeakMap<maplibregl.Map, string>());
  // Fit and camera-lock live in one effect because MapLibre clamps fitBounds
  // to the active maxBounds: the old crisis's lock must be released before
  // fitting, then re-applied once the fit settles.
  useEffect(() => {
    const map = mapRef.current;
    if (!map) return;
    const apply = () => {
      // No geometry (e.g. "Other / Unspecified"): free pan.
      if (!crisisBbox) {
        map.setMaxBounds(null);
        return;
      }
      // Already framed: refresh the lock (bbox identity may change on a list
      // refresh) but don't re-fit over the coordinator's pan/zoom.
      if (fittedByMapRef.current.get(map) === crisisId) {
        map.setMaxBounds(maxBoundsForBbox(crisisBbox));
        return;
      }
      fittedByMapRef.current.set(map, crisisId);
      const [w, s, e, n] = crisisBbox;
      // The moveend guard skips the re-lock if the selection changed mid-flight.
      map.setMaxBounds(null);
      map.fitBounds(
        [
          [w, s],
          [e, n],
        ],
        { padding: 56, maxZoom: 14, duration: 700 },
      );
      map.once("moveend", () => {
        if (fittedByMapRef.current.get(map) === crisisId && crisisBbox) {
          map.setMaxBounds(maxBoundsForBbox(crisisBbox));
        }
      });
    };
    // Gate on `mapLoadedRef`, NOT `map.loaded()`: the latter is false while
    // tiles stream even after load fired, so a `once("load")` would never fire.
    if (mapLoadedRef.current) apply();
    else map.once("load", apply);
  }, [crisisId, crisisBbox]);

  // All layers stay mounted and only `visibility` toggles, so switching modes
  // keeps cached tiles.
  useEffect(() => {
    const map = mapRef.current;
    if (!map) return;
    if (mapLoadedRef.current) applyLayerVisibility();
    else map.once("load", applyLayerVisibility);
    // Drop tooltips for layers that are now hidden.
    if (displayMode !== "heatmap" && displayMode !== "all") setHeatHover(null);
    if (displayMode !== "buildings" && displayMode !== "all") setBuildingHover(null);
  }, [displayMode, applyLayerVisibility]);

  // The heatmap ignores filters by design (all-data context), so dim it while
  // a filter is active to avoid it being read as the filtered set.
  useEffect(() => {
    const map = mapRef.current;
    if (!map) return;
    const apply = () => {
      if (!map.getLayer(HEAT_FILL_LAYER)) return;
      map.setPaintProperty(HEAT_FILL_LAYER, "fill-opacity", hasActiveFilters ? 0.34 : 0.8);
    };
    if (mapLoadedRef.current) apply();
    else map.once("load", apply);
  }, [hasActiveFilters]);

  // Debounced `/map` fetch on camera move and on filter/crisis change. The
  // camera guard skips resize-driven `moveend`s (see `CameraSnapshot`);
  // filter-driven fetches pass `force` to bypass it.
  const payloadSignature = JSON.stringify(filterPayload);
  const lastFetchedCameraRef = useRef<CameraSnapshot | null>(null);
  // biome-ignore lint/correctness/useExhaustiveDependencies: payloadSignature is the dedupe key; filterPayload/anomaliesOnly are read via refs inside the closure
  useEffect(() => {
    const map = mapRef.current;
    if (!map) return;

    // A dep change (crisis or filter payload) invalidates the prior response.
    lastFetchedCameraRef.current = null;

    const fire = (force: boolean) => {
      if (!mapLoadedRef.current) return;
      const center = map.getCenter();
      const zoom = map.getZoom();
      const camera: CameraSnapshot = { lng: center.lng, lat: center.lat, zoom };
      const prevCamera = lastFetchedCameraRef.current;
      if (!force && prevCamera && cameraApproxEqual(prevCamera, camera)) return;
      lastFetchedCameraRef.current = camera;
      const bounds = map.getBounds();
      const bbox = clampBbox([
        bounds.getWest(),
        bounds.getSouth(),
        bounds.getEast(),
        bounds.getNorth(),
      ]);
      onBboxChangeRef.current?.(bbox);
      abortRef.current?.abort();
      const ctrl = new AbortController();
      abortRef.current = ctrl;
      setLoading(true);
      setError(null);
      fetchAdminMap(
        crisisId,
        {
          ...filterPayloadRef.current,
          bbox,
          zoom,
          anomalies_only: anomaliesOnlyRef.current,
        },
        { signal: ctrl.signal },
      )
        .then((resp) => {
          setMapData(resp);
          setLoading(false);
        })
        .catch((err) => {
          if (ctrl.signal.aborted) return;
          setError(err instanceof Error ? err.message : String(err));
          setLoading(false);
        });
    };

    const scheduleFire = (force: boolean) => {
      if (debounceRef.current) clearTimeout(debounceRef.current);
      debounceRef.current = setTimeout(() => fire(force), FETCH_DEBOUNCE_MS);
    };
    const onMoveEnd = () => scheduleFire(false);

    // Dep changes force a fetch even though the camera hasn't moved.
    if (mapLoadedRef.current) scheduleFire(true);
    else map.once("load", () => scheduleFire(true));

    map.on("moveend", onMoveEnd);
    return () => {
      map.off("moveend", onMoveEnd);
      if (debounceRef.current) clearTimeout(debounceRef.current);
      abortRef.current?.abort();
    };
  }, [crisisId, payloadSignature, anomaliesOnly]);

  // Per-building stats for the viewport, fetched separately from `/map` with
  // the same debounce and camera guard.
  const lastFetchedBuildingsCameraRef = useRef<CameraSnapshot | null>(null);
  // biome-ignore lint/correctness/useExhaustiveDependencies: payloadSignature re-triggers the effect on filter change; filterPayload is read via ref inside the closure
  useEffect(() => {
    const map = mapRef.current;
    if (!map) return;
    // Heavy, and hidden in every other mode, so only fetch in "buildings" mode.
    if (displayMode !== "buildings") return;

    lastFetchedBuildingsCameraRef.current = null;

    const applyData = (fc: AdminBuildingsFeatureCollection) => {
      if (!mapLoadedRef.current) return;
      const source = map.getSource(BUILDING_STATS_SRC) as maplibregl.GeoJSONSource | undefined;
      if (!source) return;
      // Already a FeatureCollection keyed by `building_id` (the `promoteId`).
      source.setData(fc as unknown as GeoJSON.FeatureCollection);
      setBuildingStatsTruncated(fc.truncated);
    };

    const fire = (force: boolean) => {
      if (!mapLoadedRef.current) return;
      const center = map.getCenter();
      const camera: CameraSnapshot = {
        lng: center.lng,
        lat: center.lat,
        zoom: map.getZoom(),
      };
      const prevCamera = lastFetchedBuildingsCameraRef.current;
      if (!force && prevCamera && cameraApproxEqual(prevCamera, camera)) return;
      lastFetchedBuildingsCameraRef.current = camera;
      const bounds = map.getBounds();
      const bbox = clampBbox([
        bounds.getWest(),
        bounds.getSouth(),
        bounds.getEast(),
        bounds.getNorth(),
      ]);
      buildingStatsAbortRef.current?.abort();
      const ctrl = new AbortController();
      buildingStatsAbortRef.current = ctrl;
      // Same filters as the map: only buildings with matching reports, and
      // "latest damage class" means the latest matching report.
      listAdminBuildingStats(crisisId, {
        bbox,
        filter: filterPayloadRef.current,
        limit: BUILDING_STATS_LIMIT,
        signal: ctrl.signal,
      })
        .then(applyData)
        .catch(() => {
          // Best-effort: keep the previous layer state on failure.
        });
    };

    const schedule = (force: boolean) => {
      if (buildingStatsDebounceRef.current) clearTimeout(buildingStatsDebounceRef.current);
      buildingStatsDebounceRef.current = setTimeout(() => fire(force), FETCH_DEBOUNCE_MS);
    };
    const onMoveEnd = () => schedule(false);

    if (mapLoadedRef.current) schedule(true);
    else map.once("load", () => schedule(true));

    map.on("moveend", onMoveEnd);
    return () => {
      map.off("moveend", onMoveEnd);
      if (buildingStatsDebounceRef.current) clearTimeout(buildingStatsDebounceRef.current);
      buildingStatsAbortRef.current?.abort();
    };
  }, [crisisId, payloadSignature, displayMode]);

  const featureCollection = useMemo(() => mapResponseToFeatureCollection(mapData), [mapData]);

  useEffect(() => {
    const map = mapRef.current;
    if (!map || !mapLoadedRef.current) return;
    const source = map.getSource(REPORTS_SRC) as maplibregl.GeoJSONSource | undefined;
    if (!source) return;
    source.setData(featureCollection);
    // Re-fit the cluster ramp to this viewport's count distribution.
    const domain = clusterCountLnDomain(featureCollection);
    if (domain && map.getLayer(REPORTS_CLUSTER_LAYER)) {
      map.setPaintProperty(
        REPORTS_CLUSTER_LAYER,
        "circle-color",
        clusterColorExpr(domain.lo, domain.hi),
      );
      map.setPaintProperty(
        REPORTS_CLUSTER_LAYER,
        "circle-radius",
        clusterRadiusExpr(domain.lo, domain.hi),
      );
    }
  }, [featureCollection]);

  // Selection highlight via feature-state, re-applied after each setData
  // (new data wipes feature-state).
  const prevSelectedHashRef = useRef<number | null>(null);
  // biome-ignore lint/correctness/useExhaustiveDependencies: featureCollection is the trigger, not read
  useEffect(() => {
    const map = mapRef.current;
    if (!map || !mapLoadedRef.current) return;
    const prev = prevSelectedHashRef.current;
    if (prev !== null) {
      map.setFeatureState({ source: REPORTS_SRC, id: prev }, { selected: false });
    }
    if (selectedReportId) {
      const h = hashId(selectedReportId);
      map.setFeatureState({ source: REPORTS_SRC, id: h }, { selected: true });
      prevSelectedHashRef.current = h;
    } else {
      prevSelectedHashRef.current = null;
    }
  }, [selectedReportId, featureCollection]);

  const chatContextFc = useMemo(
    () => chatContextToFeatureCollection(chatContextReports),
    [chatContextReports],
  );
  useEffect(() => {
    const map = mapRef.current;
    if (!map) return;
    const apply = () => {
      const source = map.getSource(CHAT_CTX_SRC) as maplibregl.GeoJSONSource | undefined;
      if (source) source.setData(chatContextFc);
    };
    if (mapLoadedRef.current) apply();
    else map.once("load", apply);
  }, [chatContextFc]);

  // Highlight + pulse a report when its chat citation is clicked. The context
  // set is read via ref so a context refresh doesn't re-trigger the effect.
  const chatContextRef = useRef(chatContextReports);
  chatContextRef.current = chatContextReports;
  const prevFocusHashRef = useRef<number | null>(null);
  const focusRafRef = useRef<number | null>(null);
  useEffect(() => {
    const map = mapRef.current;
    if (!map || !mapLoadedRef.current) return;
    const stopPulse = () => {
      if (focusRafRef.current !== null) {
        cancelAnimationFrame(focusRafRef.current);
        focusRafRef.current = null;
      }
      if (map.getLayer(CHAT_CTX_FOCUS_RING_LAYER)) {
        map.setPaintProperty(CHAT_CTX_FOCUS_RING_LAYER, "circle-radius", 0);
        map.setPaintProperty(CHAT_CTX_FOCUS_RING_LAYER, "circle-stroke-opacity", 0);
      }
    };
    stopPulse();
    if (prevFocusHashRef.current !== null) {
      map.setFeatureState(
        { source: CHAT_CTX_SRC, id: prevFocusHashRef.current },
        { focused: false },
      );
      prevFocusHashRef.current = null;
    }
    if (!chatFocusReport) return;
    const target = (chatContextRef.current ?? []).find((r) => r.id === chatFocusReport.id);
    if (!target) return;
    const h = hashId(target.id);
    map.setFeatureState({ source: CHAT_CTX_SRC, id: h }, { focused: true });
    prevFocusHashRef.current = h;
    // The camera stays put. Only the focused feature gets a non-zero ring.
    const PERIOD = 1300;
    const RUN_FOR = 5000; // pulse to grab attention, then hold a static ring
    let start: number | null = null;
    const setRing = (radius: number, opacity: number) => {
      const onFocused = ["boolean", ["feature-state", "focused"], false];
      map.setPaintProperty(CHAT_CTX_FOCUS_RING_LAYER, "circle-radius", [
        "case",
        onFocused,
        radius,
        0,
      ]);
      map.setPaintProperty(CHAT_CTX_FOCUS_RING_LAYER, "circle-stroke-opacity", [
        "case",
        onFocused,
        opacity,
        0,
      ]);
    };
    const tick = (t: number) => {
      if (start === null) start = t;
      if (t - start >= RUN_FOR) {
        // Settle on a steady ring.
        setRing(18, 0.7);
        focusRafRef.current = null;
        return;
      }
      const phase = ((t - start) % PERIOD) / PERIOD;
      setRing(12 + phase * 20, 0.9 * (1 - phase));
      focusRafRef.current = requestAnimationFrame(tick);
    };
    focusRafRef.current = requestAnimationFrame(tick);
    return stopPulse;
  }, [chatFocusReport]);

  useEffect(() => {
    const map = mapRef.current;
    if (!map || !mapLoadedRef.current) return;
    const source = map.getSource(LOCATIONS_SRC) as maplibregl.GeoJSONSource | undefined;
    if (!source) return;
    const features: GeoJSON.Feature<GeoJSON.Point>[] = (locationMarkers ?? [])
      .filter((m) => Number.isFinite(m.centroid.lat) && Number.isFinite(m.centroid.lng))
      .map((m) => ({
        type: "Feature",
        geometry: { type: "Point", coordinates: [m.centroid.lng, m.centroid.lat] },
        properties: { id: m.id, kind: m.kind, label: m.name },
      }));
    source.setData({ type: "FeatureCollection", features });
  }, [locationMarkers]);

  useEffect(() => {
    const map = mapRef.current;
    if (!map || !mapLoadedRef.current) return;
    const source = map.getSource(LOCATIONS_POLY_SRC) as maplibregl.GeoJSONSource | undefined;
    if (!source) return;
    const features: GeoJSON.Feature<GeoJSON.Polygon | GeoJSON.MultiPolygon>[] = (
      locationPolygons ?? []
    ).map((p) => ({
      type: "Feature",
      geometry: p.geometry,
      properties: { id: p.id, name: p.name },
    }));
    source.setData({ type: "FeatureCollection", features });
  }, [locationPolygons]);

  useEffect(() => {
    const map = mapRef.current;
    if (!map) return;
    const push = () => {
      const source = map.getSource(CRISIS_AOI_SRC) as maplibregl.GeoJSONSource | undefined;
      if (!source) return;
      source.setData({
        type: "FeatureCollection",
        features: crisisGeometry
          ? [{ type: "Feature", geometry: crisisGeometry, properties: {} }]
          : [],
      });
    };
    // The geometry often lands before `load` and never changes again, so defer
    // rather than bail. The AOI source's load handler was registered first, so
    // the source exists by the time this runs.
    if (mapLoadedRef.current) push();
    else map.once("load", push);
  }, [crisisGeometry]);

  // Fit to the picked chip's polygon, or fly to its centroid while the
  // polygon is still loading.
  useEffect(() => {
    const map = mapRef.current;
    if (!map || !mapLoadedRef.current || !focusedLocation) return;
    const { chip, polygon } = focusedLocation;
    if (polygon) {
      const bbox = polygonBounds(polygon.geometry);
      if (bbox) {
        map.fitBounds(bbox, { padding: 60, duration: 700, maxZoom: 15 });
        return;
      }
    }
    map.flyTo({
      center: [chip.centroid.lng, chip.centroid.lat],
      zoom: 12,
      speed: 1.2,
      curve: 1.4,
    });
  }, [focusedLocation]);

  useEffect(() => {
    const map = mapRef.current;
    if (!map || !mapLoadedRef.current) return;
    if (drawingActive) {
      map.getCanvas().style.cursor = "crosshair";
    } else {
      map.getCanvas().style.cursor = "";
      if (drawPointsRef.current.length > 0) setDrawPoints([]);
    }
  }, [drawingActive]);

  // The first vertex is flagged `is_first` so it renders larger as the close target.
  useEffect(() => {
    const map = mapRef.current;
    if (!map || !mapLoadedRef.current) return;
    const source = map.getSource(DRAW_SRC) as maplibregl.GeoJSONSource | undefined;
    if (!source) return;
    const features: GeoJSON.Feature[] = [];
    if (drawPoints.length >= 2) {
      features.push({
        type: "Feature",
        geometry: { type: "LineString", coordinates: drawPoints.map((p) => [p[0], p[1]]) },
        properties: { kind: "line" },
      });
    }
    for (let i = 0; i < drawPoints.length; i++) {
      features.push({
        type: "Feature",
        geometry: { type: "Point", coordinates: [drawPoints[i][0], drawPoints[i][1]] },
        properties: { kind: "point", is_first: i === 0 },
      });
    }
    source.setData({ type: "FeatureCollection", features });
  }, [drawPoints]);

  return (
    <div style={{ position: "relative", width: "100%", height: "100%", minHeight: 0 }}>
      <div ref={containerRef} style={{ width: "100%", height: "100%" }} />

      {(displayMode === "heatmap" || displayMode === "all") && heatHover && (
        <HeatHoverCard info={heatHover} container={containerRef.current} />
      )}

      {(displayMode === "buildings" || displayMode === "all") && buildingHover && (
        <BuildingHoverCard info={buildingHover} container={containerRef.current} />
      )}

      {buildingStatsTruncated && displayMode === "buildings" && (
        <div
          role="note"
          style={{
            position: "absolute",
            top: 12,
            right: 12,
            background: "var(--c-card)",
            border: "1px solid var(--c-line)",
            borderRadius: 8,
            padding: "6px 10px",
            fontSize: 11,
            color: "var(--c-warn)",
            boxShadow: "var(--shadow-1)",
            maxWidth: 240,
          }}
        >
          Showing the top {BUILDING_STATS_LIMIT} damaged buildings. Zoom in to see the rest.
        </div>
      )}

      <div
        style={{
          position: "absolute",
          top: 12,
          left: 12,
          background: "var(--c-card)",
          border: "1px solid var(--c-line)",
          borderRadius: 8,
          padding: "8px 12px",
          fontSize: 12,
          color: "var(--c-ink-2)",
          boxShadow: "var(--shadow-1)",
          maxWidth: 320,
        }}
      >
        {error ? (
          <span style={{ color: "var(--c-danger)" }}>{error}</span>
        ) : loading && !mapData ? (
          <span
            style={{
              color: "var(--c-ink-3)",
              display: "inline-flex",
              alignItems: "center",
              gap: 6,
            }}
          >
            <span className="spin" aria-hidden="true" />
            Loading reports…
          </span>
        ) : mapData ? (
          <>
            <div>
              {/* Exact: the server sums cluster totals when zoomed out. */}
              <strong>{mapData.total.toLocaleString()}</strong> report
              {mapData.total === 1 ? "" : "s"} in view
            </div>
            {/* mapData is still the previous view during a refetch; show it's
                updating so a slow recluster doesn't read as a frozen map. */}
            {loading && (
              <div
                style={{
                  color: "var(--c-ink-3)",
                  marginTop: 4,
                  display: "inline-flex",
                  alignItems: "center",
                  gap: 6,
                }}
              >
                <span className="spin" aria-hidden="true" />
                Updating…
              </div>
            )}
            {/* Semantic clustering caps at the top 50K by similarity, so the
                count is not a full census there. */}
            {mapData.capped && mapData.total_match_count != null && (
              <div style={{ color: "var(--c-warn)", marginTop: 4 }}>
                Clusters over the top {mapData.total.toLocaleString()} of{" "}
                {mapData.total_match_count.toLocaleString()} matches.
              </div>
            )}
          </>
        ) : (
          <span style={{ color: "var(--c-ink-3)" }}>—</span>
        )}
      </div>
    </div>
  );
}

// The parent re-renders on every map fetch; memo skips reconciling the map.
export const AdminReportsMap = memo(forwardRef(AdminReportsMapImpl));

function HeatHoverCard({
  info,
  container,
}: {
  info: HeatHoverInfo;
  container: HTMLDivElement | null;
}) {
  // Approximate footprint, used to flip the card away from the container edges.
  const CARD_W = 220;
  const CARD_H = 200;
  const PAD = 14;
  const w = container?.clientWidth ?? CARD_W * 3;
  const h = container?.clientHeight ?? CARD_H * 3;
  const flipRight = info.x + CARD_W + PAD > w;
  const flipBottom = info.y + CARD_H + PAD > h;
  const left = flipRight ? info.x - CARD_W - PAD : info.x + PAD;
  const top = flipBottom ? info.y - CARD_H - PAD : info.y + PAD;
  const sev = Math.max(0, Math.min(1, info.weightedSeverity));
  const sevColor = sev >= 0.66 ? "#dc2626" : sev >= 0.33 ? "#fb923c" : "#fde047";
  return (
    <div
      aria-hidden="true"
      style={{
        position: "absolute",
        left,
        top,
        width: CARD_W,
        pointerEvents: "none",
        background: "var(--c-card)",
        border: "1px solid var(--c-line)",
        borderRadius: 10,
        boxShadow: "0 8px 24px rgba(20,24,36,0.18)",
        padding: "10px 12px",
        fontSize: 12,
        color: "var(--c-ink)",
        zIndex: 5,
      }}
    >
      <div
        style={{
          fontSize: 22,
          fontWeight: 700,
          lineHeight: 1.1,
          color: "var(--c-ink)",
          marginBottom: 8,
        }}
      >
        {info.reportCount}
        <span
          style={{
            fontSize: 11,
            fontWeight: 500,
            color: "var(--c-ink-3)",
            marginInlineStart: 6,
          }}
        >
          report{info.reportCount === 1 ? "" : "s"}
        </span>
      </div>
      <div style={{ marginBottom: 10 }}>
        <div
          style={{
            display: "flex",
            alignItems: "center",
            justifyContent: "space-between",
            fontSize: 10,
            color: "var(--c-ink-3)",
            marginBottom: 3,
            letterSpacing: 0.04,
            textTransform: "uppercase",
            fontWeight: 700,
          }}
        >
          <span>severity</span>
          <span
            className="mono"
            style={{ color: "var(--c-ink-2)", fontVariantNumeric: "tabular-nums" }}
          >
            {sev.toFixed(2)}
          </span>
        </div>
        <div
          style={{
            position: "relative",
            height: 6,
            borderRadius: 999,
            background: "var(--c-line-2)",
            overflow: "hidden",
          }}
        >
          <div
            style={{
              position: "absolute",
              inset: 0,
              width: `${Math.round(sev * 100)}%`,
              background: sevColor,
              borderRadius: 999,
              transition: "width 80ms linear",
            }}
          />
        </div>
      </div>
      <div style={{ display: "flex", flexDirection: "column", gap: 3, marginBottom: 8 }}>
        <CountRow color="#b1322a" label="complete" value={info.complete} />
        <CountRow color="#b87914" label="partial" value={info.partial} />
        <CountRow color="#2f7d4f" label="minimal" value={info.minimal} />
      </div>
      <div style={{ fontSize: 10, color: "var(--c-ink-3)" }}>
        last report {relativeAgo(info.latestAt)}
      </div>
    </div>
  );
}

function BuildingHoverCard({
  info,
  container,
}: {
  info: BuildingHoverInfo;
  container: HTMLDivElement | null;
}) {
  const CARD_W = 240;
  const CARD_H = 220;
  const PAD = 14;
  const w = container?.clientWidth ?? CARD_W * 3;
  const h = container?.clientHeight ?? CARD_H * 3;
  const flipRight = info.x + CARD_W + PAD > w;
  const flipBottom = info.y + CARD_H + PAD > h;
  const left = flipRight ? info.x - CARD_W - PAD : info.x + PAD;
  const top = flipBottom ? info.y - CARD_H - PAD : info.y + PAD;
  const damageColor =
    info.latestDamageClass === "complete"
      ? "#b1322a"
      : info.latestDamageClass === "partial"
        ? "#b87914"
        : "#2f7d4f";
  const shortId =
    info.buildingId.length > 10
      ? `${info.buildingId.slice(0, 6)}…${info.buildingId.slice(-3)}`
      : info.buildingId;
  return (
    <div
      aria-hidden="true"
      style={{
        position: "absolute",
        left,
        top,
        width: CARD_W,
        pointerEvents: "none",
        background: "var(--c-card)",
        border: "1px solid var(--c-line)",
        borderRadius: 10,
        boxShadow: "0 8px 24px rgba(20,24,36,0.18)",
        padding: "10px 12px",
        fontSize: 12,
        color: "var(--c-ink)",
        zIndex: 5,
      }}
    >
      <div
        style={{
          display: "flex",
          alignItems: "baseline",
          justifyContent: "space-between",
          gap: 8,
          marginBottom: 6,
        }}
      >
        <span
          style={{
            fontSize: 10,
            fontWeight: 700,
            letterSpacing: 0.06,
            textTransform: "uppercase",
            color: "var(--c-ink-3)",
          }}
        >
          Building
        </span>
        <span
          className="mono"
          style={{
            fontSize: 11,
            color: "var(--c-ink-2)",
            fontVariantNumeric: "tabular-nums",
          }}
          title={info.buildingId}
        >
          {shortId}
        </span>
      </div>
      {info.name && (
        <div
          style={{
            fontSize: 13,
            fontWeight: 600,
            color: "var(--c-ink)",
            marginBottom: 6,
            whiteSpace: "nowrap",
            overflow: "hidden",
            textOverflow: "ellipsis",
          }}
          title={info.name}
        >
          {info.name}
        </div>
      )}
      <div
        style={{
          display: "flex",
          alignItems: "center",
          gap: 8,
          marginBottom: 8,
        }}
      >
        <span
          aria-hidden="true"
          style={{
            display: "inline-block",
            width: 10,
            height: 10,
            borderRadius: 2,
            background: damageColor,
            border: "1.5px solid #fff",
            boxShadow: "0 0 0 1px rgba(20,24,36,0.18)",
          }}
        />
        <span
          style={{
            fontSize: 13,
            fontWeight: 700,
            color: "var(--c-ink)",
            textTransform: "capitalize",
          }}
        >
          {info.latestDamageClass}
        </span>
        <span style={{ fontSize: 10, color: "var(--c-ink-3)" }}>latest status</span>
      </div>
      <div
        style={{
          fontSize: 22,
          fontWeight: 700,
          lineHeight: 1.1,
          color: "var(--c-ink)",
          marginBottom: 8,
        }}
      >
        {info.reportCount}
        <span
          style={{
            fontSize: 11,
            fontWeight: 500,
            color: "var(--c-ink-3)",
            marginInlineStart: 6,
          }}
        >
          report{info.reportCount === 1 ? "" : "s"}
        </span>
      </div>
      <div style={{ display: "flex", flexDirection: "column", gap: 3, marginBottom: 8 }}>
        <CountRow color="#b1322a" label="complete" value={info.complete} />
        <CountRow color="#b87914" label="partial" value={info.partial} />
        <CountRow color="#2f7d4f" label="minimal" value={info.minimal} />
      </div>
      <div style={{ fontSize: 10, color: "var(--c-ink-3)" }}>
        last report {relativeAgo(info.latestAt)}
      </div>
    </div>
  );
}

function CountRow({ color, label, value }: { color: string; label: string; value: number }) {
  return (
    <div style={{ display: "flex", alignItems: "center", gap: 6, fontSize: 11 }}>
      <span
        style={{
          width: 8,
          height: 8,
          borderRadius: 999,
          background: color,
          border: "1.5px solid #fff",
          boxShadow: "0 0 0 1px rgba(20,24,36,0.18)",
        }}
      />
      <span style={{ color: "var(--c-ink-2)", flex: 1 }}>{label}</span>
      <span
        className="mono"
        style={{
          fontVariantNumeric: "tabular-nums",
          fontWeight: 600,
          color: "var(--c-ink)",
        }}
      >
        {value}
      </span>
    </div>
  );
}
