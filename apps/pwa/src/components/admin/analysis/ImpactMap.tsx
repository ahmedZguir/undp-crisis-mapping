import maplibregl from "maplibre-gl";
import "maplibre-gl/dist/maplibre-gl.css";
import { type ReactNode, useEffect, useMemo, useRef, useState } from "react";
import { useAppliedTheme } from "../../../hooks/useAppliedTheme";
import { BASEMAP_ATTRIBUTION, basemapTiles } from "../../../lib/basemap";
import type { AnalysisBBox, AnalysisCell, AnalysisDistrict } from "../../../types/admin";
import { formatCountRange, formatUsd, formatUsdRange } from "./format";

// Four layers, two families. The impact layers (dollars / people) and the
// blind-spot layer mean opposite things: observed impact says "send response
// here", the coverage gap says "send recon here". The districts layer is the
// PDF report's choropleth, brought on-map so a coordinator can click straight
// from the priority list to the place. Colour can only ever drive one quantity,
// so the toggle picks it; the hover popup always shows the full cell estimate.
export type ImpactMetric = "dollars" | "people" | "blindspots" | "districts";

const HALF = 0.01; // half a 0.02° cell, for the square footprint

// Sequential ramps, one per cell metric, kept distinct from the dashboard's heat
// (yellow->red), severity (red/amber/green), and cluster (blue->purple) palettes
// so the encodings never read as the same thing. Darkest = worst.
const RAMP: Record<"dollars" | "people", string[]> = {
  dollars: ["#fee5d9", "#fcae91", "#fb6a4a", "#de2d26", "#a50f15"],
  people: ["#f2f0f7", "#cbc9e2", "#9e9ac8", "#756bb1", "#54278f"],
};
const PENDING_COLOR = "#94a3b8"; // LitPop absent: dollars can't be valued here
const BLIND_FILL = "#e6550d"; // one flat colour: a cell is a blind spot or it isn't

// District choropleth, matched to the PDF report (apps/api .../analysis/charts.py
// `_ramp`): peach -> deep red by severe-report volume. Kept here as RGB so the
// fill can be interpolated client-side per district.
const DIST_LOW: [number, number, number] = [253, 232, 224]; // #fde8e0
const DIST_HIGH: [number, number, number] = [179, 38, 30]; // #b3261e
const SELECT_COLOR = "#006eb6"; // accent: the clicked district's outline

const CELLS_SRC = "impact-cells";
const CELLS_FILL = "impact-cells-fill";
const CELLS_LINE = "impact-cells-line";
const TOP_SRC = "impact-top-cells";
const TOP_LINE = "impact-top-cells-line";
const DIST_SRC = "impact-districts";
const DIST_FILL = "impact-districts-fill";
const DIST_LINE = "impact-districts-line";
const SEL_SRC = "impact-selected-district";
const SEL_LINE = "impact-selected-district-line";
const SELCELL_SRC = "impact-selected-cell";
const SELCELL_FILL = "impact-selected-cell-fill";
const SELCELL_LINE = "impact-selected-cell-line";
const AOI_SRC = "impact-aoi";
const AOI_CASING = "impact-aoi-casing";
const AOI_LINE = "impact-aoi-line";

// Stable key for a cell, shared by the hover lookup, the ranked rail's
// active-row state, and the on-map highlight so all three agree on identity.
function cellKey(c: AnalysisCell): string {
  return `${c.lon.toFixed(4)},${c.lat.toFixed(4)}`;
}

interface HoverState {
  x: number;
  y: number;
  cw: number;
  ch: number;
  cell?: AnalysisCell;
  district?: AnalysisDistrict;
}

// The value colour keys off for a cell under the active metric, or null when the
// cell has no value for that metric (filtered out of the layer entirely).
function metricValue(c: AnalysisCell, metric: ImpactMetric): number | null {
  if (metric === "people") return c.affected_buildings > 0 ? c.displaced_high : null;
  if (metric === "dollars") {
    if (c.economic_available && c.economic_high_usd != null && c.economic_high_usd > 0) {
      return c.economic_high_usd;
    }
    // Damaged but unvalued (LitPop absent) → still drawn, as "pending".
    return c.affected_buildings > 0 ? 0 : null;
  }
  // blindspots: ONLY the confirmed two-condition cells, so the layer matches
  // the KPI count exactly. Plain under-reporting is not drawn.
  return c.blind_spot ? 1 : null;
}

