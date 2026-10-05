import { API_BASE } from "../lib/apiBase";
import { buildReportFormData } from "../lib/outbox-core";
import { STORAGE_KEYS, keyFor } from "../lib/storageKeys";
import type { Crisis, DamageClass, Debris, ReportPayload } from "../types";
import { HttpError, getJson } from "./http";

export async function getCrises(): Promise<Crisis[]> {
  return getJson<Crisis[]>(`${API_BASE}/crises`, "Failed to fetch crises: ");
}

export interface CrisisStats {
  total_reports: number;
  by_damage_class: Record<DamageClass, number>;
  last_24h: number;
  last_7d: number;
  affected_cells: number;
  latest_at: string | null;
}

export function crisisStatsKey(crisisId: string): string {
  return keyFor(STORAGE_KEYS.crisisStats, crisisId);
}

export async function getCrisisStats(crisisId: string): Promise<CrisisStats> {
  return getJson<CrisisStats>(
    `${API_BASE}/crises/${crisisId}/stats`,
    "Failed to fetch crisis stats: ",
  );
}

// `client_id` is the soft credential:
// never log it, send it to analytics, or put it in a URL the user might share.
export interface CitizenReportHistoryItem {
  id: string;
  crisis_id: string;
  crisis_name: string;
  crisis_status: "inactive" | "active" | "archived";
  created_at: string;
  damage_class: DamageClass;
  description: string | null;
  location: { lat: number; lng: number } | null;
  infra_type: string[] | null;
  infra_name: string | null;
  crisis_type: string | null;
  crisis_type_detailed: string | null;
  debris: Debris | null;
  building_id: string | null;
  /** Signed URL, ~15 min TTL; don't log. On 4xx re-fetch the history. Null when the photo is missing. */
  photo_url: string | null;
  client_submission_id: string | null;
  /** Null for pre-rewards reports. While `scored` is false, points/factors aren't meaningful yet. */
  quality: ReportQuality | null;
}

type ReportFactorState = "earned" | "missed" | "na";

interface ReportPointFactor {
  /** submitted | photo | description | photo_relevant | damage_match */
  key: string;
  state: ReportFactorState;
}

export interface ReportQuality {
  scored: boolean;
  points: number;
  max_points: number;
  verified: boolean;
  is_duplicate_image: boolean;
  factors: ReportPointFactor[];
}

interface CitizenReportHistoryResponse {
  items: CitizenReportHistoryItem[];
  total: number;
}

export async function getCitizenReportHistory(
  clientId: string,
  opts: { limit?: number } = {},
): Promise<CitizenReportHistoryResponse> {
  const params = new URLSearchParams({ client_id: clientId });
  if (opts.limit !== undefined) params.set("limit", String(opts.limit));
  return getJson<CitizenReportHistoryResponse>(
    `${API_BASE}/reports?${params.toString()}`,
    "Failed to fetch citizen report history: ",
  );
}

export async function submitReport(payload: ReportPayload): Promise<{ id: string }> {
  // Never log payload: it may contain GPS coordinates or PII.
  const form = buildReportFormData(payload, payload.photo);

  const res = await fetch(`${API_BASE}/reports`, { method: "POST", body: form });
  if (!res.ok) {
    throw new SubmitReportError(`submitReport failed: ${res.status}`, res.status);
  }
  const json = (await res.json()) as { id: string };
  return { id: json.id };
}

export class SubmitReportError extends HttpError {
  constructor(message: string, status: number) {
    super(message, status);
    this.name = "SubmitReportError";
  }
}

// Deletion always returns 200 (no oracle for wrong owner / unknown id), so callers must refetch the
// history to see what went away.
type DeleteReportOutcome = "ok" | "rate_limited" | "error";

async function postDelete(url: string, clientId: string): Promise<DeleteReportOutcome> {
  try {
    const res = await fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ client_id: clientId }),
    });
    if (res.status === 429) return "rate_limited";
    if (!res.ok) return "error";
    return "ok";
  } catch {
    return "error";
  }
}

export async function deleteReport(
  reportId: string,
  clientId: string,
): Promise<DeleteReportOutcome> {
  return postDelete(`${API_BASE}/reports/${reportId}/delete`, clientId);
}

export async function deleteAllReports(clientId: string): Promise<DeleteReportOutcome> {
  return postDelete(`${API_BASE}/reports/delete`, clientId);
}

// Public `full`-mode endpoints.
// Mirror the backend schemas, which deliberately omit client ids, route_description and EXIF.

interface PublicReportBase {
  id: string;
  crisis_id: string;
  damage_class: DamageClass;
  description: string | null;
  infra_type: string[] | null;
  infra_name: string | null;
  crisis_type: string | null;
  crisis_type_detailed: string | null;
  debris: Debris | null;
  building_id: string | null;
  location: { lat: number; lng: number } | null;
  created_at: string;
}

interface PublicReportListItem extends PublicReportBase {
  // `coalesce(location, building.centroid)` — what the map plots.
  map_point: { lat: number; lng: number } | null;
}

interface PublicReportListResponse {
  items: PublicReportListItem[];
  next_cursor: string | null;
  truncated: boolean;
  total_in_bbox: number | null;
}

export interface PublicReportDetail extends PublicReportBase {
  building_centroid: { lat: number; lng: number } | null;
  /** Signed URL, 15 min TTL; don't persist or log. Null for a description-only report. */
  photo_url: string | null;
}

export async function getPublicReportsByBbox(
  crisisId: string,
  bbox: string,
  limit: number,
): Promise<PublicReportListResponse> {
  const params = new URLSearchParams({ bbox, limit: String(limit) });
  return getJson<PublicReportListResponse>(
    `${API_BASE}/crises/${crisisId}/public/reports?${params.toString()}`,
    "Failed to fetch public reports: ",
  );
}

// 404: deleted, hidden (`public_visible=false`), or the crisis left `full` mode since the list call.
export async function getPublicReportDetail(reportId: string): Promise<PublicReportDetail> {
  const res = await fetch(`${API_BASE}/public/reports/${reportId}`);
  if (!res.ok) {
    throw new PublicReportDetailError(
      `Failed to fetch public report detail: ${res.status}`,
      res.status,
    );
  }
  return (await res.json()) as PublicReportDetail;
}

export class PublicReportDetailError extends HttpError {
  constructor(message: string, status: number) {
    super(message, status);
    this.name = "PublicReportDetailError";
  }
}
