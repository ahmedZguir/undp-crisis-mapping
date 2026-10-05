import maplibregl from "maplibre-gl";
import "maplibre-gl/dist/maplibre-gl.css";
import type { CSSProperties } from "react";
import { useCallback, useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import {
  type CrisisStats,
  type PublicReportDetail,
  PublicReportDetailError,
  crisisStatsKey,
  getCitizenReportHistory,
  getPublicReportDetail,
  getPublicReportsByBbox,
} from "../api/reports";
import { useCrisisPersistence } from "../hooks/useCrisisPersistence";
import { useIsRtl } from "../hooks/useIsRtl";
import { API_BASE } from "../lib/apiBase";
import { BASEMAP_ATTRIBUTION, OSM_TILE_TEMPLATE } from "../lib/basemap";
import { readResource, writeResource } from "../lib/cachedResource";
import { getClientId } from "../lib/clientId";
import { bboxOfGeometry, boundsOfRings } from "../lib/geometry";
import { DEFAULT_MAP_CENTER } from "../lib/mapDefaults";
import { REPORTS_CACHE_UPDATED_EVENT } from "../lib/reportsCache";
import { geo } from "../platform/geo";
import { tileCache } from "../platform/tileCache";
import {
  BottomSheet,
  DAMAGE_COLORS,
  FALLBACK_DAMAGE_COLOR,
  type HeatFeatureProperties,
  type SelectedFeature,
} from "./BrowseMapSheet";

const HEAT_SOURCE_ID = "heat";
const HEAT_FILL_LAYER = "heat-fill";
const HEAT_LINE_LAYER = "heat-line";
// Blue selection outline, deliberately off the damage gradient so it reads as UI, not severity.
// "__none__" filter hides it.
const HEAT_HIGHLIGHT_LAYER = "heat-highlight";
const MY_REPORTS_SOURCE = "my-reports";
const MY_REPORTS_LAYER = "my-reports-circle";
// Public buildings-mode layer.
const BUILDINGS_SOURCE_ID = "public-buildings";
const BUILDINGS_LAYER_ID = "public-buildings-circle";
// Public `full`-mode bbox layer.
const PUBLIC_REPORTS_SOURCE_ID = "public-reports";
const PUBLIC_REPORTS_LAYER_ID = "public-reports-circle";
// Max features per bbox fetch (backend caps at 2000); larger payloads stutter on slow devices.
const PUBLIC_REPORTS_BBOX_LIMIT = 200;
// 300ms absorbs the rare double `moveend` on quick flick-pans.
const PUBLIC_REPORTS_MOVEEND_DEBOUNCE_MS = 300;
// Debounce for the stats refresh when the tab becomes visible again.
const VISIBILITY_REFRESH_DEBOUNCE_MS = 300;
// Initial-viewport fit (crisis geometry or rendered heat cells).
const FIT_OPTS = { padding: 40, animate: false, maxZoom: 14 } as const;
const DEFAULT_ZOOM = 13;

// Circle paint shared by the my-reports, public-reports and buildings layers:
// zoom-scaled radius, fill by damage class; only the stroke differs.
function damageCirclePaint(
  stroke: string,
  strokeWidth: number,
): maplibregl.CircleLayerSpecification["paint"] {
  return {
    "circle-radius": ["interpolate", ["linear"], ["zoom"], 6, 7, 12, 11, 18, 16],
    "circle-color": [
      "match",
      ["get", "damage_class"],
      "minimal",
      DAMAGE_COLORS.minimal,
      "partial",
      DAMAGE_COLORS.partial,
      "complete",
      DAMAGE_COLORS.complete,
      FALLBACK_DAMAGE_COLOR,
    ],
    "circle-stroke-color": stroke,
    "circle-stroke-width": strokeWidth,
  };
}

// Pointer cursor while hovering a clickable layer.
function bindPointerCursor(map: maplibregl.Map, layerId: string): void {
  map.on("mouseenter", layerId, () => {
    map.getCanvas().style.cursor = "pointer";
  });
  map.on("mouseleave", layerId, () => {
    map.getCanvas().style.cursor = "";
  });
}

// True when a my-report pin sits under the click; those pins are on top, so
// the lower layers ignore the click.
const hitsMyReport = (map: maplibregl.Map, e: maplibregl.MapMouseEvent): boolean =>
  map.queryRenderedFeatures(e.point, { layers: [MY_REPORTS_LAYER] }).length > 0;

interface BrowseMapProps {
  onBack: () => void;
  onChangeCrisis?: () => void;
}

export function BrowseMap({ onBack, onChangeCrisis }: BrowseMapProps) {
  const { t } = useTranslation();
  const isRtl = useIsRtl();
  const mapContainerRef = useRef<HTMLDivElement>(null);
  const mapRef = useRef<maplibregl.Map | null>(null);
  const { stored: crisis } = useCrisisPersistence();
  const crisisId = crisis?.id ?? null;
  const crisisName = crisis?.name ?? null;
  const geometry = crisis?.geometry ?? null;
  // Defaults to aggregate_view so a persisted crisis blob that predates the field still renders.
  const publicVisibility = crisis?.public_visibility ?? "aggregate_view";

  // Hydrate from the warm cache so the sheet opens with last-known counts instead of flashing zero.
  const [stats, setStats] = useState<CrisisStats | null>(() =>
    crisisId ? readResource<CrisisStats>(crisisStatsKey(crisisId)) : null,
  );
  const [statsLoading, setStatsLoading] = useState(false);
  const [crisisUnavailable, setCrisisUnavailable] = useState(false);
  // Tapped map feature, rendered in the bottom sheet.
  const [selected, setSelected] = useState<SelectedFeature | null>(null);
  // Collapsed by default; lifted here so map click handlers can expand it.
  const [sheetOpen, setSheetOpen] = useState(false);
  // Token guards the async full-mode detail fetch so a slow earlier response cannot overwrite a newer tap.
  const selectionTokenRef = useRef(0);
  // Session cache so re-tapping the same pin skips the API round trip.
  const detailCacheRef = useRef(new Map<string, PublicReportDetail>());

  const refreshStats = useCallback(async () => {
    if (!crisisId) return;
    // `none` mode: the stats endpoint 404s by design, so skip it to keep the sheet out of the unavailable branch.
    if (publicVisibility === "none") {
      setStats(null);
      return;
    }
    setStatsLoading(true);
    try {
      const res = await fetch(`${API_BASE}/crises/${crisisId}/stats`);
      if (res.status === 404) {
        setCrisisUnavailable(true);
        setStats(null);
      } else if (res.ok) {
        const fresh = (await res.json()) as CrisisStats;
        setStats(fresh);
        writeResource(crisisStatsKey(crisisId), fresh);
        setCrisisUnavailable(false);
      }
    } finally {
      setStatsLoading(false);
    }
  }, [crisisId, publicVisibility]);

  useEffect(() => {
    void refreshStats();
  }, [refreshStats]);

  useEffect(() => {
    let t: ReturnType<typeof setTimeout> | null = null;
    const onVisible = () => {
      if (document.visibilityState !== "visible") return;
      if (t != null) clearTimeout(t);
      t = setTimeout(() => void refreshStats(), VISIBILITY_REFRESH_DEBOUNCE_MS);
    };
    document.addEventListener("visibilitychange", onVisible);
    return () => {
      document.removeEventListener("visibilitychange", onVisible);
      if (t != null) clearTimeout(t);
    };
  }, [refreshStats]);

  // Force heat tiles to refetch after an outbox flush or reconnect; cached tiles keep pre-sync counts.
  const reloadHeatTiles = useCallback(() => {
    const map = mapRef.current;
    if (!map || !crisisId) return;
    const src = map.getSource(HEAT_SOURCE_ID) as
      | (maplibregl.VectorTileSource & { setTiles?: (t: string[]) => void })
      | undefined;
    const url = tileCache.vectorTileUrl(
      `${API_BASE}/crises/${crisisId}/tiles/heat/{z}/{x}/{y}.pbf`,
    );
    src?.setTiles?.([url]);
  }, [crisisId]);

  // Refetch public buildings after an outbox flush instead of waiting out the 15s Cache-Control window.
  // No-op outside `buildings` mode (the source only exists there).
  const reloadBuildingsLayer = useCallback(() => {
    const map = mapRef.current;
    if (!map || !crisisId) return;
    const src = map.getSource(BUILDINGS_SOURCE_ID) as maplibregl.GeoJSONSource | undefined;
    src?.setData(`${API_BASE}/crises/${crisisId}/public/buildings`);
  }, [crisisId]);

  // Own reports stay visible at every zoom (the heat layer can hide single submissions).
  const refreshMyReports = useCallback(async () => {
    const map = mapRef.current;
    if (!map || !crisisId) return;
    try {
      const clientId = await getClientId();
      const history = await getCitizenReportHistory(clientId);
      const features = history.items
        .filter(
          (r): r is typeof r & { location: NonNullable<typeof r.location> } =>
            r.crisis_id === crisisId && r.location != null,
        )
        .map((r) => ({
          type: "Feature" as const,
          geometry: {
            type: "Point" as const,
            coordinates: [r.location.lng, r.location.lat],
          },
          properties: {
            damage_class: r.damage_class,
            photo_url: r.photo_url,
          },
        }));
      const collection = { type: "FeatureCollection" as const, features };
      const src = map.getSource(MY_REPORTS_SOURCE) as maplibregl.GeoJSONSource | undefined;
      if (src) src.setData(collection);
    } catch {}
  }, [crisisId]);

  // On outbox flush, refresh everything so newly synced reports appear.
  useEffect(() => {
    const onSynced = () => {
      void refreshStats();
      void refreshMyReports();
      reloadHeatTiles();
      reloadBuildingsLayer();
    };
    window.addEventListener(REPORTS_CACHE_UPDATED_EVENT, onSynced);
    window.addEventListener("online", onSynced);
    return () => {
      window.removeEventListener(REPORTS_CACHE_UPDATED_EVENT, onSynced);
      window.removeEventListener("online", onSynced);
    };
  }, [refreshStats, refreshMyReports, reloadHeatTiles, reloadBuildingsLayer]);

  useEffect(() => {
    if (!mapContainerRef.current) return;
    // Register pmtiles:// (and native nativetile://) protocols here, in the lazy map chunk, to keep
    // maplibre off the first-paint path. Idempotent.
    tileCache.registerMapProtocols(maplibregl);
    const map = new maplibregl.Map({
      container: mapContainerRef.current,
      style: {
        version: 8,
        sources: {
          osm: {
            type: "raster",
            tiles: [tileCache.rasterTileUrl(OSM_TILE_TEMPLATE)],
            tileSize: 256,
            attribution: BASEMAP_ATTRIBUTION,
          },
        },
        layers: [{ id: "osm-tiles", type: "raster", source: "osm" }],
      },
      center: DEFAULT_MAP_CENTER,
      zoom: DEFAULT_ZOOM,
    });
    mapRef.current = map;

    map.on("load", () => {
      if (crisisId && !crisisUnavailable) {
        // Own reports are added in every mode (incl. `none`) so citizens still see their submissions.
        map.addSource(MY_REPORTS_SOURCE, {
          type: "geojson",
          data: { type: "FeatureCollection", features: [] },
        });
        map.addLayer({
          id: MY_REPORTS_LAYER,
          type: "circle",
          source: MY_REPORTS_SOURCE,
          paint: damageCirclePaint("#1d4ed8", 3),
        });

        map.on("click", MY_REPORTS_LAYER, (e) => {
          const f = e.features?.[0];
          if (!f) return;
          const p = f.properties as unknown as {
            damage_class: string;
            photo_url?: string | null;
          };
          selectionTokenRef.current += 1;
          setSelected({
            kind: "mine",
            damageClass: p.damage_class,
            photoUrl: p.photo_url,
          });
          setSheetOpen(true);
        });
        bindPointerCursor(map, MY_REPORTS_LAYER);

        // Mode-specific data layers.
        if (publicVisibility === "aggregate_view") {
          // Hexes use their true MVT geometry so colour sits exactly where reports were aggregated.
          map.addSource(HEAT_SOURCE_ID, {
            type: "vector",
            tiles: [
              tileCache.vectorTileUrl(`${API_BASE}/crises/${crisisId}/tiles/heat/{z}/{x}/{y}.pbf`),
            ],
            minzoom: 0,
            // Cap at z14 and overzoom: past ~z15 H3 cells span several tiles, causing flicker and missing
            // chunks.
            maxzoom: 14,
          });
          map.addLayer({
            id: HEAT_FILL_LAYER,
            type: "fill",
            source: HEAT_SOURCE_ID,
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
              "fill-opacity": 0.4,
              "fill-antialias": true,
            },
          });
          map.addLayer({
            id: HEAT_LINE_LAYER,
            type: "line",
            source: HEAT_SOURCE_ID,
            "source-layer": "heat",
            paint: {
              "line-color": "rgba(124, 45, 18, 0.45)",
              "line-width": 1,
            },
          });
          // Hidden until a hex is tapped (see the selection effect below).
          map.addLayer({
            id: HEAT_HIGHLIGHT_LAYER,
            type: "line",
            source: HEAT_SOURCE_ID,
            "source-layer": "heat",
            paint: {
              "line-color": "#2563eb",
              "line-width": 3,
            },
            filter: ["==", ["get", "cell_id"], "__none__"],
          });

          const onHexClick = (
            e: maplibregl.MapMouseEvent & { features?: maplibregl.MapGeoJSONFeature[] },
          ) => {
            if (hitsMyReport(map, e)) return;
            const feature = e.features?.[0];
            if (!feature) return;
            const props = feature.properties as unknown as HeatFeatureProperties | null;
            if (!props) return;
            selectionTokenRef.current += 1;
            setSelected({ kind: "hex", props });
            setSheetOpen(true);
          };
          map.on("click", HEAT_FILL_LAYER, onHexClick);
          bindPointerCursor(map, HEAT_FILL_LAYER);
        } else if (publicVisibility === "full") {
          map.addSource(PUBLIC_REPORTS_SOURCE_ID, {
            type: "geojson",
            data: { type: "FeatureCollection", features: [] },
          });
          map.addLayer({
            id: PUBLIC_REPORTS_LAYER_ID,
            type: "circle",
            source: PUBLIC_REPORTS_SOURCE_ID,
            paint: damageCirclePaint("#fff", 2),
          });

          // Remember the last bbox key so a no-op `moveend` (e.g. programmatic flyTo) does not refetch.
          let lastBboxKey = "";
          let moveTimer: ReturnType<typeof setTimeout> | null = null;
          const refetchPublicReports = async () => {
            if (!crisisId) return;
            const bounds = map.getBounds();
            const west = bounds.getWest();
            const south = bounds.getSouth();
            const east = bounds.getEast();
            const north = bounds.getNorth();
            // Antimeridian-crossing viewports (west >= east) are rejected by the backend; skip them.
            if (west >= east || south >= north) return;
            const bbox = `${west},${south},${east},${north}`;
            // Quantise so float drift on a stopped viewport does not re-fire fetches forever.
            const key = `${west.toFixed(6)},${south.toFixed(6)},${east.toFixed(6)},${north.toFixed(6)}`;
            if (key === lastBboxKey) return;
            lastBboxKey = key;
            try {
              const resp = await getPublicReportsByBbox(crisisId, bbox, PUBLIC_REPORTS_BBOX_LIMIT);
              const features = resp.items
                .filter(
                  (
                    item,
                  ): item is typeof item & {
                    map_point: NonNullable<typeof item.map_point>;
                  } => item.map_point != null,
                )
                .map((item) => ({
                  type: "Feature" as const,
                  geometry: {
                    type: "Point" as const,
                    coordinates: [item.map_point.lng, item.map_point.lat],
                  },
                  properties: {
                    id: item.id,
                    damage_class: item.damage_class,
                  },
                }));
              const src = map.getSource(PUBLIC_REPORTS_SOURCE_ID) as
                | maplibregl.GeoJSONSource
                | undefined;
              src?.setData({ type: "FeatureCollection", features });
            } catch {
              // Best-effort; the next gesture re-fires the fetch.
            }
          };
          const onMoveEnd = () => {
            if (moveTimer != null) clearTimeout(moveTimer);
            moveTimer = setTimeout(() => {
              void refetchPublicReports();
            }, PUBLIC_REPORTS_MOVEEND_DEBOUNCE_MS);
          };
          map.on("moveend", onMoveEnd);
          // Explicit kick for when the map opens at the default centre and never moves.
          void refetchPublicReports();

          map.on("click", PUBLIC_REPORTS_LAYER_ID, (e) => {
            if (hitsMyReport(map, e)) return;
            const f = e.features?.[0];
            if (!f) return;
            const props = f.properties as unknown as {
              id: string;
              damage_class: string;
            };
            selectionTokenRef.current += 1;
            const token = selectionTokenRef.current;
            const cached = detailCacheRef.current.get(props.id);
            if (cached) {
              setSelected({ kind: "full", detail: cached });
              setSheetOpen(true);
              return;
            }
            setSelected({ kind: "full-loading" });
            setSheetOpen(true);
            (async () => {
              try {
                const detail = await getPublicReportDetail(props.id);
                detailCacheRef.current.set(props.id, detail);
                if (selectionTokenRef.current !== token) return;
                setSelected({ kind: "full", detail });
              } catch (err) {
                if (selectionTokenRef.current !== token) return;
                // 404: the crisis left `full` mode or the row was hidden since the bbox fetch; show the
                // explicit "no longer visible" copy rather than a generic error.
                const reason =
                  err instanceof PublicReportDetailError && err.status === 404 ? "gone" : "error";
                setSelected({ kind: "full-error", reason });
              }
            })();
          });
          bindPointerCursor(map, PUBLIC_REPORTS_LAYER_ID);
        } else if (publicVisibility === "buildings") {
          // Per-building public layer; never show `building_id` in the popup. Paint mirrors the my-reports layer.
          map.addSource(BUILDINGS_SOURCE_ID, {
            type: "geojson",
            data: `${API_BASE}/crises/${crisisId}/public/buildings`,
          });
          map.addLayer({
            id: BUILDINGS_LAYER_ID,
            type: "circle",
            source: BUILDINGS_SOURCE_ID,
            paint: damageCirclePaint("#fff", 2),
          });

          map.on("click", BUILDINGS_LAYER_ID, (e) => {
            if (hitsMyReport(map, e)) return;
            const f = e.features?.[0];
            if (!f) return;
            const props = f.properties as unknown as {
              damage_class: string;
              report_count: number;
            };
            selectionTokenRef.current += 1;
            setSelected({
              kind: "building",
              damageClass: props.damage_class,
              reportCount: props.report_count,
            });
            setSheetOpen(true);
          });
          bindPointerCursor(map, BUILDINGS_LAYER_ID);
        }

        // My-reports pins sit on top so they are always visible and take clicks first.
        map.moveLayer(MY_REPORTS_LAYER);

        // Centre on crisis geometry, else (aggregate_view only) on the rendered heat tiles.
        const bounds = geometry ? bboxOfGeometry(geometry) : null;
        if (bounds) {
          map.fitBounds(bounds, FIT_OPTS);
        } else if (publicVisibility === "aggregate_view") {
          fitToHeatFeatures(map);
        }

        void refreshMyReports();
      }
    });

    return () => {
      map.remove();
      mapRef.current = null;
    };
  }, [crisisId, geometry, crisisUnavailable, refreshMyReports, publicVisibility]);

  // biome-ignore lint/correctness/useExhaustiveDependencies: reset trigger only.
  useEffect(() => {
    setSelected(null);
  }, [crisisId, publicVisibility]);

  // Drive the on-map hex highlight from the current selection. The layer only
  // exists in aggregate_view; guard with getLayer so the other modes no-op.
  useEffect(() => {
    const map = mapRef.current;
    if (!map || !map.getLayer(HEAT_HIGHLIGHT_LAYER)) return;
    const cellId = selected?.kind === "hex" ? selected.props.cell_id : null;
    map.setFilter(HEAT_HIGHLIGHT_LAYER, ["==", ["get", "cell_id"], cellId ?? "__none__"]);
  }, [selected]);

  // Later refreshes come from REPORTS_CACHE_UPDATED_EVENT / online, not every stats poll.
  useEffect(() => {
    void refreshMyReports();
  }, [refreshMyReports]);

  return (
    <div
      style={{
        position: "relative",
        height: "100svh",
        fontFamily: "var(--font-ui)",
        color: "var(--c-ink)",
        background: "var(--c-surface)",
        overflow: "hidden",
      }}
    >
      <div ref={mapContainerRef} style={{ position: "absolute", inset: 0 }} />

      {/* Offset below the shell's fixed connection indicator. */}
      <div
        style={{
          position: "absolute",
          top: "calc(max(env(safe-area-inset-top, 12px), 12px) + 24px)",
          left: "calc(12px + env(safe-area-inset-left))",
          right: "calc(12px + env(safe-area-inset-right))",
          display: "flex",
          alignItems: "center",
          gap: 8,
          zIndex: 5,
        }}
      >
        <button
          type="button"
          onClick={onBack}
          aria-label={t("browseMap.back")}
          style={{ ...floatBtn, borderRadius: 0, background: "transparent", boxShadow: "none" }}
        >
          <svg
            aria-hidden="true"
            width="17"
            height="17"
            viewBox="0 0 16 16"
            fill="none"
            style={isRtl ? { transform: "scaleX(-1)" } : undefined}
          >
            <path
              d="M10 4L6 8l4 4"
              stroke="currentColor"
              strokeWidth="1.8"
              strokeLinecap="round"
              strokeLinejoin="round"
            />
          </svg>
        </button>
        <div
          style={{
            flex: 1,
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            gap: 8,
            minWidth: 0,
          }}
        >
          {crisisName ? (
            <button
              type="button"
              onClick={onChangeCrisis}
              disabled={!onChangeCrisis}
              style={{
                maxWidth: 280,
                minWidth: 0,
                display: "inline-flex",
                alignItems: "center",
                gap: 8,
                background: "rgba(177,50,42,0.95)",
                color: "#fff",
                padding: "8px 12px",
                borderRadius: 999,
                border: 0,
                fontSize: 11,
                fontWeight: 700,
                letterSpacing: "0.06em",
                textTransform: "uppercase",
                boxShadow: "var(--c-shadow-2)",
                cursor: onChangeCrisis ? "pointer" : "default",
              }}
            >
              <span
                aria-hidden
                style={{
                  width: 7,
                  height: 7,
                  borderRadius: 999,
                  flexShrink: 0,
                  background: "var(--c-card)",
                  animation: "pulseDot 1.4s infinite",
                }}
              />
              <span
                style={{
                  overflow: "hidden",
                  textOverflow: "ellipsis",
                  whiteSpace: "nowrap",
                }}
              >
                {crisisName}
              </span>
              {stats != null && (
                <span
                  style={{
                    flexShrink: 0,
                    display: "inline-flex",
                    alignItems: "center",
                    gap: 4,
                    background: "rgba(255,255,255,0.22)",
                    borderRadius: 999,
                    padding: "2px 8px",
                    letterSpacing: 0,
                    fontVariantNumeric: "tabular-nums",
                  }}
                >
                  <b style={{ fontWeight: 800, fontSize: 11 }}>{stats.total_reports}</b>
                  <span style={{ fontSize: 9, fontWeight: 700, opacity: 0.85 }}>
                    {t("browseMap.totalReports")}
                  </span>
                </span>
              )}
              {onChangeCrisis && (
                <span aria-hidden style={{ fontSize: 10, opacity: 0.9, flexShrink: 0 }}>
                  ▾
                </span>
              )}
            </button>
          ) : onChangeCrisis ? (
            <button
              type="button"
              onClick={onChangeCrisis}
              style={{
                display: "inline-flex",
                alignItems: "center",
                gap: 6,
                background: "var(--c-card)",
                color: "var(--c-blue-700)",
                padding: "8px 16px",
                borderRadius: 999,
                border: "1px solid var(--c-line)",
                fontSize: 12,
                fontWeight: 700,
                boxShadow: "var(--c-shadow-2)",
                cursor: "pointer",
              }}
            >
              {t("home.selectCrisis")}
            </button>
          ) : null}
        </div>
      </div>

      <div
        style={{
          position: "absolute",
          top: "calc(max(env(safe-area-inset-top, 12px), 12px) + 80px)",
          right: "calc(12px + env(safe-area-inset-right))",
          zIndex: 5,
          display: "flex",
          flexDirection: "column",
          gap: 8,
        }}
      >
        <button
          type="button"
          onClick={() => mapRef.current?.zoomIn()}
          aria-label={t("browseMap.zoomIn")}
          style={{ ...floatBtn, color: "var(--c-ink-2)" }}
        >
          <svg aria-hidden="true" width="17" height="17" viewBox="0 0 16 16" fill="none">
            <path
              d="M8 3v10M3 8h10"
              stroke="currentColor"
              strokeWidth="1.8"
              strokeLinecap="round"
            />
          </svg>
        </button>
        <button
          type="button"
          onClick={() => mapRef.current?.zoomOut()}
          aria-label={t("browseMap.zoomOut")}
          style={{ ...floatBtn, color: "var(--c-ink-2)" }}
        >
          <svg aria-hidden="true" width="17" height="17" viewBox="0 0 16 16" fill="none">
            <path d="M3 8h10" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" />
          </svg>
        </button>
        <button
          type="button"
          onClick={() => {
            if (!geo.isAvailable() || !mapRef.current) return;
            geo
              .getCurrentPosition({ enableHighAccuracy: true, maximumAge: 60_000, timeout: 6000 })
              .then((pos) => {
                mapRef.current?.flyTo({
                  center: [pos.longitude, pos.latitude],
                  zoom: 15,
                });
              })
              .catch(() => {});
          }}
          aria-label={t("browseMap.locate")}
          style={{ ...floatBtn, color: "var(--c-blue-700)" }}
        >
          <svg
            aria-hidden="true"
            width="18"
            height="18"
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            strokeWidth="2"
            strokeLinecap="round"
            strokeLinejoin="round"
          >
            <circle cx="12" cy="12" r="3" />
            <path d="M12 2v3M12 19v3M2 12h3M19 12h3" />
          </svg>
        </button>
        <button
          type="button"
          onClick={() => void refreshStats()}
          disabled={!crisisId || statsLoading}
          aria-label={t("browseMap.refresh")}
          style={{
            ...floatBtn,
            color: "var(--c-blue-700)",
            opacity: statsLoading ? 0.5 : 1,
            cursor: crisisId && !statsLoading ? "pointer" : "not-allowed",
          }}
        >
          <svg aria-hidden="true" width="17" height="17" viewBox="0 0 16 16" fill="none">
            <path
              d="M13.5 8a5.5 5.5 0 1 1-1.61-3.89M13.5 3v2.5H11"
              stroke="currentColor"
              strokeWidth="1.6"
              strokeLinecap="round"
              strokeLinejoin="round"
            />
          </svg>
        </button>
      </div>

      {crisisUnavailable && <MapBanner text={t("browseMap.crisisUnavailable")} />}

      {crisisId && !crisisUnavailable && publicVisibility === "none" && (
        <MapBanner text={t("browseMap.publicViewDisabled")} />
      )}

      {/* `none` mode has no sheet so users never see an empty/stale stats panel. */}
      {crisisId && publicVisibility !== "none" && (
        <BottomSheet
          crisisName={crisisName}
          stats={stats}
          unavailable={crisisUnavailable}
          loading={statsLoading}
          open={sheetOpen}
          onOpenChange={setSheetOpen}
          selected={selected}
          onClearSelected={() => setSelected(null)}
          t={t}
        />
      )}
    </div>
  );
}