// Quantile breaks over the positive values, so one outlier cell can't wash the
// whole map into the lightest bin. Returns colors.length-1 ascending cut points.
function quantileBreaks(values: number[], bins: number): number[] {
  const sorted = values.filter((v) => v > 0).sort((a, b) => a - b);
  if (sorted.length === 0) return [];
  const breaks: number[] = [];
  for (let i = 1; i < bins; i++) {
    breaks.push(sorted[Math.min(sorted.length - 1, Math.floor((i / bins) * sorted.length))]);
  }
  return breaks;
}

function rampColor(value: number, breaks: number[], colors: string[]): string {
  let i = 0;
  while (i < breaks.length && value > breaks[i]) i++;
  return colors[Math.min(i, colors.length - 1)];
}

function lerpColor(a: [number, number, number], b: [number, number, number], t: number): string {
  const u = Math.max(0, Math.min(1, t));
  const ch = (i: number) => Math.round(a[i] + (b[i] - a[i]) * u);
  return `rgb(${ch(0)}, ${ch(1)}, ${ch(2)})`;
}

function cellPolygon(c: AnalysisCell): GeoJSON.Position[][] {
  const { lon, lat } = c;
  return [
    [
      [lon - HALF, lat - HALF],
      [lon + HALF, lat - HALF],
      [lon + HALF, lat + HALF],
      [lon - HALF, lat + HALF],
      [lon - HALF, lat - HALF],
    ],
  ];
}

// Bounding box of a district's GeoJSON polygon/multipolygon, for fly-to framing.
function geomBounds(
  geom: GeoJSON.Polygon | GeoJSON.MultiPolygon,
): maplibregl.LngLatBoundsLike | null {
  let minLon = Number.POSITIVE_INFINITY;
  let minLat = Number.POSITIVE_INFINITY;
  let maxLon = Number.NEGATIVE_INFINITY;
  let maxLat = Number.NEGATIVE_INFINITY;
  const visit = (ring: GeoJSON.Position[]) => {
    for (const [lon, lat] of ring) {
      if (lon < minLon) minLon = lon;
      if (lon > maxLon) maxLon = lon;
      if (lat < minLat) minLat = lat;
      if (lat > maxLat) maxLat = lat;
    }
  };
  if (geom.type === "Polygon") for (const ring of geom.coordinates) visit(ring);
  else for (const poly of geom.coordinates) for (const ring of poly) visit(ring);
  if (!Number.isFinite(minLon)) return null;
  return [
    [minLon, minLat],
    [maxLon, maxLat],
  ];
}

// The crisis AOI envelope, padded outward so the camera stays pinned to the
// disaster: the map can zoom in and pan within, but never drift off the area.
// The margin leaves room for the fit-padding and a little breathing space so
// the boundary ring isn't flush against the viewport edge.
function maxBoundsFor(bbox: AnalysisBBox): maplibregl.LngLatBoundsLike {
  const dx = Math.max((bbox.max_lon - bbox.min_lon) * 0.4, 0.02);
  const dy = Math.max((bbox.max_lat - bbox.min_lat) * 0.4, 0.02);
  return [
    [bbox.min_lon - dx, bbox.min_lat - dy],
    [bbox.max_lon + dx, bbox.max_lat + dy],
  ];
}

// Tooltip placement that stays inside the canvas: down-right of the cursor by
// default, flipping left / up near the right / bottom edge.
const TIP_W = 234;
const TIP_H = 116;
function tipStyle(h: HoverState): { left: number; top: number } {
  const left = h.x + TIP_W + 14 > h.cw ? Math.max(8, h.x - TIP_W - 14) : h.x + 14;
  const top = h.y + TIP_H + 14 > h.ch ? Math.max(8, h.y - TIP_H - 14) : h.y + 14;
  return { left, top };
}

interface Props {
  cells: AnalysisCell[];
  districts: AnalysisDistrict[];
  bbox: AnalysisBBox | null;
  geometry: GeoJSON.Polygon | GeoJSON.MultiPolygon | null;
  metric: ImpactMetric;
  onMetricChange: (m: ImpactMetric) => void;
  selectedDistrictId: string | null;
  onSelectDistrict: (id: string | null) => void;
}

