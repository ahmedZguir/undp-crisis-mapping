import { API_BASE } from "../lib/apiBase";
import type {
  AdminBuildingsFeatureCollection,
  AdminCrisis,
  AdminCrisisCreate,
  AdminCrisisPatch,
  AdminReportDamageClass,
  AdminReportDetail,
  AdminReportListResponse,
  AdminReporterStats,
  AnalysisMetrics,
  AreaSearchHit,
  Coordinator,
  CoordinatorRole,
  CountryRow,
  CrisisJob,
  CrisisReportDetail,
  CrisisReportListItem,
  CrisisReportStatus,
  ExportManifestFormat,
  ExportScope,
  GeoJSONMultiPolygon,
  GeoJSONPolygon,
  IngestEstimate,
  PhotoExportCreated,
  PhotoExportDetail,
  PhotoExportListItem,
} from "../types/admin";
import { authedFetch } from "./auth";
import type { LocationSource, SearchRequest } from "./search";

/** `createCoordinator` 409: the email already has a coordinator account. */
export class CoordinatorEmailExistsError extends Error {
  constructor() {
    super("email_exists");
    this.name = "CoordinatorEmailExistsError";
  }
}

/** Create/patch crisis 409 (duplicate name); carries the server detail without the `HTTP 409` prefix. */
export class CrisisNameExistsError extends Error {
  constructor(detail: string) {
    super(detail);
    this.name = "CrisisNameExistsError";
  }
}

async function errorDetail(res: Response): Promise<string> {
  try {
    const body = await res.json();
    if (typeof body?.detail === "string") return body.detail;
  } catch {
    // ignore
  }
  return `HTTP ${res.status}`;
}

async function jsonOrThrow<T>(res: Response): Promise<T> {
  if (!res.ok) {
    let detail = "";
    try {
      const body = await res.json();
      detail = typeof body?.detail === "string" ? body.detail : JSON.stringify(body);
    } catch {
      // ignore
    }
    throw new Error(`HTTP ${res.status}${detail ? `: ${detail}` : ""}`);
  }
  return (await res.json()) as T;
}

export async function listAdminCrises(): Promise<AdminCrisis[]> {
  return jsonOrThrow<AdminCrisis[]>(await authedFetch(`${API_BASE}/admin/crises`));
}

