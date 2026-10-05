export type CrisisStatus = "inactive" | "active" | "archived";

// The two staff tiers. `admin` is the superset: coordinator powers plus
// managing coordinators and creating admins. Mirrors the server's role
// model in `apps/api/src/api/admin/coordinator_routes.py`.
export type CoordinatorRole = "coordinator" | "admin";

// Mirrors `CoordinatorOut` in `apps/api/src/api/admin/coordinator_routes.py`.
// Citizens are not users (they identify by `client_id`); staff are the only accounts.
export interface Coordinator {
  id: string;
  email: string;
  // Server sends one of `CoordinatorRole`; typed wide as the API contract
  // is a free `str` and the UI must not crash on an unexpected value.
  role: string;
  // ISO-8601 strings from the API.
  created_at: string;
  banned_until: string | null;
  is_disabled: boolean;
  // The caller's own row. The UI hides destructive actions on it; the server
  // also rejects them (400 `cannot_modify_self`).
  is_self: boolean;
}

// Per-crisis public visibility. Mirrors `PublicVisibility` in
// `apps/api/src/api/domain/schemas.py`.
// - "none": heatmap and stats endpoints return 404; the crisis still appears
//   in the citizen `GET /crises` picker.
// - "aggregate_view": default; H3 heatmap with a k-anonymity floor.
// - "buildings": public surface currently behaves like "none".
// - "full": same as "buildings"; the admin UI requires an explicit confirm.
export type PublicVisibility = "none" | "aggregate_view" | "buildings" | "full";

export type GeoJSONPolygon = {
  type: "Polygon";
  coordinates: number[][][];
};

export type GeoJSONMultiPolygon = {
  type: "MultiPolygon";
  coordinates: number[][][][];
};

// One member of a multi-place union (`CrisisGeometry.parts`). Mirrors
// `CrisisGeometryPart` in `apps/api/src/api/schemas/crises.py`: a GERS
// `division_id` (resolved server-side) or an inline polygon (OSM fallback,
// uploaded, or drawn). The backend unions every part into one MultiPolygon.
export type CrisisGeometryPart =
  | { division_id: string }
  | { polygon: GeoJSONPolygon | GeoJSONMultiPolygon };

export type CrisisGeometry =
  | { division_id: string }
  | { polygon: GeoJSONPolygon | GeoJSONMultiPolygon }
  | { bbox: [number, number, number, number] }
  // Multi-place union: emitted when the Search tab holds 2+ places, or
  // a single place mixed with an OSM/inline polygon. A single Overture place
  // still serialises as `{ division_id }` and a single inline shape as
  // `{ polygon }`; `parts` only appears for genuine unions.
  | { parts: CrisisGeometryPart[] };

export interface AdminCrisisCreate {
  name: string;
  type: string;
  status?: CrisisStatus;
  countries?: string[];
  started_at?: string | null;
  ended_at?: string | null;
  geometry?: CrisisGeometry;
  // ISO 8601 UTC; null means "no scheduled time". The scheduling fields are
  // mutually exclusive (DB CHECK + Pydantic validator): only one may be
  // non-default at a time.
  activate_at?: string | null;
  activate_on_ingest_success?: boolean;
  // Optional on create; API defaults to "aggregate_view" if omitted.
  public_visibility?: PublicVisibility;
  // K-anonymity floor for the aggregate heatmap. API default is 1.
  heatmap_k_anonymity?: number;
  // Per-crisis public infra-type whitelist. `null` (default) = no filter.
  // A non-empty array is strict: a report is public iff its `infra_type`
  // overlaps. Applied only in `buildings` / `full` modes. Empty arrays are
  // normalised to `null` server-side.
  public_infra_types?: string[] | null;
  // Optional custom form schema to use instead of the server default.
  // Validated server-side; omit to use the default 9-page form.
  form_schema?: object;
}

export interface AdminCrisisPatch {
  name?: string;
  type?: string;
  status?: CrisisStatus;
  countries?: string[];
  started_at?: string | null;
  ended_at?: string | null;
  geometry?: CrisisGeometry | null;
  activate_at?: string | null;
  activate_on_ingest_success?: boolean;
  public_visibility?: PublicVisibility;
  heatmap_k_anonymity?: number;
  // Per-crisis public infra-type whitelist. Sending `null` clears the
  // filter (show all); sending a non-empty array sets the whitelist.
  // Omitting the key leaves the value untouched.
  public_infra_types?: string[] | null;
}

export type AreaSource = "overture" | "osm";