export function ImpactMap({
  cells,
  districts,
  bbox,
  geometry,
  metric,
  onMetricChange,
  selectedDistrictId,
  onSelectDistrict,
}: Props) {
  const containerRef = useRef<HTMLDivElement>(null);
  const mapRef = useRef<maplibregl.Map | null>(null);
  const loadedRef = useRef(false);
  // Active theme drives the basemap tiles (read via a ref in the mount-once
  // effect so the map isn't recreated; the swap effect below reacts to changes).
  const theme = useAppliedTheme();
  const themeRef = useRef(theme);
  themeRef.current = theme;
  // Lookups so the hover/click handlers (bound once in the mount effect) can
  // recover the full record from a feature without stashing every field as a
  // map property. Declared before the mount effect that closes over them.
  const cellLookupRef = useRef(new Map<string, AnalysisCell>());
  const districtLookupRef = useRef(new Map<string, AnalysisDistrict>());
  const onSelectRef = useRef(onSelectDistrict);
  onSelectRef.current = onSelectDistrict;
  const [hover, setHover] = useState<HoverState | null>(null);
  // Which ranked cell the coordinator clicked, highlighted on the map and
  // marked active in the rail.
  const [selectedCellKey, setSelectedCellKey] = useState<string | null>(null);

  const isDistricts = metric === "districts";

  // Cells that carry a value under the active cell metric, worst-first. Drives
  // the fill colours and the top-cell outlines together.
  const { fc, ranked, anyPending } = useMemo(() => {
    if (isDistricts) {
      return { fc: emptyFc(), ranked: [] as AnalysisCell[], anyPending: false };
    }
    const scored = cells
      .map((c) => ({ c, v: metricValue(c, metric) }))
      .filter((s): s is { c: AnalysisCell; v: number } => s.v !== null);
    const colors = metric === "blindspots" ? [BLIND_FILL] : RAMP[metric];
    const breaks = quantileBreaks(
      scored.map((s) => s.v),
      colors.length,
    );
    let pending = false;
    const features: GeoJSON.Feature<GeoJSON.Polygon>[] = scored.map(({ c, v }) => {
      const isPending =
        metric === "dollars" && !(c.economic_available && (c.economic_high_usd ?? 0) > 0);
      if (isPending) pending = true;
      const color =
        metric === "blindspots"
          ? BLIND_FILL
          : isPending
            ? PENDING_COLOR
            : rampColor(v, breaks, colors);
      return {
        type: "Feature",
        geometry: { type: "Polygon", coordinates: cellPolygon(c) },
        properties: {
          lon: c.lon,
          lat: c.lat,
          color,
          opacity: metric === "blindspots" ? 0.62 : isPending ? 0.35 : 0.72,
        },
      };
    });
    // Ranking key differs from the colour value: blind spots all colour the
    // same flat shade, but rank by how under-reported they are. We rank by the
    // raw reporting shortfall (expected minus observed) so the order matches the
    // "26 of ~384" numbers shown in the rail, rather than the residual z-score
    // (which normalises by variance and so doesn't track the visible gap).
    const rankKey = (c: AnalysisCell): number =>
      metric === "dollars"
        ? c.economic_available
          ? (c.economic_high_usd ?? 0)
          : 0
        : metric === "people"
          ? c.displaced_high
          : c.expected - c.observed;
    const sorted = [...scored].sort((a, b) => rankKey(b.c) - rankKey(a.c));
    return {
      fc: { type: "FeatureCollection" as const, features },
      ranked: sorted.slice(0, 20).map((s) => s.c),
      anyPending: pending,
    };
  }, [cells, metric, isDistricts]);

  // Top cells get a bold outline. For blind spots, every confirmed cell is
  // "top"; for impact, the worst cells by value.
  const topFc = useMemo(() => {
    if (isDistricts) return emptyFc();
    const top = metric === "blindspots" ? cells.filter((c) => c.blind_spot) : ranked;
    return {
      type: "FeatureCollection" as const,
      features: top.map((c) => ({
        type: "Feature" as const,
        geometry: { type: "Polygon" as const, coordinates: cellPolygon(c) },
        properties: {},
      })),
    };
  }, [cells, ranked, metric, isDistricts]);

  // District choropleth, scaled to the most severe district so colour spans the
  // full ramp regardless of crisis size.
  const distFc = useMemo(() => {
    const withGeom = districts.filter(
      (d): d is AnalysisDistrict & { geom: GeoJSON.Polygon | GeoJSON.MultiPolygon } =>
        d.geom != null,
    );
    const maxComplete = Math.max(1, ...withGeom.map((d) => d.damage.complete));
    return {
      type: "FeatureCollection" as const,
      features: withGeom.map((d) => ({
        type: "Feature" as const,
        geometry: d.geom,
        properties: {
          id: d.id,
          color: lerpColor(DIST_LOW, DIST_HIGH, Math.sqrt(d.damage.complete / maxComplete)),
        },
      })),
    };
  }, [districts]);

  const selFc = useMemo(() => {
    const sel = districts.find((d) => d.id === selectedDistrictId && d.geom != null);
    if (!sel?.geom) return emptyFc();
    return {
      type: "FeatureCollection" as const,
      features: [{ type: "Feature" as const, geometry: sel.geom, properties: {} }],
    };
  }, [districts, selectedDistrictId]);

  // The single clicked cell, drawn as a bold accent square on top of everything.
  const selCellFc = useMemo(() => {
    if (isDistricts || !selectedCellKey) return emptyFc();
    const c = cells.find((x) => cellKey(x) === selectedCellKey);
    if (!c) return emptyFc();
    return {
      type: "FeatureCollection" as const,
      features: [
        {
          type: "Feature" as const,
          geometry: { type: "Polygon" as const, coordinates: cellPolygon(c) },
          properties: {},
        },
      ],
    };
  }, [cells, selectedCellKey, isDistricts]);

  // The crisis AOI outline: drawn under everything and visible on every layer.
  const aoiFc = useMemo(() => {
    if (!geometry) return emptyFc();
    return {
      type: "FeatureCollection" as const,
      features: [{ type: "Feature" as const, geometry, properties: {} }],
    };
  }, [geometry]);

  // Mount the map once.
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
      center: [20, 25],
      zoom: 2.4,
    });
    map.addControl(new maplibregl.NavigationControl({ showCompass: false }), "top-right");
    mapRef.current = map;

    map.on("load", () => {
      loadedRef.current = true;
      map.addSource(AOI_SRC, { type: "geojson", data: emptyFc() });
      map.addSource(CELLS_SRC, { type: "geojson", data: emptyFc() });
      map.addSource(TOP_SRC, { type: "geojson", data: emptyFc() });
      map.addSource(DIST_SRC, { type: "geojson", data: emptyFc() });
      map.addSource(SEL_SRC, { type: "geojson", data: emptyFc() });
      map.addSource(SELCELL_SRC, { type: "geojson", data: emptyFc() });

      // Crisis boundary first, so every other layer paints on top of it. A
      // soft teal glow under a crisp dashed teal line keeps the AOI ring legible
      // over any fill colour while staying distinct from the maroon district
      // outlines, the blue selection accent and the red/purple cell ramps.
      map.addLayer({
        id: AOI_CASING,
        type: "line",
        source: AOI_SRC,
        layout: { "line-join": "round" },
        paint: { "line-color": "#0d9488", "line-width": 8, "line-opacity": 0.3, "line-blur": 3 },
      });
      map.addLayer({
        id: AOI_LINE,
        type: "line",
        source: AOI_SRC,
        layout: { "line-join": "round" },
        paint: {
          "line-color": "#0d9488",
          "line-width": 2.8,
          "line-opacity": 1,
          "line-dasharray": [2.5, 1.5],
        },
      });

      map.addLayer({
        id: DIST_FILL,
        type: "fill",
        source: DIST_SRC,
        paint: { "fill-color": ["get", "color"], "fill-opacity": 0.55 },
      });
      map.addLayer({
        id: DIST_LINE,
        type: "line",
        source: DIST_SRC,
        paint: { "line-color": "#7a1410", "line-width": 1, "line-opacity": 0.7 },
      });
      map.addLayer({
        id: CELLS_FILL,
        type: "fill",
        source: CELLS_SRC,
        paint: { "fill-color": ["get", "color"], "fill-opacity": ["get", "opacity"] },
      });
      map.addLayer({
        id: CELLS_LINE,
        type: "line",
        source: CELLS_SRC,
        paint: { "line-color": "#1f2937", "line-width": 0.3, "line-opacity": 0.4 },
      });
      map.addLayer({
        id: TOP_LINE,
        type: "line",
        source: TOP_SRC,
        paint: { "line-color": "#111827", "line-width": 2.2, "line-opacity": 0.95 },
      });
      map.addLayer({
        id: SEL_LINE,
        type: "line",
        source: SEL_SRC,
        paint: { "line-color": SELECT_COLOR, "line-width": 3, "line-opacity": 0.95 },
      });
      // Clicked-cell highlight, drawn last so it reads on top of the impact fill
      // and the bold top-cell outlines. Accent blue contrasts with every ramp.
      map.addLayer({
        id: SELCELL_FILL,
        type: "fill",
        source: SELCELL_SRC,
        paint: { "fill-color": SELECT_COLOR, "fill-opacity": 0.25 },
      });
      map.addLayer({
        id: SELCELL_LINE,
        type: "line",
        source: SELCELL_SRC,
        paint: { "line-color": SELECT_COLOR, "line-width": 3.5, "line-opacity": 1 },
      });

      const canvasSize = () => {
        const cv = map.getCanvas();
        return { cw: cv.clientWidth, ch: cv.clientHeight };
      };

      map.on("mouseenter", CELLS_FILL, () => {
        map.getCanvas().style.cursor = "pointer";
      });
      map.on("mousemove", CELLS_FILL, (e) => {
        const p = e.features?.[0]?.properties as Record<string, unknown> | undefined;
        if (!p) return;
        const lon = Number(p.lon);
        const lat = Number(p.lat);
        const cell = cellLookupRef.current.get(`${lon.toFixed(4)},${lat.toFixed(4)}`);
        if (cell) setHover({ x: e.point.x, y: e.point.y, ...canvasSize(), cell });
      });
      map.on("mouseleave", CELLS_FILL, () => {
        map.getCanvas().style.cursor = "";
        setHover(null);
      });

      map.on("mouseenter", DIST_FILL, () => {
        map.getCanvas().style.cursor = "pointer";
      });
      map.on("mousemove", DIST_FILL, (e) => {
        const p = e.features?.[0]?.properties as Record<string, unknown> | undefined;
        const district = p ? districtLookupRef.current.get(String(p.id)) : undefined;
        if (district) setHover({ x: e.point.x, y: e.point.y, ...canvasSize(), district });
      });
      map.on("mouseleave", DIST_FILL, () => {
        map.getCanvas().style.cursor = "";
        setHover(null);
      });
      map.on("click", DIST_FILL, (e) => {
        const p = e.features?.[0]?.properties as Record<string, unknown> | undefined;
        if (p?.id) onSelectRef.current(String(p.id));
      });
    });

    return () => {
      loadedRef.current = false;
      map.remove();
      mapRef.current = null;
    };
  }, []);

  // Refresh the basemap tiles when the theme changes, in place. Both themes
  // serve the same OSM tiles; the dark look comes from the canvas invert filter
  // in styles/admin.css.
  useEffect(() => {
    const map = mapRef.current;
    if (!map) return;
    const apply = () => {
      const src = map.getSource("osm") as maplibregl.RasterTileSource | undefined;
      src?.setTiles(basemapTiles(theme));
    };
    if (loadedRef.current) apply();
    else map.once("load", apply);
  }, [theme]);

  // Keep the hover lookups in sync with the current data.
  useEffect(() => {
    const m = new Map<string, AnalysisCell>();
    for (const c of cells) m.set(cellKey(c), c);
    cellLookupRef.current = m;
  }, [cells]);
  useEffect(() => {
    const m = new Map<string, AnalysisDistrict>();
    for (const d of districts) m.set(d.id, d);
    districtLookupRef.current = m;
  }, [districts]);

  // Push data on metric / data change, and toggle which family is visible.
  // Also clears any stale tooltip, since the layer under the cursor just changed.
  useEffect(() => {
    setHover(null);
    const map = mapRef.current;
    if (!map) return;
    const apply = () => {
      (map.getSource(AOI_SRC) as maplibregl.GeoJSONSource | undefined)?.setData(aoiFc);
      (map.getSource(CELLS_SRC) as maplibregl.GeoJSONSource | undefined)?.setData(fc);
      (map.getSource(TOP_SRC) as maplibregl.GeoJSONSource | undefined)?.setData(topFc);
      (map.getSource(DIST_SRC) as maplibregl.GeoJSONSource | undefined)?.setData(distFc);
      (map.getSource(SEL_SRC) as maplibregl.GeoJSONSource | undefined)?.setData(selFc);
      (map.getSource(SELCELL_SRC) as maplibregl.GeoJSONSource | undefined)?.setData(selCellFc);
      const cellVis = isDistricts ? "none" : "visible";
      const distVis = isDistricts ? "visible" : "none";
      for (const id of [CELLS_FILL, CELLS_LINE, TOP_LINE, SELCELL_FILL, SELCELL_LINE]) {
        map.setLayoutProperty(id, "visibility", cellVis);
      }
      for (const id of [DIST_FILL, DIST_LINE]) {
        map.setLayoutProperty(id, "visibility", distVis);
      }
    };
    if (loadedRef.current) apply();
    else map.once("load", apply);
  }, [fc, topFc, distFc, selFc, selCellFc, aoiFc, isDistricts]);

  // Switching the map layer clears the clicked-cell highlight: the ranking it
  // came from no longer applies, and the cell may not even be drawn anymore.
  // biome-ignore lint/correctness/useExhaustiveDependencies: metric is the trigger, not read.
  useEffect(() => {
    setSelectedCellKey(null);
  }, [metric]);

  // Frame the whole AOI once when the bbox lands.
  const fittedRef = useRef(false);
  useEffect(() => {
    const map = mapRef.current;
    if (!map || !bbox || fittedRef.current) return;
    const fit = () => {
      fittedRef.current = true;
      // Pin the camera to the AOI first, then frame it. Padded bounds, so the
      // fit (with its screen padding) still resolves inside the lock.
      map.setMaxBounds(maxBoundsFor(bbox));
      map.fitBounds(
        [
          [bbox.min_lon, bbox.min_lat],
          [bbox.max_lon, bbox.max_lat],
        ],
        { padding: 40, maxZoom: 13, duration: 600 },
      );
    };
    if (loadedRef.current) fit();
    else map.once("load", fit);
  }, [bbox]);

  // Fly to the selected district whenever it changes.
  useEffect(() => {
    const map = mapRef.current;
    if (!map || !selectedDistrictId) return;
    const sel = districts.find((d) => d.id === selectedDistrictId);
    if (!sel) return;
    const go = () => {
      const b = sel.geom ? geomBounds(sel.geom) : null;
      if (b) map.fitBounds(b, { padding: 64, maxZoom: 14, duration: 700 });
      else map.flyTo({ center: [sel.centroid_lon, sel.centroid_lat], zoom: 12.5, speed: 1.1 });
    };
    if (loadedRef.current) go();
    else map.once("load", go);
  }, [selectedDistrictId, districts]);

  // One message covers every "nothing to draw" case, layer-aware, so a sparse
  // layer (e.g. zero blind spots) reads as a finding rather than a blank map.
  let emptyMsg: string | null = null;
  if (isDistricts) {
    if (distFc.features.length === 0) {
      emptyMsg =
        "No mapped areas for this crisis yet. Reports haven't fallen into a recognised division with a boundary.";
    }
  } else if (cells.length === 0) {
    emptyMsg =
      "No mesh cells for this crisis yet. Buildings or the density grid haven't been loaded, so there's nothing to score on the map.";
  } else if (fc.features.length === 0) {
    emptyMsg =
      metric === "blindspots"
        ? "No blind spots flagged. Reporting coverage looks even across this crisis."
        : metric === "dollars"
          ? "No valued damage to map on this layer yet."
          : "No displacement to map on this layer yet.";
  }

  const flyToCell = (c: AnalysisCell) => {
    setSelectedCellKey(cellKey(c));
    mapRef.current?.flyTo({ center: [c.lon, c.lat], zoom: 13, speed: 1.2 });
  };

  // Re-frame the whole AOI, undoing the coordinator's pan/zoom. Same framing
  // the map opens with (see the bbox-fit effect above). Falls back to the wide
  // opening view for a crisis with no AOI envelope.
  const resetView = () => {
    const map = mapRef.current;
    if (!map) return;
    if (bbox) {
      map.fitBounds(
        [
          [bbox.min_lon, bbox.min_lat],
          [bbox.max_lon, bbox.max_lat],
        ],
        { padding: 40, maxZoom: 13, duration: 600 },
      );
    } else {
      map.easeTo({ center: [20, 25], zoom: 2.4, duration: 600 });
    }
  };

  return (
    <div className="ana-mapwrap">
      <div className="ana-map">
        <div className="ana-map-bar">
          <span className="lead">Map layer</span>
          <MetricToggle metric={metric} onChange={onMetricChange} />
          <span className="hint">
            {isDistricts ? "Click an area to focus it" : "Hover a cell for its estimate"}
          </span>
        </div>
        <div className="ana-map-stage">
          <div ref={containerRef} className="ana-map-canvas" />
          <button
            type="button"
            className="ov map-reset"
            onClick={resetView}
            title="Reset the map to frame the whole crisis area"
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
          {emptyMsg && <div className="ana-map-empty">{emptyMsg}</div>}
          {!emptyMsg && <Legend metric={metric} anyPending={anyPending} />}
          {hover && <MapTooltip hover={hover} />}
        </div>
      </div>
      <RankedRail metric={metric} cells={ranked} selectedKey={selectedCellKey} onPick={flyToCell} />
    </div>
  );
}

