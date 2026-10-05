import maplibregl from "maplibre-gl";
import "maplibre-gl/dist/maplibre-gl.css";
import { useEffect, useMemo, useRef, useState } from "react";
import {
  fetchAreaGeometry,
  fetchCountries,
  fetchCountriesGeometry,
  searchAreas,
} from "../../api/admin";
import type {
  AreaSearchHit,
  CrisisAreaValue,
  GeoJSONMultiPolygon,
  GeoJSONPolygon,
  SearchSelection,
} from "../../types/admin";

type Mode = "search" | "upload" | "draw" | "countries-only";

interface Props {
  value: CrisisAreaValue;
  onChange: (next: CrisisAreaValue) => void;
  // Countries picked in the form; scopes search and gates the countries-only tab.
  countries: string[];
}

export function bboxFromPolygon(
  geom: GeoJSONPolygon | GeoJSONMultiPolygon,
): [number, number, number, number] {
  let minLon = Number.POSITIVE_INFINITY;
  let minLat = Number.POSITIVE_INFINITY;
  let maxLon = Number.NEGATIVE_INFINITY;
  let maxLat = Number.NEGATIVE_INFINITY;
  const visit = (lon: number, lat: number) => {
    if (lon < minLon) minLon = lon;
    if (lat < minLat) minLat = lat;
    if (lon > maxLon) maxLon = lon;
    if (lat > maxLat) maxLat = lat;
  };
  if (geom.type === "Polygon") {
    for (const ring of geom.coordinates) for (const [lon, lat] of ring) visit(lon, lat);
  } else {
    for (const poly of geom.coordinates)
      for (const ring of poly) for (const [lon, lat] of ring) visit(lon, lat);
  }
  return [minLon, minLat, maxLon, maxLat];
}