export interface AreaSearchHit {
  id: string;
  name: string;
  subtype: string;
  country: string | null;
  parents: string[];
  bbox: [number, number, number, number];
  // Absent means "overture". OSM rows carry the polygon inline so the picker
  // doesn't need a second fetch.
  source?: AreaSource;
  geometry?: GeoJSONPolygon | GeoJSONMultiPolygon | null;
}

export interface CountryRow {
  id: string;
  iso2: string;
  name: string;
}

// One picked place in the multi-select Search tab; the backend unions them.
// An Overture pick loads its polygon lazily; an OSM pick carries it inline.
export type SearchSelection =
  | {
      source: "overture";
      division_id: string;
      preview: AreaSearchHit;
      // Fetched lazily via `GET /admin/areas/{id}/geometry`. While `undefined`,
      // the preview map shows a loading hint over the bbox.
      polygon?: GeoJSONPolygon | GeoJSONMultiPolygon;
      // The geometry endpoint returned 404 (Overture has the name but no
      // polygon). Excluded from the saved geometry until swapped for an OSM
      // match or removed; the row surfaces OSM alternatives inline.
      polygonMissing?: boolean;
    }
  | {
      source: "osm";
      osm_id: string;
      preview: AreaSearchHit;
      // OSM hits carry the polygon inline, so `polygon` is always set.
      polygon: GeoJSONPolygon | GeoJSONMultiPolygon;
    };

export type CrisisAreaValue =
  | {
      mode: "search";
      // The accumulated places. Empty list behaves like "no area chosen yet"
      // (treated as empty by `geometryFromArea`/`willHaveGeometry`).
      selections: SearchSelection[];
    }
  | {
      mode: "upload";
      polygon: GeoJSONPolygon | GeoJSONMultiPolygon;
      filename: string;
      bbox: [number, number, number, number];
    }
  | {
      mode: "draw";
      polygon: GeoJSONPolygon;
      bbox: [number, number, number, number];
    }
  | { mode: "countries-only" }
  | { mode: "empty" };

export interface AdminCrisis {
  id: string;
  name: string;
  status: CrisisStatus;
  type: string;
  countries: string[];
  started_at: string | null;
  ended_at: string | null;
  created_at: string;
  has_geometry: boolean;
  // Envelope of the crisis geometry as `[w, s, e, n]` (WGS84), or null when
  // the crisis has no AOI. The dashboard map fits to this on selection.
  bbox?: [number, number, number, number] | null;
  pmtiles_url?: string | null;
  overture_release_pinned?: string | null;
  // Building-ingest stamps. NULL until a successful run, and nulled in the
  // same UPDATE that rewrites `geometry`, so counts never go stale against a
  // new polygon. `buildings_ingested_count` is the last run's row count, not a
  // live spatial query.
  buildings_ingested_at?: string | null;
  buildings_ingested_count?: number | null;
  // Scheduling fields. `activate_at` is ISO 8601 UTC; both default to
  // "no schedule" (null / false). The backend enforces mutual exclusion.
  activate_at?: string | null;
  activate_on_ingest_success?: boolean;
  // Always populated by the API (default "aggregate_view").
  public_visibility: PublicVisibility;
  // K-anonymity floor for the aggregate heatmap.
  heatmap_k_anonymity: number;
  // Per-crisis public infra-type whitelist (admin-only). `null` = no filter.
  // Optional so a cached response without the field doesn't crash;
  // consumers coerce `undefined` to `null`.
  public_infra_types?: string[] | null;
}

export type CrisisJobStatus = "running" | "succeeded" | "failed";

export interface CrisisJob {
  job_type: string;
  status: CrisisJobStatus;
  error: string | null;
  started_at: string;
  ended_at: string | null;
  // Buildings bulk-load progress, mirroring the nullable `crisis_jobs` columns.
  // All null until the worker reports progress; `progress_total` stays null
  // when no pre-flight estimate was taken (render the count without a denominator).
  phase?: string | null;
  progress_count?: number | null;
  progress_total?: number | null;
}

// Pre-flight ingest estimate. Mirrors `IngestEstimateResponse` /
// `IngestEstimateSeconds` in `apps/api/src/api/schemas/crises.py`, returned by
// `POST /admin/crises/{id}/ingest_buildings/estimate`. `approx_building_count`
// is an overcount (AOI bbox from parquet metadata, no per-row geometry), shown
// so the coordinator sees scale and duration before committing.
export interface IngestEstimateSeconds {
  download: number;
  load: number;
  total: number;
}

export interface IngestEstimate {
  approx_building_count: number;
  estimate_basis: string;
  est_seconds: IngestEstimateSeconds;
  note: string;
}