function emptyFc(): GeoJSON.FeatureCollection {
  return { type: "FeatureCollection", features: [] };
}

// --- Metric toggle (dashboard `.modes` segmented control) ---------------
const DollarIcon = (
  <svg
    aria-hidden="true"
    viewBox="0 0 24 24"
    fill="none"
    stroke="currentColor"
    strokeWidth="2"
    strokeLinecap="round"
    strokeLinejoin="round"
  >
    <path d="M12 2v20M17 6.5C17 4.6 14.8 3.5 12 3.5S7 4.6 7 6.5 9 9.5 12 10s5 1.6 5 3.5-2.2 3-5 3-5-1.1-5-3" />
  </svg>
);
const PeopleIcon = (
  <svg
    aria-hidden="true"
    viewBox="0 0 24 24"
    fill="none"
    stroke="currentColor"
    strokeWidth="2"
    strokeLinecap="round"
    strokeLinejoin="round"
  >
    <path d="M16 19v-1a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v1" />
    <circle cx="9" cy="7" r="3" />
    <path d="M22 19v-1a4 4 0 0 0-3-3.85M16 4.15A4 4 0 0 1 16 11.7" />
  </svg>
);
const BlindIcon = (
  <svg
    aria-hidden="true"
    viewBox="0 0 24 24"
    fill="none"
    stroke="currentColor"
    strokeWidth="2"
    strokeLinecap="round"
    strokeLinejoin="round"
  >
    <circle cx="12" cy="12" r="9" />
    <circle cx="12" cy="12" r="3.4" />
    <path d="M12 3v3M12 18v3M3 12h3M18 12h3" />
  </svg>
);
const DistrictIcon = (
  <svg
    aria-hidden="true"
    viewBox="0 0 24 24"
    fill="none"
    stroke="currentColor"
    strokeWidth="2"
    strokeLinecap="round"
    strokeLinejoin="round"
  >
    <path d="M3 6l6-3 6 3 6-3v15l-6 3-6-3-6 3V6z" />
    <path d="M9 3v15M15 6v15" />
  </svg>
);