export async function createAdminCrisis(payload: AdminCrisisCreate): Promise<AdminCrisis> {
  const res = await authedFetch(`${API_BASE}/admin/crises`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  if (res.status === 409) {
    throw new CrisisNameExistsError(await errorDetail(res));
  }
  return jsonOrThrow<AdminCrisis>(res);
}

export async function patchAdminCrisis(
  id: string,
  payload: AdminCrisisPatch,
): Promise<AdminCrisis> {
  const res = await authedFetch(`${API_BASE}/admin/crises/${id}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  if (res.status === 409) {
    throw new CrisisNameExistsError(await errorDetail(res));
  }
  return jsonOrThrow<AdminCrisis>(res);
}

export async function ingestBuildings(id: string): Promise<{ job_id: string }> {
  const res = await authedFetch(`${API_BASE}/admin/crises/${id}/ingest_buildings`, {
    method: "POST",
  });
  return jsonOrThrow<{ job_id: string }>(res);
}

export async function listCrisisJobs(id: string): Promise<CrisisJob[]> {
  return jsonOrThrow<CrisisJob[]>(await authedFetch(`${API_BASE}/admin/crises/${id}/jobs`));
}

// Approximate building count and per-phase duration before committing to an ingest.
// 422 when the crisis has no geometry.
export async function estimateIngestBuildings(id: string): Promise<IngestEstimate> {
  const res = await authedFetch(`${API_BASE}/admin/crises/${id}/ingest_buildings/estimate`, {
    method: "POST",
  });
  return jsonOrThrow<IngestEstimate>(res);
}

// Crisis-free variant for the create flow, from a WGS84 footprint. 422 if the geometry is empty/invalid.
export async function estimateIngestBuildingsForGeometry(
  geometry: GeoJSONPolygon | GeoJSONMultiPolygon,
  opts: { signal?: AbortSignal } = {},
): Promise<IngestEstimate> {
  const res = await authedFetch(`${API_BASE}/admin/buildings/ingest_estimate`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ geometry }),
    signal: opts.signal,
  });
  return jsonOrThrow<IngestEstimate>(res);
}

// --- Crisis analysis reports --------------------------------------------
// POST starts a background job and returns the row as `running`; poll
// `getAnalysisReport` until `succeeded`/`failed`. Path is `analysis_reports`,
// not `reports` (that is the damage-report surface).

export async function createAnalysisReport(
  crisisId: string,
): Promise<{ id: string; status: CrisisReportStatus; created_at: string }> {
  const res = await authedFetch(`${API_BASE}/admin/crises/${crisisId}/analysis_reports`, {
    method: "POST",
  });
  return jsonOrThrow<{ id: string; status: CrisisReportStatus; created_at: string }>(res);
}

export async function listAnalysisReports(
  crisisId: string,
  opts: { signal?: AbortSignal } = {},
): Promise<CrisisReportListItem[]> {
  const res = await authedFetch(`${API_BASE}/admin/crises/${crisisId}/analysis_reports`, {
    signal: opts.signal,
  });
  return jsonOrThrow<CrisisReportListItem[]>(res);
}

export async function getAnalysisReport(
  crisisId: string,
  reportId: string,
  opts: { signal?: AbortSignal } = {},
): Promise<CrisisReportDetail> {
  const res = await authedFetch(
    `${API_BASE}/admin/crises/${crisisId}/analysis_reports/${reportId}`,
    { signal: opts.signal },
  );
  return jsonOrThrow<CrisisReportDetail>(res);
}

// Deterministic slice of the analysis report (priority districts, blind spots, per-cell
// impact), computed on demand (~2-3s, no LLM, no PDF) for the #/analysis tab.
export async function getAnalysisMetrics(
  crisisId: string,
  opts: { signal?: AbortSignal } = {},
): Promise<AnalysisMetrics> {
  const res = await authedFetch(`${API_BASE}/admin/crises/${crisisId}/analysis_metrics`, {
    signal: opts.signal,
  });
  return jsonOrThrow<AnalysisMetrics>(res);
}

export async function searchAreas(
  q: string,
  opts: {
    country?: string;
    limit?: number;
    signal?: AbortSignal;
    // `"osm"` skips Overture: alternatives when an Overture pick had no polygon.
    source?: "osm";
  } = {},
): Promise<AreaSearchHit[]> {
  const params = new URLSearchParams({ q });
  if (opts.country) params.set("country", opts.country);
  if (opts.limit) params.set("limit", String(opts.limit));
  if (opts.source) params.set("source", opts.source);
  const res = await authedFetch(`${API_BASE}/admin/areas/search?${params.toString()}`, {
    signal: opts.signal,
  });
  return jsonOrThrow<AreaSearchHit[]>(res);
}

export async function fetchCrisisGeometry(
  crisisId: string,
  opts: { signal?: AbortSignal } = {},
): Promise<GeoJSONPolygon | GeoJSONMultiPolygon | null> {
  // `null` when the crisis has no stored geometry (the reserved
  // "Other / Unspecified" row, or a polygon explicitly cleared).
  const res = await authedFetch(
    `${API_BASE}/admin/crises/${encodeURIComponent(crisisId)}/geometry`,
    {
      signal: opts.signal,
    },
  );
  if (res.status === 404) return null;
  return jsonOrThrow<GeoJSONPolygon | GeoJSONMultiPolygon>(res);
}

export async function fetchAreaGeometry(
  divisionId: string,
  opts: { signal?: AbortSignal } = {},
): Promise<GeoJSONPolygon | GeoJSONMultiPolygon | null> {
  // `null` on 404 (division has no polygon; caller pivots to OSM). Other failures throw.
  const res = await authedFetch(
    `${API_BASE}/admin/areas/${encodeURIComponent(divisionId)}/geometry`,
    {
      signal: opts.signal,
    },
  );
  if (res.status === 404) return null;
  return jsonOrThrow<GeoJSONPolygon | GeoJSONMultiPolygon>(res);
}

export async function fetchCountriesGeometry(
  iso2Codes: string[],
  opts: { signal?: AbortSignal } = {},
): Promise<GeoJSONPolygon | GeoJSONMultiPolygon | null> {
  // `null` on empty input or 404 (no rows matched).
  if (iso2Codes.length === 0) return null;
  const params = new URLSearchParams();
  for (const code of iso2Codes) params.append("iso", code);
  const res = await authedFetch(`${API_BASE}/admin/areas/countries/geometry?${params.toString()}`, {
    signal: opts.signal,
  });
  if (res.status === 404) return null;
  return jsonOrThrow<GeoJSONPolygon | GeoJSONMultiPolygon>(res);
}

// --- Admin reports ------------------------------------------------------
// `GET /admin/crises/{id}/reports` (bbox and cursor modes share one response shape)
// and `GET /admin/reports/{id}` (full row + signed photo URL).

export interface ListAdminReportsOpts {
  bbox?: [number, number, number, number];
  cursor?: string;
  damageClass?: AdminReportDamageClass | null;
  limit?: number;
  signal?: AbortSignal;
}

export async function listAdminReports(
  crisisId: string,
  opts: ListAdminReportsOpts = {},
): Promise<AdminReportListResponse> {
  const params = new URLSearchParams();
  if (opts.bbox) params.set("bbox", opts.bbox.join(","));
  if (opts.cursor) params.set("cursor", opts.cursor);
  if (opts.damageClass) params.set("damage_class", opts.damageClass);
  if (opts.limit !== undefined) params.set("limit", String(opts.limit));
  const qs = params.toString();
  const url = `${API_BASE}/admin/crises/${crisisId}/reports${qs ? `?${qs}` : ""}`;
  return jsonOrThrow<AdminReportListResponse>(await authedFetch(url, { signal: opts.signal }));
}

// --- Admin map (server-side clustering) ---------------------------------
// `POST /admin/crises/{id}/map` takes the full filter plus viewport bbox + zoom and
// answers with grid clusters (exact totals + per-class breakdown) or, when zoomed
// in and sparse, the actual points.

/** `distinct_point_count === 1`: every report shares one coordinate (a stacked building).
 *  When `total === 1` the singleton fields are set so it draws as a real pin, not a "1" bubble. */
export interface AdminMapClusterCell {
  lat: number;
  lng: number;
  total: number;
  minimal: number;
  partial: number;
  complete: number;
  distinct_point_count: number;
  report_id: string | null;
  damage_class: AdminReportDamageClass | null;
  location_source: LocationSource | null;
  location_area_only: boolean;
}

export interface AdminMapPoint {
  id: string;
  damage_class: AdminReportDamageClass;
  map_point: { lat: number; lng: number };
  location_source: LocationSource | null;
  location_area_only: boolean;
  is_anomaly: boolean;
}

export interface AdminMapResponse {
  mode: "clusters" | "points";
  cells: AdminMapClusterCell[];
  items: AdminMapPoint[];
  /** Reports represented: sum of cell totals (clusters) or point count. */
  total: number;
  /** Semantic only: the true uncapped match count when the 50K cap was hit. */
  total_match_count: number | null;
  /** True when the semantic cluster path aggregated over the capped top-50K. */
  capped: boolean;
}

/** `location` (chips) composes AND with the viewport `bbox`. */
export interface AdminMapRequest extends SearchRequest {
  bbox: [number, number, number, number];
  zoom: number;
  anomalies_only?: boolean;
}

export async function fetchAdminMap(
  crisisId: string,
  payload: AdminMapRequest,
  opts: { signal?: AbortSignal } = {},
): Promise<AdminMapResponse> {
  const res = await authedFetch(`${API_BASE}/admin/crises/${crisisId}/map`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
    signal: opts.signal,
  });
  return jsonOrThrow<AdminMapResponse>(res);
}

// --- Admin buildings ----------------------------------------------------
// Viewport-bound footprints with per-building report stats, honouring the full
// filter. POST because the filter is a body.

export interface ListAdminBuildingStatsOpts {
  bbox: [number, number, number, number];
  /** Filter without the viewport bbox (sent separately). Omitted: whole-crisis stats. */
  filter?: SearchRequest;
  limit?: number;
  signal?: AbortSignal;
}

export async function listAdminBuildingStats(
  crisisId: string,
  opts: ListAdminBuildingStatsOpts,
): Promise<AdminBuildingsFeatureCollection> {
  const body = {
    ...(opts.filter ?? {}),
    bbox: opts.bbox,
    ...(opts.limit !== undefined ? { limit: opts.limit } : {}),
  };
  const res = await authedFetch(`${API_BASE}/admin/crises/${crisisId}/buildings/stats`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
    signal: opts.signal,
  });
  return jsonOrThrow<AdminBuildingsFeatureCollection>(res);
}

export async function getAdminReportDetail(
  reportId: string,
  opts: { signal?: AbortSignal } = {},
): Promise<AdminReportDetail> {
  const res = await authedFetch(`${API_BASE}/admin/reports/${reportId}`, { signal: opts.signal });
  return jsonOrThrow<AdminReportDetail>(res);
}

// --- Admin export -------------------------------------------------------
// `POST /admin/crises/{id}/export.{geojson,csv}`: the body is the view's filter;
// `scope=crisis` with an empty body is the whole crisis, incl. non-geolocated reports.
// Read as a Blob (it can be large); the filename comes from Content-Disposition.

export type ExportFormat = "geojson" | "csv";

// Prefer RFC 5987 `filename*=UTF-8''…` (keeps a non-ASCII crisis name), else `filename="…"`.
function filenameFromDisposition(disposition: string): string | null {
  const utf8 = disposition.match(/filename\*=UTF-8''([^;]+)/i);
  if (utf8) {
    try {
      return decodeURIComponent(utf8[1]);
    } catch {
      // Malformed percent-encoding: fall through to the ASCII filename.
    }
  }
  const plain = disposition.match(/filename="?([^";]+)"?/i);
  return plain ? plain[1] : null;
}

export async function exportCrisis(
  crisisId: string,
  format: ExportFormat,
  filter: SearchRequest,
  scope: ExportScope,
  opts: { signal?: AbortSignal } = {},
): Promise<{ blob: Blob; filename: string }> {
  const url = `${API_BASE}/admin/crises/${crisisId}/export.${format}?scope=${scope}`;
  const res = await authedFetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    // An empty filter has no spatial predicate, so it includes non-geolocated reports.
    body: JSON.stringify(scope === "crisis" ? {} : filter),
    signal: opts.signal,
  });
  if (!res.ok) {
    throw new Error(`HTTP ${res.status}: ${await errorDetail(res)}`);
  }
  const disposition = res.headers.get("content-disposition") ?? "";
  const filename = filenameFromDisposition(disposition) ?? `crisis-${crisisId}.${format}`;
  return { blob: await res.blob(), filename };
}

// --- Photo-export image bundles -----------------------------------------
// POST starts (or attaches to) a job bundling matched photos into zip parts; poll
// `getPhotoExport` until terminal, then download each part via its signed URL.

export async function createPhotoExport(
  crisisId: string,
  body: { filters: SearchRequest; scope: ExportScope; format: ExportManifestFormat },
): Promise<PhotoExportCreated> {
  const res = await authedFetch(`${API_BASE}/admin/crises/${crisisId}/photo_exports`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  return jsonOrThrow<PhotoExportCreated>(res);
}

export async function listPhotoExports(
  crisisId: string,
  opts: { signal?: AbortSignal } = {},
): Promise<PhotoExportListItem[]> {
  const res = await authedFetch(`${API_BASE}/admin/crises/${crisisId}/photo_exports`, {
    signal: opts.signal,
  });
  return jsonOrThrow<PhotoExportListItem[]>(res);
}

export async function getPhotoExport(
  crisisId: string,
  exportId: string,
  opts: { signal?: AbortSignal } = {},
): Promise<PhotoExportDetail> {
  const res = await authedFetch(`${API_BASE}/admin/crises/${crisisId}/photo_exports/${exportId}`, {
    signal: opts.signal,
  });
  return jsonOrThrow<PhotoExportDetail>(res);
}

// --- Admin coordinators -------------------------------------------------
// Citizens are not users: these endpoints manage coordinator accounts only.

export async function listCoordinators(): Promise<Coordinator[]> {
  return jsonOrThrow<Coordinator[]>(await authedFetch(`${API_BASE}/admin/coordinators`));
}

export async function createCoordinator(
  email: string,
  password: string,
  role: CoordinatorRole = "coordinator",
): Promise<Coordinator> {
  const res = await authedFetch(`${API_BASE}/admin/coordinators`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ email, password, role }),
  });
  if (res.status === 409) {
    throw new CoordinatorEmailExistsError();
  }
  return jsonOrThrow<Coordinator>(res);
}

export async function rotateCoordinatorPassword(id: string, password: string): Promise<void> {
  const res = await authedFetch(`${API_BASE}/admin/coordinators/${id}/password`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ password }),
  });
  await jsonOrThrow<{ status: string }>(res);
}

export async function disableCoordinator(id: string, reason?: string): Promise<void> {
  const res = await authedFetch(`${API_BASE}/admin/coordinators/${id}/disable`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(reason ? { reason } : {}),
  });
  await jsonOrThrow<{ user_id: string; disabled_at: string }>(res);
}

export async function enableCoordinator(id: string): Promise<void> {
  const res = await authedFetch(`${API_BASE}/admin/coordinators/${id}/enable`, {
    method: "POST",
  });
  await jsonOrThrow<{ status: string }>(res);
}

let countriesCache: Promise<CountryRow[]> | null = null;

export function fetchCountries(force = false): Promise<CountryRow[]> {
  if (force || !countriesCache) {
    countriesCache = (async () => {
      try {
        return await jsonOrThrow<CountryRow[]>(
          await authedFetch(`${API_BASE}/admin/areas/countries`),
        );
      } catch (err) {
        countriesCache = null;
        throw err;
      }
    })();
  }
  return countriesCache;
}

// --- First-run onboarding state (per coordinator account) ----------------
// Server-backed so walkthrough/checklist state follows the account across devices.

export interface OnboardingState {
  walkthrough_completed: boolean;
  dashboard_explored: boolean;
}

export async function getOnboardingState(): Promise<OnboardingState> {
  return jsonOrThrow<OnboardingState>(await authedFetch(`${API_BASE}/admin/onboarding`));
}

/** Monotonic server-side, so replaying the walkthrough is safe. */
export async function markOnboarding(patch: Partial<OnboardingState>): Promise<OnboardingState> {
  return jsonOrThrow<OnboardingState>(
    await authedFetch(`${API_BASE}/admin/onboarding`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(patch),
    }),
  );
}

export interface VerifyReportResult {
  confidence_score: number;
  verified: boolean;
  reporter_stats: AdminReporterStats | null;
}

export async function verifyAdminReport(
  reportId: string,
  verified: boolean,
): Promise<VerifyReportResult> {
  const res = await authedFetch(`${API_BASE}/admin/reports/${reportId}/verify`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ verified }),
  });
  return jsonOrThrow<VerifyReportResult>(res);
}