// --- Crisis analysis reports --------------------------------------------
// Generated PDF snapshots of a crisis's reporting state. A POST starts a
// background job (~30s to 2min) that walks `phase` through
// `analysing → summarising → rendering → uploading`; the detail endpoint is
// polled until a terminal status. Mirrors `CrisisReportListItem` /
// `CrisisReportDetail` on the admin `analysis_reports` sub-router.

export type CrisisReportStatus = "running" | "succeeded" | "failed";

// One row in the history list (`GET .../analysis_reports`). Newest-first.
export interface CrisisReportListItem {
  id: string;
  created_at: string;
  // Supabase user id of the generator (stable identity).
  created_by: string;
  // Generator's email snapshotted at generation time. Null for unresolved
  // authors; render falls back to `created_by`.
  created_by_email: string | null;
  status: CrisisReportStatus;
  phase: string | null;
  report_count: number | null;
  // Already 0-100 (a percentage, not a fraction). Render small values
  // honestly ("<0.1%") rather than rounding to 0.
  coverage_pct: number | null;
  download_ready: boolean;
}

// Full detail (`GET .../analysis_reports/{id}`). `download_url` is a
// short-lived signed URL, present only when `status === "succeeded"`.
export interface CrisisReportDetail {
  id: string;
  crisis_id: string;
  created_at: string;
  created_by: string;
  // Snapshot of the generator's email at generation time (see ListItem).
  created_by_email: string | null;
  status: CrisisReportStatus;
  phase: string | null;
  progress_count: number | null;
  progress_total: number | null;
  error: string | null;
  ended_at: string | null;
  report_count: number | null;
  device_count: number | null;
  building_count: number | null;
  coverage_pct: number | null;
  download_url: string | null;
}

// --- Photo-export image bundles -----------------------------------------
// A coordinator/admin exports a crisis's report photos as downloadable zip
// parts, built by a background job that honors the current view filters. Mirrors
// the analysis-report poll shape but adds an 'expired' terminal state (the bundle
// is purged after 48h; the audit row survives).

export type ReportExportStatus = "running" | "succeeded" | "failed" | "expired";
export type ExportScope = "view" | "crisis";
export type ExportManifestFormat = "csv" | "geojson";

// One downloadable zip part, with a freshly-signed short-lived URL.
export interface PhotoExportPart {
  part_number: number;
  download_url: string;
  bytes: number;
  photo_count: number;
}

// One row in the export history (`GET .../photo_exports`). Newest-first.
export interface PhotoExportListItem {
  id: string;
  created_at: string;
  created_by: string;
  created_by_email: string | null;
  status: ReportExportStatus;
  phase: string | null;
  scope: ExportScope | null;
  format: ExportManifestFormat | null;
  photo_count: number | null;
  total_bytes: number | null;
  expires_at: string | null;
  download_ready: boolean;
}

// Full detail (`GET .../photo_exports/{id}`). `parts` is populated only when the
// run succeeded and the bundle has not yet expired; each URL is short-lived.
export interface PhotoExportDetail {
  id: string;
  crisis_id: string;
  created_at: string;
  created_by: string;
  created_by_email: string | null;
  status: ReportExportStatus;
  phase: string | null;
  progress_count: number | null;
  progress_total: number | null;
  error: string | null;
  ended_at: string | null;
  scope: ExportScope | null;
  format: ExportManifestFormat | null;
  photo_count: number | null;
  total_bytes: number | null;
  expires_at: string | null;
  parts: PhotoExportPart[];
}

// 201 body for `POST .../photo_exports`. `attached` is true when a
// byte-identical export was already running and this request joined it.
export interface PhotoExportCreated {
  id: string;
  status: ReportExportStatus;
  created_at: string;
  attached: boolean;
}

// --- Live crisis-analysis metrics ---------------------------------------
// Mirrors `AnalysisMetricsResponse` (apps/api/src/api/schemas/analysis_metrics.py):
// the deterministic slice of the analysis report, computed live (~2-3s, no LLM,
// no PDF) for the #/analysis tab.

export interface AnalysisBBox {
  min_lon: number;
  min_lat: number;
  max_lon: number;
  max_lat: number;
}

export interface AnalysisDamageMix {
  minimal: number;
  partial: number;
  complete: number;
}

export interface AnalysisMeta {
  crisis_id: string;
  crisis_name: string;
  crisis_type: string | null;
  countries: string[];
  as_of: string;
  bbox: AnalysisBBox | null;
  // Simplified AOI polygon for the boundary outline on the impact map; null
  // when the crisis has no geometry.
  geometry: GeoJSON.Polygon | GeoJSON.MultiPolygon | null;
  report_count: number;
  device_count: number;
  building_count: number;
  reported_building_count: number;
  coverage_pct: number;
}