const METRICS: { id: ImpactMetric; label: string; icon: ReactNode }[] = [
  { id: "dollars", label: "Dollars", icon: DollarIcon },
  { id: "people", label: "People", icon: PeopleIcon },
  { id: "blindspots", label: "Blind spots", icon: BlindIcon },
  { id: "districts", label: "Areas", icon: DistrictIcon },
];

function MetricToggle({
  metric,
  onChange,
}: {
  metric: ImpactMetric;
  onChange: (m: ImpactMetric) => void;
}) {
  return (
    <div className="modes" role="tablist" aria-label="Impact map layer">
      {METRICS.map((m) => (
        <button
          key={m.id}
          type="button"
          role="tab"
          aria-selected={metric === m.id}
          className={metric === m.id ? "on" : undefined}
          onClick={() => onChange(m.id)}
        >
          {m.icon}
          {m.label}
        </button>
      ))}
    </div>
  );
}

function Legend({ metric, anyPending }: { metric: ImpactMetric; anyPending: boolean }) {
  if (metric === "blindspots") {
    return (
      <div className="ana-ov ana-legend">
        <div className="lt">Confirmed blind spots</div>
        <div className="note" style={{ marginTop: 2 }}>
          <span className="sw" style={{ background: BLIND_FILL }} aria-hidden="true" />
          <span>under-reported and ringed by damage</span>
        </div>
      </div>
    );
  }
  if (metric === "districts") {
    return (
      <div className="ana-ov ana-legend">
        <div className="lt">Severe-damage reports</div>
        <div className="ramp">
          <i style={{ background: "#fde8e0" }} />
          <i style={{ background: "#f3b9a8" }} />
          <i style={{ background: "#e07b66" }} />
          <i style={{ background: "#c8463a" }} />
          <i style={{ background: "#b3261e" }} />
        </div>
        <div className="ends">
          <span>fewer</span>
          <span>more</span>
        </div>
      </div>
    );
  }
  const label =
    metric === "dollars"
      ? "Estimated economic loss (high end)"
      : "Estimated people affected (high end)";
  return (
    <div className="ana-ov ana-legend">
      <div className="lt">{label}</div>
      <div className="ramp">
        {RAMP[metric].map((c) => (
          <i key={c} style={{ background: c }} />
        ))}
      </div>
      <div className="ends">
        <span>lower</span>
        <span>higher</span>
      </div>
      {metric === "dollars" && anyPending && (
        <div className="note">
          <span className="sw" style={{ background: PENDING_COLOR }} aria-hidden="true" />
          <span>damaged, value pending (no LitPop)</span>
        </div>
      )}
    </div>
  );
}