const floatBtn: CSSProperties = {
  width: 40,
  height: 40,
  borderRadius: 999,
  background: "var(--c-card)",
  border: 0,
  display: "grid",
  placeItems: "center",
  boxShadow: "var(--c-shadow-2)",
  color: "var(--c-ink-2)",
  cursor: "pointer",
  flexShrink: 0,
};

function MapBanner({ text }: { text: string }) {
  return (
    <div
      style={{
        position: "absolute",
        top: "calc(max(env(safe-area-inset-top, 12px), 12px) + 110px)",
        insetInline: 12,
        zIndex: 5,
        padding: "10px 14px",
        background: "var(--c-danger-bg)",
        border: "1px solid var(--c-danger)",
        borderRadius: 12,
        color: "var(--c-danger)",
        fontSize: 13,
        fontWeight: 600,
        boxShadow: "0 2px 8px rgba(0,0,0,0.08)",
        display: "flex",
        alignItems: "center",
        gap: 10,
      }}
    >
      <span style={{ flex: 1 }}>{text}</span>
    </div>
  );
}

function fitToHeatFeatures(map: maplibregl.Map): void {
  const tryFit = () => {
    const features = map.queryRenderedFeatures(undefined, { layers: [HEAT_FILL_LAYER] });
    if (features.length === 0) return;
    const polys: number[][][][] = [];
    for (const f of features) {
      const geom = f.geometry;
      if (geom.type === "Polygon") polys.push(geom.coordinates);
      else if (geom.type === "MultiPolygon") polys.push(...geom.coordinates);
    }
    const bounds = boundsOfRings(polys);
    if (bounds) map.fitBounds(bounds, FIT_OPTS);
  };
  map.once("idle", tryFit);
}