export interface AnalysisDistrict {
  id: string;
  name: string;
  official: boolean;
  centroid_lat: number;
  centroid_lon: number;
  report_count: number;
  damage: AnalysisDamageMix;
  exposure_buildings: number;
  debris_yes: number;
  debris_known: number;
  services_hit: number;
  coverage_residual_z: number;
  flags: string[];
  red_components: string[];
  tier: number;
  rank: number;
  priority_score: number;
  severity_score: number;
  reachability_score: number;
  geom: GeoJSON.Polygon | GeoJSON.MultiPolygon | null;
}

// One analysis cell on the shared 0.02° mesh. Carries the coverage signal
// (`residual_z`, `blind_spot`) and the per-cell impact (displaced + dollar
// ranges) so the frontend recolours the same cells per toggle. `economic_*_usd`
// is null where LitPop has no coverage (`economic_available` false); displaced
// is always present.
export interface AnalysisCell {
  lat: number;
  lon: number;
  observed: number;
  expected: number;
  residual_z: number;
  gi_z: number;
  blind_spot: boolean;
  affected_buildings: number;
  displaced_low: number;
  displaced_high: number;
  economic_available: boolean;
  economic_low_usd: number | null;
  economic_high_usd: number | null;
}

export interface AnalysisImpactHeadline {
  affected_buildings: number;
  displaced_low: number;
  displaced_high: number;
  // true when displaced came from the WorldPop population grid; false when it
  // fell back to the 3-6 occupants-per-building assumption.
  population_available: boolean;
  economic_available: boolean;
  economic_low_usd: number | null;
  economic_high_usd: number | null;
  notes: string[];
}

export interface AnalysisHotspots {
  hot_cell_count: number;
  cold_cell_count: number;
  significance_z: number;
}

export interface AnalysisMetrics {
  meta: AnalysisMeta;
  damage: AnalysisDamageMix;
  priority_districts: AnalysisDistrict[];
  cells: AnalysisCell[];
  blind_spot_district_ids: string[];
  observed_total: number;
  expected_total: number;
  hotspots: AnalysisHotspots;
  infra_by_type: Record<string, number>;
  impact: AnalysisImpactHeadline;
}

// --- Admin reports ------------------------------------------------------
// Mirrors `AdminReportListItem`, `AdminReportListResponse`, and
// `AdminReportDetailResponse` in `apps/api/src/api/domain/schemas.py`.

export type AdminReportDamageClass = "minimal" | "partial" | "complete";
export type AdminReportDebris = "yes" | "no" | "unknown";

export interface AdminReportLocation {
  lat: number;
  lng: number;
}

// Where a report's `map_point` came from, in precedence order. `ai_geocode`
// is an approximate best-effort geocode of the route text, rendered
// distinctly from a real `submitted_pin`.
export type AdminLocationSource = "submitted_pin" | "building_centroid" | "ai_geocode";

export interface AdminReportListItem {
  id: string;
  crisis_id: string;
  damage_class: AdminReportDamageClass;
  description: string | null;
  // Citizen-typed directions to the site (the "Location description" column),
  // used when there's no GPS pin. null when not provided.
  route_description: string | null;
  infra_type: string[] | null;
  infra_name: string | null;
  crisis_type: string | null;
  crisis_type_detailed: string | null;
  debris: AdminReportDebris | null;
  building_id: string | null;
  client_id: string | null;
  client_submission_id: string | null;
  // Raw report GPS; NULL when the submit had no fix.
  location: AdminReportLocation | null;
  // `coalesce(location, building.centroid, ai_geocode)`: what the map
  // plots. NULL when none resolve (excluded from bbox mode, included in
  // list mode).
  map_point: AdminReportLocation | null;
  // Provenance of `map_point`. The three `location_*` fields below are
  // populated only for `ai_geocode` so the map can render an approximate
  // (AI-located) point distinctly from a submitted pin.
  location_source: AdminLocationSource | null;
  location_radius_m: number | null;
  location_area_only: boolean;
  location_confidence: number | null;
  // null for a description-only report (photo is optional).
  photo_path: string | null;
  created_at: string;
}

export interface AdminReportListResponse {
  items: AdminReportListItem[];
  // Populated only in cursor (list) mode; null on last page.
  next_cursor: string | null;
  // Populated only in bbox (map) mode; true iff total_in_bbox > limit.
  truncated: boolean;
  total_in_bbox: number | null;
}