// The backend's `country` param is single-valued, so with 2+ countries fan out
// one request per country and merge round-robin (deduped by id) so each country
// is fairly represented. `limit` is the post-merge cap; each call also asks for
// `limit`, accepting some over-fetch.
async function searchAreasScoped(
  q: string,
  opts: {
    countries: string[];
    signal?: AbortSignal;
    source?: "osm";
    limit?: number;
  },
): Promise<AreaSearchHit[]> {
  const { countries, signal, source, limit = 10 } = opts;
  if (countries.length <= 1) {
    return searchAreas(q, { country: countries[0], signal, source, limit });
  }
  const perCountry = await Promise.all(
    countries.map((c) => searchAreas(q, { country: c, signal, source, limit })),
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

function extractGeometry(raw: unknown): GeoJSONPolygon | GeoJSONMultiPolygon | { error: string } {
  if (typeof raw !== "object" || raw === null)
    return { error: "Top-level value must be an object." };
  const obj = raw as { type?: unknown; geometry?: unknown; features?: unknown };
  if (obj.type === "Feature") return extractGeometry(obj.geometry);
  if (obj.type === "FeatureCollection") {
    const feats = Array.isArray(obj.features) ? obj.features : [];
    if (feats.length !== 1) return { error: "FeatureCollection must contain exactly one feature." };
    return extractGeometry((feats[0] as { geometry?: unknown }).geometry);
  }
  if (obj.type === "Polygon" || obj.type === "MultiPolygon") {
    return obj as unknown as GeoJSONPolygon | GeoJSONMultiPolygon;
  }
  return { error: "GeoJSON must be a Polygon or MultiPolygon." };
}

export function CrisisAreaPicker({ value, onChange, countries }: Props) {
  const initialMode: Mode =
    value.mode === "search" || value.mode === "upload" || value.mode === "draw"
      ? value.mode
      : "search";
  const [mode, setMode] = useState<Mode>(
    value.mode === "countries-only" ? "countries-only" : initialMode,
  );

  // Per-mode cache so switching tabs doesn't wipe each tab's work. Only the
  // active tab's entry is emitted upstream.
  const [cache, setCache] = useState<Partial<Record<Mode, CrisisAreaValue>>>(() => {
    const init: Partial<Record<Mode, CrisisAreaValue>> = {};
    if (value.mode === "search") init.search = value;
    else if (value.mode === "upload") init.upload = value;
    else if (value.mode === "draw") init.draw = value;
    else if (value.mode === "countries-only") init["countries-only"] = value;
    return init;
  });

  // Sync parent value changes into the active mode's cache for later restore.
  useEffect(() => {
    if (value.mode === "search") setCache((c) => ({ ...c, search: value }));
    else if (value.mode === "upload") setCache((c) => ({ ...c, upload: value }));
    else if (value.mode === "draw") setCache((c) => ({ ...c, draw: value }));
    else if (value.mode === "countries-only") setCache((c) => ({ ...c, "countries-only": value }));
  }, [value]);

  // Keep mode roughly in sync if value flips via parent (e.g. edit mode opening).
  useEffect(() => {
    if (value.mode === "search" || value.mode === "upload" || value.mode === "draw")
      setMode(value.mode);
    else if (value.mode === "countries-only") setMode("countries-only");
  }, [value.mode]);

  const switchMode = (next: Mode) => {
    if (next === mode) return;
    setMode(next);
    // Restore the destination tab's cached value, else its default.
    const cached = cache[next];
    if (cached) onChange(cached);
    else if (next === "countries-only") onChange({ mode: "countries-only" });
    else if (next === "search") onChange({ mode: "search", selections: [] });
    else onChange({ mode: "empty" });
  };

  return (
    <div className="field" style={{ marginBottom: 12 }}>
      <span className="label-eyebrow">Crisis area</span>
      <div
        style={{
          display: "grid",
          gridTemplateColumns: "1fr 1fr 1fr 1fr",
          gap: 4,
          padding: 4,
          background: "var(--c-card-2)",
          border: "1px solid var(--c-line)",
          borderRadius: 10,
          marginBottom: 8,
        }}
      >
        {(["search", "upload", "draw", "countries-only"] as Mode[]).map((m) => {
          const sel = mode === m;
          const hasCached = cache[m] !== undefined;
          return (
            <button
              key={m}
              type="button"
              onClick={() => switchMode(m)}
              title={hasCached && !sel ? "Restores your previous choice for this tab" : undefined}
              style={{
                padding: "8px 10px",
                fontSize: 13.5,
                fontWeight: sel ? 700 : 500,
                background: sel ? "var(--c-blue-50)" : "transparent",
                color: sel ? "var(--c-blue-700)" : "var(--c-ink-2)",
                border: sel ? "1px solid var(--c-blue-200)" : "1px solid transparent",
                borderRadius: 7,
                cursor: "pointer",
                fontFamily: "inherit",
                display: "inline-flex",
                alignItems: "center",
                justifyContent: "center",
                gap: 6,
                transition: "background 100ms ease, color 100ms ease",
              }}
              onMouseEnter={(e) => {
                if (!sel) e.currentTarget.style.background = "var(--c-card)";
              }}
              onMouseLeave={(e) => {
                if (!sel) e.currentTarget.style.background = "transparent";
              }}
            >
              <span>
                {m === "search" && "Search"}
                {m === "upload" && "Upload GeoJSON"}
                {m === "draw" && "Draw"}
                {m === "countries-only" && "Countries only"}
              </span>
              {hasCached && !sel && (
                <span
                  aria-hidden="true"
                  title="Saved"
                  style={{
                    width: 6,
                    height: 6,
                    borderRadius: 999,
                    background: "var(--c-blue-600)",
                  }}
                />
              )}
            </button>
          );
        })}
      </div>

      {/* All mode panels stay mounted (toggled via `display`) so an in-progress
          drawing survives a tab flip. Hidden maps get a 0×0 container and
          resize via a ResizeObserver when shown again. */}
      <div style={{ display: mode === "search" ? "block" : "none" }}>
        <SearchMode value={value} onChange={onChange} countries={countries} />
      </div>
      <div style={{ display: mode === "upload" ? "block" : "none" }}>
        <UploadMode value={value} onChange={onChange} />
      </div>
      <div style={{ display: mode === "draw" ? "block" : "none" }}>
        <DrawMode
          value={value}
          onChange={onChange}
          countries={countries}
          visible={mode === "draw"}
        />
      </div>
      <div style={{ display: mode === "countries-only" ? "block" : "none" }}>
        <CountriesOnlyMode countries={countries} />
      </div>

      {/* Draw mode owns its own map; rendering the preview again would just duplicate it. */}
      {mode !== "draw" && <PreviewMap value={value} countries={countries} />}
    </div>
  );
}

// Stable identity for a selection, used to dedupe picks and as a React key.
function selectionKey(s: SearchSelection): string {
  return s.source === "overture" ? `ov:${s.division_id}` : `osm:${s.osm_id}`;
}

function hitToSelection(h: AreaSearchHit): SearchSelection | null {
  const source = h.source ?? "overture";
  if (source === "osm") {
    if (!h.geometry) return null; // OSM hit without inline polygon is unusable
    return { source: "osm", osm_id: h.id, preview: h, polygon: h.geometry };
  }
  return { source: "overture", division_id: h.id, preview: h };
}

function SearchMode({
  value,
  onChange,
  countries,
}: {
  value: CrisisAreaValue;
  onChange: (next: CrisisAreaValue) => void;
  countries: string[];
}) {
  const [q, setQ] = useState("");
  const [hits, setHits] = useState<AreaSearchHit[]>([]);
  const [loading, setLoading] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  const selections = value.mode === "search" ? value.selections : [];

  // Always emit a `search` value so the mode stays consistent when the list empties.
  const emit = (next: SearchSelection[]) => onChange({ mode: "search", selections: next });

  const addHit = (h: AreaSearchHit) => {
    const sel = hitToSelection(h);
    if (!sel) return;
    const key = selectionKey(sel);
    if (selections.some((s) => selectionKey(s) === key)) {
      setQ("");
      setHits([]);
      return; // already picked
    }
    emit([...selections, sel]);
    setQ("");
    setHits([]);
  };

  const removeAt = (idx: number) => emit(selections.filter((_, i) => i !== idx));
  const replaceAt = (idx: number, sel: SearchSelection) =>
    emit(selections.map((s, i) => (i === idx ? sel : s)));

  useEffect(() => {
    const trimmed = q.trim();
    if (trimmed.length < 2) {
      setHits([]);
      setLoading(false);
      setErr(null);
      return;
    }
    const ctrl = new AbortController();
    const t = window.setTimeout(async () => {
      setLoading(true);
      setErr(null);
      try {
        const next = await searchAreasScoped(trimmed, { countries, signal: ctrl.signal });
        setHits(next);
      } catch (e) {
        if ((e as { name?: string }).name === "AbortError") return;
        setErr(e instanceof Error ? e.message : String(e));
      } finally {
        setLoading(false);
      }
    }, 250);
    return () => {
      window.clearTimeout(t);
      ctrl.abort();
    };
  }, [q, countries]);

  // Lazy-load Overture polygons (search hits carry only a bbox; OSM hits are
  // inline). A 404 flags the row `polygonMissing` so it offers OSM matches.
  // Results apply against the latest list (via a ref) so concurrent fetches
  // don't clobber each other.
  const selectionsRef = useRef(selections);
  selectionsRef.current = selections;
  const onChangeRef = useRef(onChange);
  onChangeRef.current = onChange;
  // Stable key for the pending set, so the effect doesn't re-run on every render.
  const pendingKey = selections
    .filter(
      (s): s is Extract<SearchSelection, { source: "overture" }> =>
        s.source === "overture" && s.polygon === undefined && !s.polygonMissing,
    )
    .map((s) => s.division_id)
    .join("|");
  // biome-ignore lint/correctness/useExhaustiveDependencies: keyed on `pendingKey`; the effect reads the freshest list via `selectionsRef`/`onChangeRef` and re-derives the pending set inside, so the array identities are intentionally excluded.
  useEffect(() => {
    const pending = selectionsRef.current.filter(
      (s): s is Extract<SearchSelection, { source: "overture" }> =>
        s.source === "overture" && s.polygon === undefined && !s.polygonMissing,
    );
    if (pending.length === 0) return;
    const ctrl = new AbortController();
    let cancelled = false;
    void (async () => {
      const resolved = await Promise.all(
        pending.map(async (s) => {
          try {
            const polygon = await fetchAreaGeometry(s.division_id, { signal: ctrl.signal });
            return { id: s.division_id, polygon };
          } catch (e) {
            if ((e as { name?: string } | null)?.name === "AbortError") return null;
            // Non-fatal: the backend resolves geometry server-side at save
            // time, so the row is still submittable. Leave it un-flagged.
            console.warn("Failed to load admin area geometry preview:", e);
            return null;
          }
        }),
      );
      if (cancelled) return;
      const byId = new Map<string, GeoJSONPolygon | GeoJSONMultiPolygon | null>();
      for (const r of resolved) if (r) byId.set(r.id, r.polygon);
      if (byId.size === 0) return;
      const next = selectionsRef.current.map((s): SearchSelection => {
        if (s.source !== "overture" || !byId.has(s.division_id)) return s;
        const polygon = byId.get(s.division_id) ?? null;
        return polygon === null
          ? {
              source: "overture",
              division_id: s.division_id,
              preview: s.preview,
              polygonMissing: true,
            }
          : { source: "overture", division_id: s.division_id, preview: s.preview, polygon };
      });
      onChangeRef.current({ mode: "search", selections: next });
    })();
    return () => {
      cancelled = true;
      ctrl.abort();
    };
  }, [pendingKey]);

  return (
    <div>
      <input
        type="text"
        placeholder="Search city, region, country…"
        value={q}
        onChange={(e) => setQ(e.target.value)}
        style={{
          width: "100%",
          padding: "11px 14px",
          border: "1px solid var(--c-line)",
          borderRadius: 8,
          fontSize: 15,
          marginBottom: 6,
        }}
      />
      {selections.length > 0 && (
        <div style={{ display: "flex", flexDirection: "column", gap: 6, marginBottom: 8 }}>
          {selections.map((s, idx) => (
            <SelectionRow
              key={selectionKey(s)}
              selection={s}
              countries={countries}
              onRemove={() => removeAt(idx)}
              onReplace={(next) => replaceAt(idx, next)}
            />
          ))}
        </div>
      )}
      {loading && (
        <div style={{ fontSize: 14, color: "var(--c-ink-3)", padding: 6 }}>Searching…</div>
      )}
      {err && <div style={{ fontSize: 14, color: "var(--c-danger)", padding: 6 }}>{err}</div>}
      {!loading && !err && q.trim().length >= 2 && hits.length === 0 && (
        <div style={{ fontSize: 14, color: "var(--c-ink-3)", padding: 6 }}>
          No matches in Overture or OpenStreetMap. Try a different name, or use{" "}
          <strong>Upload</strong> / <strong>Draw</strong> to provide a polygon.
        </div>
      )}
      {hits.length > 0 && (
        <div
          style={{
            border: "1px solid var(--c-line)",
            borderRadius: 8,
            maxHeight: 220,
            overflowY: "auto",
          }}
        >
          {hits.map((h) => {
            const source = h.source ?? "overture";
            const already = selections.some(
              (s) => selectionKey(s) === (source === "osm" ? `osm:${h.id}` : `ov:${h.id}`),
            );
            return (
              <button
                key={h.id}
                type="button"
                disabled={already}
                onClick={() => addHit(h)}
                style={{
                  width: "100%",
                  textAlign: "start",
                  padding: "9px 12px",
                  background: "transparent",
                  border: "none",
                  borderBottom: "1px solid var(--c-line)",
                  cursor: already ? "default" : "pointer",
                  opacity: already ? 0.5 : 1,
                  display: "flex",
                  gap: 8,
                  alignItems: "center",
                  fontSize: 15,
                }}
              >
                <span style={{ fontWeight: 700 }}>{h.name}</span>
                <span style={{ color: "var(--c-ink-3)", flex: 1, fontSize: 14 }}>
                  {h.parents.join(", ")}
                </span>
                {already ? (
                  <span className="chip" style={{ padding: "2px 9px", fontSize: 13 }}>
                    added
                  </span>
                ) : (
                  <span className="chip" style={{ padding: "2px 9px", fontSize: 13 }}>
                    {h.subtype}
                  </span>
                )}
                <span
                  className="mono"
                  style={{
                    fontSize: 13,
                    color: source === "osm" ? "var(--c-blue-700)" : "var(--c-ink-3)",
                    border: `1px solid ${source === "osm" ? "var(--c-blue-200)" : "var(--c-line)"}`,
                    borderRadius: 4,
                    padding: "1px 6px",
                  }}
                >
                  {source === "osm" ? "OSM" : "Overture"}
                </span>
              </button>
            );
          })}
        </div>
      )}
      <div style={{ fontSize: 13, color: "var(--c-ink-3)", marginTop: 6 }}>
        Pick one or more places; the crisis area is their union.
        {countries.length > 0 && (
          <>
            {" "}
            Scoped to <span className="mono">{countries.join(", ")}</span>.
          </>
        )}
      </div>
    </div>
  );
}

// One picked place. When an Overture pick is `polygonMissing`, the row fetches
// OSM alternatives for the same name and lets the admin swap it.
function SelectionRow({
  selection,
  countries,
  onRemove,
  onReplace,
}: {
  selection: SearchSelection;
  countries: string[];
  onRemove: () => void;
  onReplace: (sel: SearchSelection) => void;
}) {
  const preview = selection.preview;
  const missing = selection.source === "overture" && selection.polygonMissing === true;
  const loadingPoly =
    selection.source === "overture" && selection.polygon === undefined && !missing;

  const [osmAlts, setOsmAlts] = useState<AreaSearchHit[]>([]);
  const [osmAltsLoading, setOsmAltsLoading] = useState(false);
  const [osmAltsErr, setOsmAltsErr] = useState<string | null>(null);

  useEffect(() => {
    if (!missing) {
      setOsmAlts([]);
      setOsmAltsLoading(false);
      setOsmAltsErr(null);
      return;
    }
    const ctrl = new AbortController();
    setOsmAltsLoading(true);
    setOsmAltsErr(null);
    (async () => {
      try {
        const next = await searchAreasScoped(preview.name, {
          countries,
          source: "osm",
          signal: ctrl.signal,
        });
        setOsmAlts(next);
      } catch (e) {
        if ((e as { name?: string }).name === "AbortError") return;
        setOsmAltsErr(e instanceof Error ? e.message : String(e));
      } finally {
        setOsmAltsLoading(false);
      }
    })();
    return () => ctrl.abort();
  }, [missing, preview.name, countries]);

  return (
    <div
      style={{
        background: missing ? "var(--c-warn-bg)" : "var(--c-blue-50)",
        border: `1px solid ${missing ? "var(--c-warn)" : "var(--c-blue-200)"}`,
        borderRadius: 8,
        padding: "9px 12px",
      }}
    >
      <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
        <span style={{ fontWeight: 600, fontSize: 14.5 }}>{preview.name}</span>
        {preview.parents.length > 0 && (
          <span style={{ color: "var(--c-ink-3)", fontSize: 13.5 }}>
            · {preview.parents.join(", ")}
          </span>
        )}
        <span style={{ flex: 1 }} />
        <span
          className="mono"
          style={{
            fontSize: 12,
            color: selection.source === "osm" ? "var(--c-blue-700)" : "var(--c-ink-3)",
            border: `1px solid ${
              selection.source === "osm" ? "var(--c-blue-200)" : "var(--c-line)"
            }`,
            borderRadius: 4,
            padding: "1px 6px",
          }}
        >
          {selection.source === "osm" ? "OSM" : "Overture"}
        </span>
        {loadingPoly && <span style={{ fontSize: 12, color: "var(--c-ink-3)" }}>loading…</span>}
        <button
          type="button"
          onClick={onRemove}
          className="chip"
          style={{ padding: "3px 10px", fontSize: 13, cursor: "pointer" }}
          title="Remove this place"
        >
          remove
        </button>
      </div>
      {missing && (
        <div style={{ marginTop: 8 }}>
          <div style={{ fontSize: 13, lineHeight: 1.5, color: "var(--c-ink-2)" }}>
            No polygon for <strong>{preview.name}</strong> in our dataset. It won't be included
            until you pick an OpenStreetMap match or remove it.
            {osmAltsLoading && " Searching OpenStreetMap…"}
            {!osmAltsLoading &&
              !osmAltsErr &&
              osmAlts.length === 0 &&
              " No OpenStreetMap match either. Remove it and use Draw or Upload instead."}
            {osmAltsErr && ` ${osmAltsErr}`}
          </div>
          {osmAlts.length > 0 && (
            <div
              style={{
                marginTop: 8,
                background: "var(--c-card)",
                border: "1px solid var(--c-line)",
                borderRadius: 6,
                maxHeight: 200,
                overflowY: "auto",
              }}
            >
              {osmAlts.map((h, i) => (
                <button
                  key={h.id}
                  type="button"
                  onClick={() => {
                    if (!h.geometry) return;
                    onReplace({ source: "osm", osm_id: h.id, preview: h, polygon: h.geometry });
                  }}
                  style={{
                    width: "100%",
                    textAlign: "start",
                    padding: "8px 12px",
                    background: "transparent",
                    border: "none",
                    borderBottom: i === osmAlts.length - 1 ? "none" : "1px solid var(--c-line-2)",
                    cursor: "pointer",
                    display: "flex",
                    gap: 8,
                    alignItems: "center",
                    fontSize: 14,
                    color: "var(--c-ink)",
                  }}
                >
                  <span style={{ fontWeight: 600 }}>{h.name}</span>
                  <span style={{ color: "var(--c-ink-3)", flex: 1, fontSize: 13 }}>
                    {h.parents.join(", ")}
                  </span>
                  <span className="mono" style={{ fontSize: 12, color: "var(--c-ink-3)" }}>
                    {h.subtype}
                  </span>
                </button>
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  );
}

function UploadMode({
  value,
  onChange,
}: {
  value: CrisisAreaValue;
  onChange: (next: CrisisAreaValue) => void;
}) {
  const [err, setErr] = useState<string | null>(null);
  const filename = value.mode === "upload" ? value.filename : null;
  const inputRef = useRef<HTMLInputElement>(null);

  const handleFile = async (file: File) => {
    setErr(null);
    if (!file.name.toLowerCase().endsWith(".geojson")) {
      setErr("File must have a .geojson extension.");
      return;
    }
    try {
      const text = await file.text();
      let parsed: unknown;
      try {
        parsed = JSON.parse(text);
      } catch {
        setErr("File is not valid JSON.");
        return;
      }
      const result = extractGeometry(parsed);
      if ("error" in result) {
        setErr(result.error);
        return;
      }
      const bbox = bboxFromPolygon(result);
      onChange({ mode: "upload", polygon: result, filename: file.name, bbox });
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    }
  };

  return (
    <div>
      <button
        type="button"
        onClick={() => inputRef.current?.click()}
        onDragOver={(e) => e.preventDefault()}
        onDrop={(e) => {
          e.preventDefault();
          const file = e.dataTransfer.files?.[0];
          if (file) void handleFile(file);
        }}
        style={{
          width: "100%",
          padding: 18,
          border: "1.5px dashed var(--c-line)",
          borderRadius: 10,
          background: "var(--c-card)",
          cursor: "pointer",
          fontSize: 15,
          color: "var(--c-ink-2)",
          textAlign: "center",
        }}
      >
        {filename ? (
          <>
            <div style={{ fontWeight: 700 }}>{filename}</div>
            <div style={{ fontSize: 13, color: "var(--c-ink-3)", marginTop: 4 }}>
              Click or drop another file to replace.
            </div>
          </>
        ) : (
          <>
            Drop a <span className="mono">.geojson</span> file here, or click to choose.
          </>
        )}
      </button>
      <input
        ref={inputRef}
        type="file"
        accept=".geojson,application/geo+json,application/json"
        style={{ display: "none" }}
        onChange={(e) => {
          const f = e.target.files?.[0];
          if (f) void handleFile(f);
          e.target.value = "";
        }}
      />
      {err && <div style={{ fontSize: 14, color: "var(--c-danger)", marginTop: 8 }}>{err}</div>}
      <div style={{ fontSize: 13, color: "var(--c-ink-3)", marginTop: 8 }}>
        Shapefile (<span className="mono">.zip</span>) and KML/KMZ support coming soon. Convert to
        GeoJSON via QGIS or <span className="mono">ogr2ogr</span> for now.
      </div>
    </div>
  );
}

function DrawMode({
  value,
  onChange,
  countries,
  visible,
}: {
  value: CrisisAreaValue;
  onChange: (next: CrisisAreaValue) => void;
  countries: string[];
  visible: boolean;
}) {
  const containerRef = useRef<HTMLDivElement>(null);
  const mapRef = useRef<maplibregl.Map | null>(null);
  const loadedRef = useRef(false);
  const onChangeRef = useRef(onChange);
  onChangeRef.current = onChange;

  // Hydrate once from the parent value, dropping GeoJSON's closing duplicate
  // vertex (the in-progress ring is unclosed).
  const [points, setPoints] = useState<Array<[number, number]>>(() => {
    if (value.mode === "draw") {
      const ring = value.polygon.coordinates[0];
      return ring.slice(0, ring.length - 1) as Array<[number, number]>;
    }
    return [];
  });
  const [finalized, setFinalized] = useState<boolean>(value.mode === "draw");

  // Refs mirror state so the map click handler reads fresh values without re-binding.
  const pointsRef = useRef(points);
  pointsRef.current = points;
  const finalizedRef = useRef(finalized);
  finalizedRef.current = finalized;

  const applySources = () => {
    const map = mapRef.current;
    if (!map || !loadedRef.current) return;
    const fill = map.getSource("draw-fill") as maplibregl.GeoJSONSource | undefined;
    const line = map.getSource("draw-line") as maplibregl.GeoJSONSource | undefined;
    const pts = map.getSource("draw-points") as maplibregl.GeoJSONSource | undefined;
    if (!fill || !line || !pts) return;
    const ps = pointsRef.current;
    const isFinal = finalizedRef.current;
    if (isFinal && ps.length >= 3) {
      const ring: number[][] = [...ps.map((p) => [p[0], p[1]]), [ps[0][0], ps[0][1]]];
      fill.setData({
        type: "Feature",
        geometry: { type: "Polygon", coordinates: [ring] },
        properties: {},
      });
      line.setData({ type: "FeatureCollection", features: [] });
      pts.setData({ type: "FeatureCollection", features: [] });
    } else {
      fill.setData({ type: "FeatureCollection", features: [] });
      if (ps.length >= 2) {
        line.setData({
          type: "Feature",
          geometry: { type: "LineString", coordinates: ps.map((p) => [p[0], p[1]]) },
          properties: {},
        });
      } else {
        line.setData({ type: "FeatureCollection", features: [] });
      }
      pts.setData({
        type: "FeatureCollection",
        features: ps.map((p, i) => ({
          type: "Feature",
          geometry: { type: "Point", coordinates: [p[0], p[1]] },
          properties: { index: i, is_first: i === 0 },
        })),
      });
    }
    map.getCanvas().style.cursor = isFinal ? "" : "crosshair";
  };

  const finalize = () => {
    const pts = pointsRef.current;
    if (pts.length < 3) return;
    const ring: number[][] = [...pts.map((p) => [p[0], p[1]]), [pts[0][0], pts[0][1]]];
    const polygon: GeoJSONPolygon = { type: "Polygon", coordinates: [ring] };
    const bbox = bboxFromPolygon(polygon);
    setFinalized(true);
    onChangeRef.current({ mode: "draw", polygon, bbox });
  };

  const startEdit = () => {
    setFinalized(false);
    // Clear the parent value while re-drawing; Finish commits again.
    onChangeRef.current({ mode: "empty" });
  };

  const undo = () => {
    setPoints((prev) => prev.slice(0, prev.length - 1));
  };

  const clearAll = () => {
    setPoints([]);
  };

  // Runs once; applySources() updates sources through refs.
  // biome-ignore lint/correctness/useExhaustiveDependencies: map mounts once; mutable state flows through refs
  useEffect(() => {
    if (!containerRef.current) return;
    const map = new maplibregl.Map({
      container: containerRef.current,
      style: {
        version: 8,
        sources: {
          osm: {
            type: "raster",
            tiles: ["https://tile.openstreetmap.org/{z}/{x}/{y}.png"],
            tileSize: 256,
            attribution:
              '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors',
          },
        },
        layers: [{ id: "osm-tiles", type: "raster", source: "osm" }],
      },
      center: [0, 20],
      zoom: 1,
      attributionControl: false,
    });
    mapRef.current = map;

    const handleClick = (e: maplibregl.MapMouseEvent) => {
      if (finalizedRef.current) return;
      const ps = pointsRef.current;
      // Click near the first vertex with at least 3 points closes the ring.
      if (ps.length >= 3) {
        const first = map.project(ps[0]);
        const dx = first.x - e.point.x;
        const dy = first.y - e.point.y;
        if (dx * dx + dy * dy < 12 * 12) {
          finalize();
          return;
        }
      }
      setPoints((prev) => [...prev, [e.lngLat.lng, e.lngLat.lat]]);
    };

    map.on("click", handleClick);
    map.on("load", () => {
      loadedRef.current = true;
      map.addSource("draw-fill", {
        type: "geojson",
        data: { type: "FeatureCollection", features: [] },
      });
      map.addSource("draw-line", {
        type: "geojson",
        data: { type: "FeatureCollection", features: [] },
      });
      map.addSource("draw-points", {
        type: "geojson",
        data: { type: "FeatureCollection", features: [] },
      });
      map.addLayer({
        id: "draw-fill-layer",
        type: "fill",
        source: "draw-fill",
        paint: { "fill-color": "#1670c2", "fill-opacity": 0.3 },
      });
      map.addLayer({
        id: "draw-fill-stroke",
        type: "line",
        source: "draw-fill",
        paint: { "line-color": "#0b3d6e", "line-width": 2.5 },
      });
      map.addLayer({
        id: "draw-line-layer",
        type: "line",
        source: "draw-line",
        paint: {
          "line-color": "#0b3d6e",
          "line-width": 2,
          "line-dasharray": [2, 2],
        },
      });
      map.addLayer({
        id: "draw-points-layer",
        type: "circle",
        source: "draw-points",
        paint: {
          "circle-color": "#ffffff",
          "circle-stroke-color": "#0b3d6e",
          "circle-stroke-width": 2,
          "circle-radius": ["case", ["==", ["get", "is_first"], true], 8, 5],
        },
      });
      applySources();
    });

    return () => {
      loadedRef.current = false;
      map.remove();
      mapRef.current = null;
    };
  }, []);

  // Sync source data whenever interaction state changes.
  // biome-ignore lint/correctness/useExhaustiveDependencies: applySources reads from refs that mirror these
  useEffect(() => {
    applySources();
  }, [points, finalized]);

  // The container can be 0×0 on mount (layout pending) or while its tab is
  // display:none. Without `map.resize()` once it has real size, the canvas
  // keeps the first measurement and half the map renders empty.
  useEffect(() => {
    const el = containerRef.current;
    if (!el) return;
    const ro = new ResizeObserver(() => {
      mapRef.current?.resize();
    });
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  // Also resize on the next frame when shown, for browsers that defer layout.
  useEffect(() => {
    if (!visible) return;
    const id = requestAnimationFrame(() => mapRef.current?.resize());
    return () => cancelAnimationFrame(id);
  }, [visible]);

  // One-shot, best-effort fit to the picked country when Draw opens.
  // biome-ignore lint/correctness/useExhaustiveDependencies: intentional one-shot; re-fitting on every countries change would fight the user
  useEffect(() => {
    if (points.length > 0 || countries.length === 0) return;
    const iso = countries[0];
    let cancelled = false;
    void (async () => {
      try {
        const rows = await fetchCountries();
        if (cancelled) return;
        const name = rows.find((r) => r.iso2 === iso)?.name;
        if (!name) return;
        const hits = await searchAreas(name, { country: iso, limit: 1 });
        if (cancelled || hits.length === 0) return;
        const map = mapRef.current;
        if (!map) return;
        const fit = () => {
          const [w, s, e, n] = hits[0].bbox;
          map.fitBounds(
            [
              [w, s],
              [e, n],
            ],
            { padding: 24, animate: false, maxZoom: 12 },
          );
        };
        if (loadedRef.current) fit();
        else map.once("load", fit);
      } catch {
        // silent fallback to global default
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  const caption = finalized
    ? "Polygon ready. Click Edit to redraw."
    : points.length === 0
      ? countries.length > 0
        ? "Click on the map to add corners. Tip: the map is zoomed to your selected country."
        : "Click on the map to add corners. Tip: pick a country above to zoom there first."
      : points.length < 3
        ? `Drawing: ${points.length} corner${points.length === 1 ? "" : "s"} so far. Add at least 3 to close.`
        : "Click the first corner or Finish to close.";

  return (
    <div>
      <div style={{ fontSize: 14, color: "var(--c-ink-2)", marginBottom: 8, minHeight: 18 }}>
        {caption}
      </div>
      <div
        style={{
          height: 340,
          borderRadius: 8,
          border: "1px solid var(--c-line)",
          overflow: "hidden",
          position: "relative",
        }}
      >
        <div ref={containerRef} style={{ position: "absolute", inset: 0 }} />
      </div>
      <div style={{ display: "flex", gap: 6, marginTop: 6 }}>
        {finalized ? (
          <button
            type="button"
            className="btn secondary"
            style={{ flex: 1, padding: "9px 12px", fontSize: 14 }}
            onClick={startEdit}
          >
            Edit
          </button>
        ) : (
          <>
            <button
              type="button"
              className="btn secondary"
              style={{ flex: 1, padding: "9px 12px", fontSize: 14 }}
              onClick={undo}
              disabled={points.length === 0}
            >
              Undo last
            </button>
            <button
              type="button"
              className="btn secondary"
              style={{ flex: 1, padding: "9px 12px", fontSize: 14 }}
              onClick={clearAll}
              disabled={points.length === 0}
            >
              Clear
            </button>
            <button
              type="button"
              className="btn"
              style={{ flex: 1, padding: "9px 12px", fontSize: 14 }}
              onClick={finalize}
              disabled={points.length < 3}
            >
              Finish
            </button>
          </>
        )}
      </div>
    </div>
  );
}

function CountriesOnlyMode({ countries }: { countries: string[] }) {
  return (
    <div
      style={{
        padding: 14,
        border: "1px solid var(--c-blue-200)",
        background: "var(--c-blue-50)",
        borderRadius: 8,
        fontSize: 14,
        color: "var(--c-ink)",
        lineHeight: 1.5,
      }}
    >
      The crisis area will be the polygon-union of the countries selected above.
      {countries.length === 0 ? (
        <div style={{ marginTop: 4, color: "var(--c-danger)" }}>
          No countries selected yet. Pick at least one in the Countries field.
        </div>
      ) : (
        <div style={{ marginTop: 4 }}>
          Currently: <span className="mono">{countries.join(", ")}</span>
        </div>
      )}
    </div>
  );
}

function rectGeom(bbox: [number, number, number, number]): GeoJSONPolygon {
  const [w, s, e, n] = bbox;
  return {
    type: "Polygon",
    coordinates: [
      [
        [w, s],
        [e, s],
        [e, n],
        [w, n],
        [w, s],
      ],
    ],
  };
}

// Bounding box of all boxes, to frame a multi-place selection without a client-side union.
function combineBounds(
  boxes: [number, number, number, number][],
): [number, number, number, number] {
  let [w, s, e, n] = boxes[0];
  for (const b of boxes) {
    w = Math.min(w, b[0]);
    s = Math.min(s, b[1]);
    e = Math.max(e, b[2]);
    n = Math.max(n, b[3]);
  }
  return [w, s, e, n];
}

function PreviewMap({ value, countries }: { value: CrisisAreaValue; countries: string[] }) {
  const containerRef = useRef<HTMLDivElement>(null);
  const mapRef = useRef<maplibregl.Map | null>(null);
  const loadedRef = useRef(false);

  // Countries-only previews the union of the selected codes, fetched lazily.
  const countriesKey = countries.join(",");
  const [countriesGeom, setCountriesGeom] = useState<GeoJSONPolygon | GeoJSONMultiPolygon | null>(
    null,
  );
  const [countriesGeomLoading, setCountriesGeomLoading] = useState(false);
  useEffect(() => {
    if (value.mode !== "countries-only" || countriesKey === "") {
      setCountriesGeom(null);
      setCountriesGeomLoading(false);
      return;
    }
    const codes = countriesKey.split(",");
    const ctrl = new AbortController();
    setCountriesGeomLoading(true);
    fetchCountriesGeometry(codes, { signal: ctrl.signal })
      .then((geom) => {
        setCountriesGeom(geom);
      })
      .catch((err: unknown) => {
        if ((err as { name?: string } | null)?.name === "AbortError") return;
        console.warn("Failed to load countries-union preview:", err);
        setCountriesGeom(null);
      })
      .finally(() => {
        setCountriesGeomLoading(false);
      });
    return () => ctrl.abort();
  }, [value.mode, countriesKey]);

  // Search overlays every picked place, drawing a bbox until its polygon
  // loads; other modes draw a single shape.
  const drawing = useMemo<{
    geoms: (GeoJSONPolygon | GeoJSONMultiPolygon)[];
    bounds: [number, number, number, number];
    loading: boolean;
  } | null>(() => {
    if (value.mode === "search") {
      if (value.selections.length === 0) return null;
      const geoms: (GeoJSONPolygon | GeoJSONMultiPolygon)[] = [];
      const boxes: [number, number, number, number][] = [];
      let loading = false;
      for (const s of value.selections) {
        boxes.push(s.preview.bbox);
        if (s.polygon) {
          geoms.push(s.polygon);
          continue;
        }
        // No polygon yet: draw the bbox. A `polygonMissing` pick stays a
        // rectangle until swapped for an OSM match.
        geoms.push(rectGeom(s.preview.bbox));
        if (s.source === "overture" && s.polygonMissing !== true) loading = true;
      }
      return { geoms, bounds: combineBounds(boxes), loading };
    }
    if (value.mode === "upload") {
      return { geoms: [value.polygon], bounds: value.bbox, loading: false };
    }
    if (value.mode === "draw") {
      return { geoms: [value.polygon], bounds: value.bbox, loading: false };
    }
    if (value.mode === "countries-only" && countriesGeom !== null) {
      return {
        geoms: [countriesGeom],
        bounds: bboxFromPolygon(countriesGeom),
        loading: false,
      };
    }
    return null;
  }, [value, countriesGeom]);

  useEffect(() => {
    if (!containerRef.current) return;
    const map = new maplibregl.Map({
      container: containerRef.current,
      style: {
        version: 8,
        sources: {
          osm: {
            type: "raster",
            tiles: ["https://tile.openstreetmap.org/{z}/{x}/{y}.png"],
            tileSize: 256,
            attribution:
              '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors',
          },
        },
        layers: [{ id: "osm-tiles", type: "raster", source: "osm" }],
      },
      center: [0, 20],
      zoom: 1,
      attributionControl: false,
    });
    mapRef.current = map;
    map.on("load", () => {
      loadedRef.current = true;
    });
    return () => {
      loadedRef.current = false;
      map.remove();
      mapRef.current = null;
    };
  }, []);

  // As in DrawMode, the container can start 0×0; resize when it gets real size.
  useEffect(() => {
    const el = containerRef.current;
    if (!el) return;
    const ro = new ResizeObserver(() => {
      mapRef.current?.resize();
    });
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  useEffect(() => {
    const map = mapRef.current;
    if (!map) return;
    const apply = () => {
      if (map.getLayer("area-fill")) map.removeLayer("area-fill");
      if (map.getLayer("area-line")) map.removeLayer("area-line");
      if (map.getSource("area")) map.removeSource("area");
      if (!drawing) return;
      const { geoms, bounds } = drawing;
      map.addSource("area", {
        type: "geojson",
        data: {
          type: "FeatureCollection",
          features: geoms.map((geometry) => ({ type: "Feature", properties: {}, geometry })),
        },
      });
      map.addLayer({
        id: "area-fill",
        type: "fill",
        source: "area",
        paint: { "fill-color": "#1670c2", "fill-opacity": 0.3 },
      });
      map.addLayer({
        id: "area-line",
        type: "line",
        source: "area",
        paint: { "line-color": "#0b3d6e", "line-width": 2.5 },
      });
      const [w, s, e, n] = bounds;
      map.fitBounds(
        [
          [w, s],
          [e, n],
        ],
        { padding: 24, animate: false, maxZoom: 12 },
      );
    };

    if (loadedRef.current) apply();
    else map.once("load", apply);
  }, [drawing]);

  const placeholderText =
    value.mode === "empty"
      ? "No area selected yet"
      : value.mode === "search" && value.selections.length === 0
        ? "No places selected yet. Search above to add one or more."
        : value.mode === "countries-only" && countries.length === 0
          ? "Pick at least one country above to preview the union."
          : value.mode === "countries-only" && countriesGeom === null && !countriesGeomLoading
            ? "No polygon found for the selected countries."
            : null;

  return (
    <div style={{ marginTop: 8 }}>
      <span className="label-eyebrow" style={{ display: "block", marginBottom: 4 }}>
        Preview
      </span>
      <div
        style={{
          height: 340,
          borderRadius: 8,
          border: "1px solid var(--c-line)",
          overflow: "hidden",
          position: "relative",
        }}
      >
        <div ref={containerRef} style={{ position: "absolute", inset: 0 }} />
        {placeholderText !== null && (
          <div
            style={{
              position: "absolute",
              inset: 0,
              display: "grid",
              placeItems: "center",
              background: "rgba(255,255,255,0.85)",
              fontSize: 14,
              color: "var(--c-ink-3)",
              pointerEvents: "none",
              textAlign: "center",
              padding: "0 14px",
            }}
          >
            {placeholderText}
          </div>
        )}
        {(drawing?.loading || countriesGeomLoading) && (
          <div
            style={{
              position: "absolute",
              top: 6,
              insetInlineStart: 6,
              padding: "4px 10px",
              background: "rgba(255,255,255,0.92)",
              border: "1px solid var(--c-line)",
              borderRadius: 6,
              fontSize: 13,
              color: "var(--c-ink-2)",
              pointerEvents: "none",
            }}
          >
            Loading polygon…
          </div>
        )}
      </div>
    </div>
  );
}