function MapTooltip({ hover }: { hover: HoverState }) {
  const pos = tipStyle(hover);
  if (hover.district) {
    const d = hover.district;
    const total = d.damage.minimal + d.damage.partial + d.damage.complete;
    const severePct =
      total > 0 ? Math.round(((d.damage.partial + d.damage.complete) / total) * 100) : 0;
    return (
      <div className="ana-ov ana-celltip" style={{ left: pos.left, top: pos.top }}>
        <div className="tip-name">{d.name}</div>
        <div className="r">
          <span className="k">Reports</span>
          <span className="v">{d.report_count.toLocaleString()}</span>
        </div>
        <div className="r">
          <span className="k">Severe share</span>
          <span className="v">{severePct}%</span>
        </div>
        <div className="r">
          <span className="k">Services hit</span>
          <span className="v">{d.services_hit}</span>
        </div>
      </div>
    );
  }
  const c = hover.cell;
  if (!c) return null;
  return (
    <div className="ana-ov ana-celltip" style={{ left: pos.left, top: pos.top }}>
      <div className="r">
        <span className="k">Affected</span>
        <span className="v">{formatCountRange(c.displaced_low, c.displaced_high)} people</span>
      </div>
      <div className="r">
        <span className="k">Economic loss</span>
        <span className="v">
          {c.economic_available ? formatUsdRange(c.economic_low_usd, c.economic_high_usd) : "-"}
        </span>
      </div>
      <div className="r">
        <span className="k">Reports here</span>
        <span className="v">
          {c.observed} of ~{Math.round(c.expected)} expected
        </span>
      </div>
      {c.blind_spot && <div className="flag">Blind spot</div>}
    </div>
  );
}