// --- Admin buildings ----------------------------------------------------
// Mirrors `AdminBuildingsFeatureCollection` /
// `AdminBuildingFeature` / `AdminBuildingProperties` in
// `apps/api/src/api/schemas/admin_buildings.py`. The wire shape is a
// GeoJSON FeatureCollection so the admin map can hand it straight to a
// MapLibre GeoJSON source without reshaping.

export interface AdminBuildingProperties {
  building_id: string;
  name: string | null;
  report_count: number;
  latest_damage_class: AdminReportDamageClass;
  latest_at: string;
  minimal_count: number;
  partial_count: number;
  complete_count: number;
  centroid_lat: number;
  centroid_lng: number;
}

export type AdminBuildingGeometry =
  | { type: "Polygon"; coordinates: number[][][] }
  | { type: "MultiPolygon"; coordinates: number[][][][] };

export interface AdminBuildingFeature {
  type: "Feature";
  geometry: AdminBuildingGeometry;
  properties: AdminBuildingProperties;
}

export interface AdminBuildingsFeatureCollection {
  type: "FeatureCollection";
  features: AdminBuildingFeature[];
  // The query hit `limit` rows; the client shows a "zoom in" hint.
  truncated: boolean;
}

// Mirrors the `*_status` columns on `public.report_translations`. `null`
// means no sidecar row exists; the inspector treats it like "skipped".
export type AdminReportTranslationStatus =
  | "pending"
  | "ready"
  | "passthrough"
  | "skipped"
  | "failed";

export interface AdminReportDetail {
  id: string;
  crisis_id: string;
  damage_class: AdminReportDamageClass;
  description: string | null;
  infra_type: string[] | null;
  infra_name: string | null;
  crisis_type: string | null;
  crisis_type_detailed: string | null;
  debris: AdminReportDebris | null;
  building_id: string | null;
  // Overture's own name for the matched footprint. Usually null (most
  // footprints are unnamed); the inspector falls back to `infra_name`.
  building_name: string | null;
  client_id: string | null;
  client_submission_id: string | null;
  location: AdminReportLocation | null;
  building_centroid: AdminReportLocation | null;
  // Which position source the map point came from (same precedence as the
  // list/map endpoints). When "ai_geocode", the location was inferred by the
  // AI from `route_description`, which the inspector then surfaces as the
  // "Located from" text. null when the report has no resolvable location.
  location_source: AdminLocationSource | null;
  // Both null for a description-only report (no stored photo to sign).
  photo_path: string | null;
  // Short-lived signed Supabase URL (~15-min TTL). Re-fetch the detail
  // endpoint to mint a fresh one; do not persist or log.
  photo_url: string | null;
  created_at: string;
  // Translation sidecar fields. Null when no sidecar row exists, or while the
  // worker is still processing a fresh report.
  description_lang: string | null;
  description_en: string | null;
  description_status: AdminReportTranslationStatus | null;
  // Raw citizen-typed directions to the site. The text an AI geocode is
  // inferred from (see `location_source`); also the route fallback when no GPS
  // pin was shared.
  route_description: string | null;
  route_description_lang: string | null;
  route_description_en: string | null;
  route_description_status: AdminReportTranslationStatus | null;
  // AI vision caption of the photo (enrichment sidecar). null on
  // description-only reports and when enrichment hasn't run.
  // Status is 'pending' | 'ready' | 'failed' | 'skipped'.
  ai_caption: string | null;
  ai_caption_status: string | null;
  // Reporter-quality fields; null/undefined when the report hasn't been scored.
  confidence_score: number | null | undefined;
  verified: boolean | null | undefined;
  reporter_stats: AdminReporterStats | null | undefined;
  // Full quality breakdown; null/undefined until the score_report job has run.
  quality?: AdminReportQuality | null;
}

// Mirror of the backend `ReportQualityOut`. Only the fields the inspector
// reads are typed; extend as the UI surfaces more of the breakdown.
export interface AdminReportQuality {
  // AI photo-relevance verdict: "relevant" | "irrelevant" | "unclear".
  relevance_label: string | null;
  is_duplicate_image: boolean;
  // When the confidence pipeline last scored this report. null on a quality
  // row that carries a relevance verdict but was never scored (e.g. backfilled
  // rows), so the confidence pill can hide a meaningless 0.
  computed_at: string | null;
}

export interface AdminReporterStats {
  total_reports: number;
  quality_reports: number;
  verified_count: number;
  badge_count: number;
  badge_slugs: string[];
}
