import maplibregl from "maplibre-gl";
import "maplibre-gl/dist/maplibre-gl.css";
import { useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { useLatest } from "../../hooks/useLatest";
import { BASEMAP_ATTRIBUTION, OSM_TILE_TEMPLATE } from "../../lib/basemap";
import { type AreaGeometry, bboxOfGeometry, pointInGeometry } from "../../lib/geometry";
import { DEFAULT_MAP_CENTER } from "../../lib/mapDefaults";
import { GeoError, acquireProgressive, geo } from "../../platform/geo";
import { tileCache } from "../../platform/tileCache";
import type { Crisis } from "../../types";
import { type BuildingDetails, BuildingDetailsSheet } from "../BuildingDetailsSheet";
import { MapLoadingIndicator } from "../MapLoadingIndicator";
import { StepShell } from "../StepShell";
import { VoiceNoteRecorder } from "../VoiceNoteRecorder";

type GpsStatus = "idle" | "acquiring" | "acquired" | "denied" | "unavailable" | "outside";

const gpsErrorStatus = (err: unknown): GpsStatus =>
  err instanceof GeoError && err.code === "permission_denied" ? "denied" : "unavailable";

const DEFAULT_ZOOM = 16;
const CRISIS_FIT_ZOOM = 14;
// Below this zoom the buildings PMTiles has no tiles, so the loading overlay is suppressed.
const BUILDINGS_MIN_ZOOM = 15;
// Spinner cap for a permission prompt left open or a platform that never calls back
// (acquireProgressive's own coarse timeout is 6s).
const GPS_TIMEOUT_MS = 12_000;
// Delay before the loading overlay may show, so warm revisits never flash it.
const OVERLAY_GRACE_MS = 450;
const OVERLAY_SAFETY_TIMEOUT_MS = 10_000;
const SCROLL_LOCK_DELAY_MS = 350;
// Map show/hide height transition; MapLibre resizes just after it ends.
const MAP_HEIGHT_TRANSITION_MS = 300;
const HIT_TEST_TOLERANCE_PX = 6;

interface StepLocationProps {
  latitude: number | null;
  longitude: number | null;
  crisis?: Crisis | null;
  infraName: string;
  onChangeInfraName: (v: string) => void;
  routeDescription: string;
  onChangeRouteDescription: (v: string) => void;
  onChange: (lat: number, lng: number) => void;
  onBuildingSelect?: (buildingId: string | null) => void;
  onNext: () => void;
  onBack: () => void;
  onChangeCrisis?: () => void;
  stepNumber?: number;
  totalSteps?: number;
}

function asNumber(v: unknown): number | null {
  if (typeof v === "number" && Number.isFinite(v)) return v;
  if (typeof v === "string" && v.trim() !== "") {
    const n = Number(v);
    return Number.isFinite(n) ? n : null;
  }
  return null;
}

function asString(v: unknown): string | null {
  return typeof v === "string" && v.trim() !== "" ? v : null;
}

function extractName(props: Record<string, unknown> | null | undefined): string | null {
  if (!props) return null;
  const direct = asString(props.name);
  if (direct) return direct;
  // Overture stores names as an object: { primary, common, rules: [...] }
  const namesRaw = props.names;
  let names: unknown = namesRaw;
  if (typeof namesRaw === "string") {
    try {
      names = JSON.parse(namesRaw);
    } catch {
      return null;
    }
  }
  if (names && typeof names === "object") {
    const obj = names as Record<string, unknown>;
    return asString(obj.primary) ?? asString(obj.common);
  }
  return null;
}

function featureToBuildingDetails(
  gers: string,
  props: Record<string, unknown> | null | undefined,
): BuildingDetails {
  const p = props ?? {};
  return {
    gers,
    name: extractName(p),
    class: asString(p.class) ?? asString(p.building_class),
    subtype: asString(p.subtype),
    height: asNumber(p.height),
    num_floors: asNumber(p.num_floors),
    min_height: asNumber(p.min_height),
    level: asNumber(p.level),
  };
}

const MAX_NAME_LENGTH = 100;
const MAX_ROUTE_LENGTH = 300;

export function StepLocation({
  latitude,
  longitude,
  crisis,
  infraName,
  onChangeInfraName,
  routeDescription,
  onChangeRouteDescription,
  onChange,
  onBuildingSelect,
  onNext,
  onBack,
  onChangeCrisis,
  stepNumber,
  totalSteps,
}: StepLocationProps) {
  const { t } = useTranslation();
  const mapContainerRef = useRef<HTMLDivElement>(null);
  const mapRef = useRef<maplibregl.Map | null>(null);
  const routeDetailsRef = useRef<HTMLDetailsElement>(null);
  const progressiveCancelRef = useRef<(() => void) | null>(null);
  const gpsWatchdogRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const scrollLockTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  // Set on a user drag/zoom so moveend commits only positions the user chose,
  // never programmatic recenters (crisis fit, GPS flyTo).
  const pendingUserMoveRef = useRef(false);
  const onChangeRef = useLatest(onChange);
  const onBuildingSelectRef = useLatest(onBuildingSelect);
  const onChangeInfraNameRef = useLatest(onChangeInfraName);
  const infraNameRef = useLatest(infraName);
  // Last auto-filled building name, so a later pick replaces it but never a typed name.
  const lastAutoFilledNameRef = useRef<string>("");
  const selectedBuildingIdRef = useRef<string | null>(null);
  const [selectedBuilding, setSelectedBuilding] = useState<BuildingDetails | null>(null);
  const [gpsStatus, setGpsStatus] = useState<GpsStatus>("idle");
  const [accuracy, setAccuracy] = useState<number | null>(null);
  const [displayCoords, setDisplayCoords] = useState<{ lat: number; lng: number } | null>(
    latitude !== null && longitude !== null ? { lat: latitude, lng: longitude } : null,
  );
  const [outsideArea, setOutsideArea] = useState(false);
  // Map pin and description are mutually exclusive (the report carries one or the
  // other). Never opened automatically; seeded open only for a draft that used it.
  const [routeOpen, setRouteOpen] = useState(() => routeDescription.trim() !== "");
  const [buildingsLoading, setBuildingsLoading] = useState(true);
  // The map re-initializes on every mount; on a warm revisit it settles within this
  // grace period, so the overlay only shows on a genuinely slow load.
  const [overlayGraceElapsed, setOverlayGraceElapsed] = useState(false);
  useEffect(() => {
    const t = setTimeout(() => setOverlayGraceElapsed(true), OVERLAY_GRACE_MS);
    return () => clearTimeout(t);
  }, []);
  // Buildings overlay is suppressed while zoomed out below BUILDINGS_MIN_ZOOM.
  const hasBuildings = Boolean(crisis?.pmtiles_url);
  const [inBuildingZoom, setInBuildingZoom] = useState(true);
  const showBuildingsOverlay =
    buildingsLoading && overlayGraceElapsed && (!hasBuildings || inBuildingZoom);
  // Holds the buildings overlay across the initial GPS flyTo so buildings do not
  // reload uncovered at the GPS position.
  const awaitingAutoRecenterRef = useRef(false);
  const crisisGeometryRef = useLatest<AreaGeometry | null>(crisis?.geometry ?? null);

  const clearGpsWatchdog = () => {
    if (gpsWatchdogRef.current !== null) {
      clearTimeout(gpsWatchdogRef.current);
      gpsWatchdogRef.current = null;
    }
  };

  // Never opens the "describe location" path for the user; they toggle it.
  const armGpsWatchdog = () => {
    clearGpsWatchdog();
    gpsWatchdogRef.current = setTimeout(() => {
      gpsWatchdogRef.current = null;
      progressiveCancelRef.current?.();
      setGpsStatus((s) => (s === "acquiring" ? "unavailable" : s));
    }, GPS_TIMEOUT_MS);
  };

  // biome-ignore lint/correctness/useExhaustiveDependencies: map initializes once; lat/lng/crisis seed position only
  useEffect(() => {
    let overlaySafetyTimeout: ReturnType<typeof setTimeout> | null = null;
    if (!mapContainerRef.current) return;

    // See BrowseMap for why protocols register from the lazy map chunk.
    tileCache.registerMapProtocols(maplibregl);

    const crisisGeometry = crisis?.geometry ?? null;
    const crisisBounds = crisisGeometry ? bboxOfGeometry(crisisGeometry) : null;
    const hasExistingPick = latitude !== null && longitude !== null;
    const initialCenter: maplibregl.LngLatLike = hasExistingPick
      ? [longitude as number, latitude as number]
      : DEFAULT_MAP_CENTER;

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
      center: initialCenter,
      zoom: DEFAULT_ZOOM,
    });
    mapRef.current = map;

    map.on("error", (e) => {
      const err = (e as { error?: { message?: string } }).error;
      console.error("[stepLocation] map error:", err?.message ?? e);
    });

    if (!hasExistingPick && crisisBounds) {
      map.fitBounds(crisisBounds, { padding: 40, animate: false, maxZoom: CRISIS_FIT_ZOOM });
    }
    // Do not auto-acquire once the user chose the route-description path; it would
    // re-attach a GPS pin they did not want.
    if (!hasExistingPick && routeDescription.trim() === "" && geo.isAvailable()) {
      setGpsStatus("acquiring");
      awaitingAutoRecenterRef.current = true;
      armGpsWatchdog();
      // Coarse first, then precise. Cancelled on user movestart so a late precise fix
      // cannot yank the chosen point.
      const startAuto = () => {
        progressiveCancelRef.current = acquireProgressive(
          (pos) => {
            clearGpsWatchdog();
            if (crisisGeometry && !pointInGeometry(pos.longitude, pos.latitude, crisisGeometry)) {
              setGpsStatus("outside");
              setOutsideArea(true);
              mapRef.current?.flyTo({
                center: [pos.longitude, pos.latitude],
                zoom: DEFAULT_ZOOM,
                duration: 700,
              });
              finishAutoRecenter(true);
              return;
            }
            setGpsStatus("acquired");
            setAccuracy(pos.accuracy);
            setDisplayCoords({ lat: pos.latitude, lng: pos.longitude });
            onChangeRef.current(pos.latitude, pos.longitude);
            mapRef.current?.flyTo({
              center: [pos.longitude, pos.latitude],
              zoom: DEFAULT_ZOOM,
              duration: 700,
            });
            finishAutoRecenter(true);
          },
          (err) => {
            clearGpsWatchdog();
            setGpsStatus(gpsErrorStatus(err));
            finishAutoRecenter(false);
          },
          { maximumAge: 60_000 },
        );
      };
      // Blocked permission never re-prompts, so getCurrentPosition would hang until the watchdog.
      geo.getPermissionState().then((perm) => {
        if (perm === "denied") {
          clearGpsWatchdog();
          setGpsStatus("denied");
          finishAutoRecenter(false);
          return;
        }
        startAuto();
      });
    }

    map.addControl(new maplibregl.NavigationControl({ showCompass: false }), "bottom-right");

    // Marker is pinned to the viewport centre.
    const centerMarker = new maplibregl.Marker().setLngLat(map.getCenter()).addTo(map);
    map.on("move", () => {
      centerMarker.setLngLat(map.getCenter());
      if (crisisGeometry) {
        const c = map.getCenter();
        setOutsideArea(!pointInGeometry(c.lng, c.lat, crisisGeometry));
      }
    });

    // Programmatic flyTo has no originalEvent; only user gestures cancel the precise upgrade.
    map.on("movestart", (e) => {
      if ((e as { originalEvent?: unknown }).originalEvent) {
        pendingUserMoveRef.current = true;
        progressiveCancelRef.current?.();
      }
    });

    // Only called from deliberate user gestures; the marker IS the map centre.
    const commitCenterPick = () => {
      const center = map.getCenter();
      if (crisisGeometry && !pointInGeometry(center.lng, center.lat, crisisGeometry)) {
        setOutsideArea(true);
        return;
      }
      setOutsideArea(false);
      setDisplayCoords({ lat: center.lat, lng: center.lng });
      onChangeRef.current(center.lat, center.lng);
    };

    // buildingsReady latches once the overlay is dismissed.
    let buildingsTileRequested = false;
    let buildingsSettled = false;
    let buildingsReady = false;

    const dismissBuildingsOverlay = () => {
      if (buildingsReady) return;
      buildingsReady = true;
      setBuildingsLoading(false);
      selectBuildingAtCenter();
    };

    const onBuildingsSettled = () => {
      buildingsSettled = true;
      if (awaitingAutoRecenterRef.current) return;
      dismissBuildingsOverlay();
    };

    const finishAutoRecenter = (willRecenter: boolean) => {
      awaitingAutoRecenterRef.current = false;
      if (willRecenter) {
        buildingsSettled = false;
        return;
      }
      if (buildingsSettled) dismissBuildingsOverlay();
    };

    const applyBuildingPick = (
      buildingId: string | null,
      props: Record<string, unknown> | null | undefined,
    ) => {
      if (selectedBuildingIdRef.current === buildingId) return;

      if (selectedBuildingIdRef.current) {
        map.setFeatureState(
          { source: "buildings", sourceLayer: "building", id: selectedBuildingIdRef.current },
          { selected: false },
        );
      }

      if (buildingId) {
        selectedBuildingIdRef.current = buildingId;
        map.setFeatureState(
          { source: "buildings", sourceLayer: "building", id: buildingId },
          { selected: true },
        );
        onBuildingSelectRef.current?.(buildingId);
        const details = featureToBuildingDetails(buildingId, props);
        setSelectedBuilding(details);
        // Prefill the name only if empty or still our last auto-fill; never overwrite a typed name.
        const next = (details.name ?? t("stepLocation.unnamed")).slice(0, MAX_NAME_LENGTH);
        const current = infraNameRef.current.trim();
        if (current === "" || current === lastAutoFilledNameRef.current) {
          onChangeInfraNameRef.current(next);
          lastAutoFilledNameRef.current = next;
        }
      } else {
        selectedBuildingIdRef.current = null;
        onBuildingSelectRef.current?.(null);
        setSelectedBuilding(null);
        // Pin left the building: drop an auto-filled name, never a typed one.
        const current = infraNameRef.current.trim();
        if (current !== "" && current === lastAutoFilledNameRef.current) {
          onChangeInfraNameRef.current("");
          lastAutoFilledNameRef.current = "";
        }
      }
    };

    const selectBuildingAtCenter = () => {
      if (!buildingsReady) return;
      if (!map.getLayer("buildings-fill")) return;
      const center = map.getCenter();
      if (crisisGeometry && !pointInGeometry(center.lng, center.lat, crisisGeometry)) {
        applyBuildingPick(null, null);
        return;
      }
      const point = map.project(center);
      // Marker tip is sub-pixel at high zoom, so widen to a small box on a centre miss.
      let features = map.queryRenderedFeatures(point, { layers: ["buildings-fill"] });
      if (features.length === 0) {
        features = map.queryRenderedFeatures(
          [
            [point.x - HIT_TEST_TOLERANCE_PX, point.y - HIT_TEST_TOLERANCE_PX],
            [point.x + HIT_TEST_TOLERANCE_PX, point.y + HIT_TEST_TOLERANCE_PX],
          ],
          { layers: ["buildings-fill"] },
        );
      }
      const feature = features[0];
      const buildingId = (feature?.id as string | undefined) ?? null;
      applyBuildingPick(buildingId, feature?.properties ?? null);
    };

    map.on("moveend", () => {
      const userMoved = pendingUserMoveRef.current;
      pendingUserMoveRef.current = false;
      if (userMoved) commitCenterPick();
      selectBuildingAtCenter();
    });

    // Buildings often paint after moveend, so re-run the centre hit-test on idle.
    // applyBuildingPick dedups repeats.
    map.on("idle", selectBuildingAtCenter);

    const syncBuildingZoom = () => {
      const inRange = map.getZoom() >= BUILDINGS_MIN_ZOOM;
      setInBuildingZoom(inRange);
      if (inRange && crisis?.pmtiles_url && !buildingsReady && overlaySafetyTimeout === null) {
        overlaySafetyTimeout = setTimeout(dismissBuildingsOverlay, OVERLAY_SAFETY_TIMEOUT_MS);
      }
    };
    map.on("zoomend", syncBuildingZoom);
    syncBuildingZoom();

    map.on("load", () => {
      if (crisisGeometry) {
        map.addSource("crisis-boundary", {
          type: "geojson",
          data: {
            type: "Feature",
            geometry: crisisGeometry,
            properties: {},
          },
        });
        map.addLayer({
          id: "crisis-boundary-line",
          type: "line",
          source: "crisis-boundary",
          paint: { "line-color": "#1d4ed8", "line-width": 2, "line-dasharray": [4, 2] },
        });
      }
      if (crisis?.pmtiles_url) {
        const raw = crisis.pmtiles_url.startsWith("pmtiles://")
          ? crisis.pmtiles_url.slice("pmtiles://".length)
          : crisis.pmtiles_url;
        // Native: caches PMTiles byte-ranges to the Filesystem (no-op on web, the SW does it).
        // Must run before the source loads.
        tileCache.registerPmtiles(raw);
        const pmtilesUrl = `pmtiles://${raw}`;
        // setFeatureState needs feature.id, so promote the Overture GERS id.
        map.addSource("buildings", {
          type: "vector",
          url: pmtilesUrl,
          promoteId: { building: "id" },
        });
        map.addLayer({
          id: "buildings-fill",
          type: "fill",
          source: "buildings",
          "source-layer": "building",
          paint: {
            "fill-color": [
              "case",
              ["boolean", ["feature-state", "selected"], false],
              "#dc2626",
              "#3b82f6",
            ],
            "fill-opacity": ["case", ["boolean", ["feature-state", "selected"], false], 0.4, 0.25],
            "fill-outline-color": [
              "case",
              ["boolean", ["feature-state", "selected"], false],
              "#991b1b",
              "#1d4ed8",
            ],
          },
        });

        // A raw map.once("idle") fires before buildings tiles are even queued (the map is
        // already idle from the OSM load), so wait for the idle after the first buildings dataloading.
        map.on("dataloading", (e: maplibregl.MapDataEvent & { sourceId?: string }) => {
          if (e.sourceId === "buildings") buildingsTileRequested = true;
        });
        map.on("idle", () => {
          if (!buildingsTileRequested) return;
          onBuildingsSettled();
        });

        // Cover a map that already loaded in buildings range (e.g. GPS fix).
        syncBuildingZoom();

        // A tap selects (and commits) a building until the next pan re-picks at the pin.
        map.on("click", "buildings-fill", (e) => {
          const feature = e.features?.[0];
          const buildingId = feature?.id as string | undefined;
          if (!feature || !buildingId) return;
          if (crisisGeometry && !pointInGeometry(e.lngLat.lng, e.lngLat.lat, crisisGeometry)) {
            setOutsideArea(true);
            return;
          }
          applyBuildingPick(buildingId, feature.properties ?? null);
          commitCenterPick();
        });
      } else {
        // Safety timeout in case `idle` never fires.
        map.on("idle", onBuildingsSettled);
        overlaySafetyTimeout = setTimeout(dismissBuildingsOverlay, OVERLAY_SAFETY_TIMEOUT_MS);
      }
    });

    return () => {
      if (overlaySafetyTimeout !== null) clearTimeout(overlaySafetyTimeout);
      clearGpsWatchdog();
      progressiveCancelRef.current?.();
      centerMarker.remove();
      map.remove();
      mapRef.current = null;
    };
  }, []);

  async function requestLocation() {
    if (!geo.isAvailable()) {
      setGpsStatus("unavailable");
      return;
    }
    // Blocked permission never re-prompts; a fresh getCurrentPosition would silently hang.
    const perm = await geo.getPermissionState();
    if (perm === "denied") {
      setGpsStatus("denied");
      return;
    }
    setGpsStatus("acquiring");
    armGpsWatchdog();
    progressiveCancelRef.current?.();
    progressiveCancelRef.current = acquireProgressive(
      (pos) => {
        clearGpsWatchdog();
        const { latitude: lat, longitude: lng, accuracy: acc } = pos;
        if (crisisGeometryRef.current && !pointInGeometry(lng, lat, crisisGeometryRef.current)) {
          setGpsStatus("outside");
          setOutsideArea(true);
          mapRef.current?.flyTo({ center: [lng, lat], zoom: DEFAULT_ZOOM, duration: 800 });
          return;
        }
        setGpsStatus("acquired");
        setAccuracy(acc);
        setOutsideArea(false);
        onChange(lat, lng);
        setDisplayCoords({ lat, lng });
        mapRef.current?.flyTo({ center: [lng, lat], zoom: DEFAULT_ZOOM, duration: 800 });
      },
      (err) => {
        clearGpsWatchdog();
        setGpsStatus(gpsErrorStatus(err));
      },
    );
  }

  // A route description is the "no GPS" choice: drop any pin (the parent clears lat/lng).
  function handleRouteChange(v: string) {
    onChangeRouteDescription(v);
    if (v.trim() !== "") {
      progressiveCancelRef.current?.();
      clearGpsWatchdog();
      setDisplayCoords(null);
      setAccuracy(null);
      setOutsideArea(false);
      setGpsStatus((s) => (s === "acquiring" ? "idle" : s));
    }
  }

  // Opening the "no GPS" panel clears the pin-derived name. Only on a real closed->open
  // transition so resuming a route-path draft does not wipe it.
  function handleRouteToggle(nextOpen: boolean) {
    if (nextOpen && !routeOpen) {
      selectedBuildingIdRef.current = null;
      lastAutoFilledNameRef.current = "";
      if (infraNameRef.current.trim() !== "") onChangeInfraName("");
    }
    setRouteOpen(nextOpen);
  }

  // Mobile: the keyboard's scroll-into-view and visualViewport reflow yank the field
  // mid-type, so freeze the step scroller after the initial focus-scroll settles.
  const lockScroll = (locked: boolean) => {
    const scroller = routeDetailsRef.current?.closest<HTMLElement>("[data-step-scroll]");
    if (scroller) scroller.style.overflowY = locked ? "hidden" : "auto";
  };
  const handleRouteFocus = () => {
    if (scrollLockTimerRef.current !== null) clearTimeout(scrollLockTimerRef.current);
    scrollLockTimerRef.current = setTimeout(() => {
      scrollLockTimerRef.current = null;
      lockScroll(true);
    }, SCROLL_LOCK_DELAY_MS);
  };
  const handleRouteBlur = () => {
    if (scrollLockTimerRef.current !== null) {
      clearTimeout(scrollLockTimerRef.current);
      scrollLockTimerRef.current = null;
    }
    lockScroll(false);
  };
  // Unlock if the step unmounts while the field is focused (e.g. tapping Next).
  // biome-ignore lint/correctness/useExhaustiveDependencies: unmount-only restore; lockScroll closes over stable refs
  useEffect(() => () => lockScroll(false), []);

  // The map container stays mounted while hidden (unmounting tears down MapLibre),
  // so resize() after the height transition.
  // biome-ignore lint/correctness/useExhaustiveDependencies: routeOpen is the trigger, not a body dep; re-run to resize when the map shows/hides
  useEffect(() => {
    const resizeId = setTimeout(() => mapRef.current?.resize(), MAP_HEIGHT_TRANSITION_MS + 20);
    return () => clearTimeout(resizeId);
  }, [routeOpen]);

  const hasPin = displayCoords !== null;
  const gpsFailed = gpsStatus === "denied" || gpsStatus === "unavailable";
  const locationMissing = (!hasPin || outsideArea) && routeDescription.trim() === "";
  // Collapses to 0 while the description path is active.
  const mapHeight = routeOpen ? "0px" : "min(42vh, calc(320px * var(--ui-scale, 1)))";

  function handleNext() {
    if (locationMissing) return;
    onNext();
  }

  return (
    <>
      <StepShell
        stepNumber={stepNumber}
        totalSteps={totalSteps}
        onBack={onBack}
        footer={
          <button
            type="button"
            onClick={handleNext}
            disabled={locationMissing}
            aria-disabled={locationMissing}
            style={{
              width: "100%",
              height: 48,
              borderRadius: 12,
              border: "none",
              background: locationMissing ? "var(--c-line, #d1d5db)" : "var(--c-blue-700, #1d4ed8)",
              fontSize: 15,
              fontWeight: 600,
              color: locationMissing ? "var(--c-ink-3, #6b7280)" : "#fff",
              cursor: locationMissing ? "default" : "pointer",
            }}
          >
            {t("stepLocation.next")}
          </button>
        }
      >
        <div style={{ display: "flex", flexDirection: "column" }}>
          <div style={{ padding: "16px 16px 10px", flexShrink: 0 }}>
            <h2
              style={{
                fontSize: "calc(22px * var(--ui-scale, 1))",
                fontWeight: 700,
                color: "var(--c-ink)",
                margin: 0,
              }}
            >
              {t("stepLocation.title")}
            </h2>
          </div>

          <div style={{ padding: "0 16px 12px", flexShrink: 0 }}>
            <label
              htmlFor="infra-name"
              style={{
                display: "block",
                fontSize: 12,
                fontWeight: 700,
                color: "var(--c-ink-2)",
                letterSpacing: "0.04em",
                textTransform: "uppercase",
                marginBottom: 6,
              }}
            >
              {t("stepLocation.nameLabel")}
            </label>
            <input
              id="infra-name"
              type="text"
              aria-label={t("stepLocation.nameAria")}
              value={infraName}
              maxLength={MAX_NAME_LENGTH}
              placeholder={t("stepLocation.nameExample")}
              onChange={(e) => onChangeInfraName(e.target.value.slice(0, MAX_NAME_LENGTH))}
              style={{
                width: "100%",
                padding: "10px 12px",
                borderRadius: 10,
                border: "1.5px solid var(--c-line)",
                fontSize: 15,
                fontFamily: "var(--font-ui)",
                color: "var(--c-ink)",
                background: "var(--c-card)",
                outline: "none",
                boxSizing: "border-box",
              }}
            />
          </div>

          {/* Collapsed to height 0 (not unmounted) on the description path to keep MapLibre alive. */}
          <div
            style={{
              position: "relative",
              height: mapHeight,
              transition: `height ${MAP_HEIGHT_TRANSITION_MS}ms ease`,
              margin: routeOpen ? 0 : "0 16px",
              borderRadius: 12,
              overflow: "hidden",
              border: routeOpen ? "none" : "1px solid var(--c-line)",
            }}
          >
            <div
              ref={mapContainerRef}
              style={{
                height: "100%",
                width: "100%",
                pointerEvents: routeOpen ? "none" : "auto",
              }}
            />

            {showBuildingsOverlay && (
              <MapLoadingIndicator
                label={t("stepLocation.buildingsLoading", {
                  defaultValue: "Loading map and buildings…",
                })}
                style={{
                  position: "absolute",
                  inset: 0,
                  gap: 8,
                  background: "rgba(255,255,255,0.45)",
                  zIndex: 10,
                  pointerEvents: "none",
                }}
              />
            )}

            {!routeOpen && hasPin && (
              <div
                style={{
                  position: "absolute",
                  top: 8,
                  left: 10,
                  zIndex: 1000,
                  fontFamily: "monospace",
                  fontSize: 11,
                  color: "var(--c-ink)",
                  background: "var(--c-card)",
                  padding: "2px 6px",
                  borderRadius: 4,
                  pointerEvents: "none",
                }}
              >
                {displayCoords.lat.toFixed(5)}, {displayCoords.lng.toFixed(5)}
              </div>
            )}

            {!routeOpen && (
              <button
                type="button"
                onClick={requestLocation}
                disabled={gpsStatus === "acquiring"}
                aria-label={t("stepLocation.useMyLocation")}
                title={t("stepLocation.useMyLocation")}
                style={{
                  position: "absolute",
                  top: 8,
                  right: 8,
                  zIndex: 1000,
                  width: 40,
                  height: 40,
                  borderRadius: 8,
                  border: "1px solid var(--c-line, #e5e7eb)",
                  background: "var(--c-card)",
                  color: "var(--c-blue-700, #1d4ed8)",
                  cursor: gpsStatus === "acquiring" ? "default" : "pointer",
                  opacity: gpsStatus === "acquiring" ? 0.7 : 1,
                  display: "grid",
                  placeItems: "center",
                  boxShadow: "0 1px 3px rgba(0,0,0,0.15)",
                }}
              >
                {gpsStatus === "acquiring" ? (
                  <div
                    role="status"
                    aria-label={t("stepLocation.gpsStatus", { status: gpsStatus })}
                    style={{
                      width: 18,
                      height: 18,
                      borderRadius: "50%",
                      border: "2px solid var(--c-line, #e5e7eb)",
                      borderTopColor: "var(--c-blue-700, #1d4ed8)",
                      animation: "spin 0.7s linear infinite",
                    }}
                  />
                ) : (
                  <svg
                    width="18"
                    height="18"
                    viewBox="0 0 24 24"
                    fill="none"
                    stroke="currentColor"
                    strokeWidth="2"
                    strokeLinecap="round"
                    strokeLinejoin="round"
                    aria-hidden="true"
                  >
                    <circle cx="12" cy="12" r="3" />
                    <path d="M12 2v3M12 19v3M2 12h3M19 12h3" />
                  </svg>
                )}
              </button>
            )}

            {!routeOpen && gpsStatus !== "idle" && (
              <div
                style={{
                  position: "absolute",
                  top: 52,
                  right: 14,
                  zIndex: 1000,
                  width: 8,
                  height: 8,
                  borderRadius: 999,
                  background:
                    gpsStatus === "acquired"
                      ? "#22c55e"
                      : gpsStatus === "acquiring"
                        ? "#f59e0b"
                        : "#ef4444",
                  boxShadow: "0 0 0 2px #fff",
                  pointerEvents: "none",
                }}
                aria-label={
                  gpsStatus === "acquired" && accuracy !== null
                    ? t("stepLocation.gpsStatusAccuracy", {
                        status: gpsStatus,
                        meters: Math.round(accuracy),
                      })
                    : t("stepLocation.gpsStatus", { status: gpsStatus })
                }
              />
            )}

            {!routeOpen && <BuildingDetailsSheet building={selectedBuilding} />}

            {!routeOpen && outsideArea && (
              <div
                style={{
                  position: "absolute",
                  bottom: 8,
                  left: 8,
                  right: 8,
                  zIndex: 1001,
                  background: "#fee2e2",
                  border: "1px solid #dc2626",
                  borderRadius: 8,
                  padding: "8px 10px",
                  fontSize: 12,
                  color: "#991b1b",
                  display: "flex",
                  flexDirection: "column",
                  alignItems: "center",
                  gap: 6,
                }}
              >
                <span style={{ fontWeight: 600, textAlign: "center" }}>
                  {t("stepLocation.outsideArea")}
                </span>
                {onChangeCrisis && (
                  <button
                    type="button"
                    onClick={onChangeCrisis}
                    style={{
                      background: "#dc2626",
                      color: "#fff",
                      border: "none",
                      borderRadius: 6,
                      padding: "4px 12px",
                      fontSize: 12,
                      fontWeight: 600,
                      cursor: "pointer",
                    }}
                  >
                    {t("stepLocation.changeCrisis")}
                  </button>
                )}
              </div>
            )}
          </div>

          <div style={{ padding: "12px 16px 24px", flexShrink: 0 }}>
            {gpsFailed && (
              <div
                style={{
                  marginBottom: 10,
                  background: "#fffbeb",
                  border: "1px solid #f59e0b",
                  borderRadius: 8,
                  padding: "8px 10px",
                  fontSize: 12,
                  color: "#92400e",
                  lineHeight: 1.4,
                }}
              >
                {t("stepLocation.locationBlocked")}
              </div>
            )}
            <details
              ref={routeDetailsRef}
              open={routeOpen}
              onToggle={(e) => {
                handleRouteToggle((e.currentTarget as HTMLDetailsElement).open);
              }}
              style={{ marginBottom: 12 }}
            >
              <summary
                style={{
                  fontSize: 13,
                  color: "var(--c-blue-700, #1d4ed8)",
                  cursor: "pointer",
                  fontWeight: 500,
                  listStyle: "none",
                  display: "flex",
                  alignItems: "center",
                  gap: 6,
                }}
              >
                <svg
                  width="13"
                  height="13"
                  viewBox="0 0 24 24"
                  fill="none"
                  stroke="currentColor"
                  strokeWidth="2"
                  strokeLinecap="round"
                  strokeLinejoin="round"
                  aria-hidden="true"
                >
                  <path d="M21 10c0 7-9 13-9 13s-9-6-9-13a9 9 0 0118 0z" />
                  <circle cx="12" cy="10" r="3" />
                </svg>
                {routeOpen
                  ? t("stepLocation.useMapInstead", {
                      defaultValue: "Use the map to drop a pin instead",
                    })
                  : t("stepLocation.noGps")}
              </summary>

              <div style={{ marginTop: 8 }}>
                <label
                  htmlFor="location-route-description"
                  style={{
                    display: "block",
                    fontSize: 11,
                    fontWeight: 600,
                    color: "var(--c-ink-2)",
                    marginBottom: 3,
                  }}
                >
                  {t("stepLocation.routeLabel")}
                </label>
                <div style={{ position: "relative" }}>
                  <textarea
                    id="location-route-description"
                    value={routeDescription}
                    rows={3}
                    maxLength={MAX_ROUTE_LENGTH}
                    placeholder={t("stepLocation.routeExample")}
                    onFocus={handleRouteFocus}
                    onBlur={handleRouteBlur}
                    onChange={(e) => handleRouteChange(e.target.value.slice(0, MAX_ROUTE_LENGTH))}
                    style={{
                      width: "100%",
                      padding: "10px 12px calc(42px * var(--ui-scale, 1))",
                      borderRadius: 10,
                      border: "1.5px solid var(--c-line, #e5e7eb)",
                      fontSize: 13,
                      fontFamily: "var(--font-ui)",
                      color: "var(--c-ink)",
                      background: "var(--c-card)",
                      outline: "none",
                      resize: "vertical",
                      lineHeight: 1.5,
                      boxSizing: "border-box",
                    }}
                  />
                  <div
                    style={{
                      position: "absolute",
                      right: 8,
                      bottom: 10,
                      maxWidth: "calc(100% - 16px)",
                    }}
                  >
                    <VoiceNoteRecorder
                      value={routeDescription}
                      onChange={handleRouteChange}
                      maxLength={MAX_ROUTE_LENGTH}
                    />
                  </div>
                </div>
                <div style={{ display: "flex", justifyContent: "flex-end", marginTop: 4 }}>
                  <span style={{ fontSize: 11, color: "var(--c-ink-4)" }}>
                    {routeDescription.length}/{MAX_ROUTE_LENGTH}
                  </span>
                </div>
              </div>
            </details>
          </div>
        </div>
      </StepShell>
    </>
  );
}