// --- Ranked per-cell rail (right of the map) ----------------------------
function RankedRail({
  metric,
  cells,
  selectedKey,
  onPick,
}: {
  metric: ImpactMetric;
  cells: AnalysisCell[];
  selectedKey: string | null;
  onPick: (c: AnalysisCell) => void;
}) {
  const isDistricts = metric === "districts";
  const title =
    metric === "dollars"
      ? "Highest economic loss"
      : metric === "people"
        ? "Most people affected"
        : metric === "blindspots"
          ? "Most under-reported"
          : "Top cells";
  const valueLabel = (c: AnalysisCell): string => {
    if (metric === "dollars") {
      return c.economic_available ? formatUsd(c.economic_high_usd ?? 0) : "pending";
    }
    if (metric === "people") return c.displaced_high.toLocaleString();
    // Under-reporting layer: show the plain reporting gap (reports seen vs the
    // model's expected count) rather than the residual z-score, which reads as
    // jargon (and is negative for under-reported cells, which confuses further).
    return `${c.observed} of ~${Math.round(c.expected)}`;
  };
  // Column label for the value, so the bare per-row numbers are explained once
  // at the top rather than guessed at. For under-reporting "Reports / expected"
  // templates the "26 of ~384" value: reports received over reports expected.
  const valueHeader =
    metric === "dollars"
      ? "Est. loss"
      : metric === "people"
        ? "People affected"
        : "Reports / expected";
  return (
    <aside className="ana-rank">
      <div className="ana-rank-h">
        <div className="t">{title}</div>
        <div className="meta">
          {isDistricts ? "Per-cell ranking" : "Click a cell to highlight and zoom to it"}
        </div>
      </div>
      <div className="ana-rank-list">
        {isDistricts ? (
          <div className="ana-rank-empty">
            Switch to the Dollars, People or Blind spots layer to rank individual cells.
          </div>
        ) : cells.length === 0 ? (
          <div className="ana-rank-empty">No cells to rank on this layer.</div>
        ) : (
          <>
            <div className="ana-rank-cols" aria-hidden="true">
              <span className="i-sp" />
              <span className="co-h">Cell</span>
              <span className="v-h">{valueHeader}</span>
            </div>
            {cells.map((c, i) => (
              <button
                type="button"
                key={`${c.lon},${c.lat}`}
                className={`ana-rank-row${cellKey(c) === selectedKey ? " on" : ""}`}
                aria-pressed={cellKey(c) === selectedKey}
                onClick={() => onPick(c)}
              >
                <span className="i">{i + 1}</span>
                <span className="co">
                  {c.lat.toFixed(3)}, {c.lon.toFixed(3)}
                </span>
                <span className="v">{valueLabel(c)}</span>
              </button>
            ))}
          </>
        )}
      </div>
    </aside>
  );
}
