/**
 * Client for `/admin/reports/search`:
 *   - `searchReports`  → POST /admin/reports/search/{crisis_id}
 *   - `streamSummary`  → POST /admin/reports/search/{crisis_id}/summary (SSE)
 *   - `openChat`       → POST /admin/reports/search/{crisis_id}/chat/open
 *   - `chatTurn`       → POST /admin/reports/search/{crisis_id}/chat/turn
 */

import { API_BASE } from "../lib/apiBase";
import type { AdminReportDamageClass } from "../types/admin";
import { authedFetch, getAccessToken } from "./auth";
import { readJsonOrThrow } from "./http";

export type Strictness = "loose" | "balanced" | "strict";
export type DebrisFilter = "yes" | "no" | "any";
export type LocationKindFilter = "exact" | "ai_geocode" | "any";
export type DamageClassFilter = AdminReportDamageClass | "any";
export type LocationSource = "submitted_pin" | "building_centroid" | "ai_geocode";

export interface SearchTimeWindow {
  from?: string | null;
  to?: string | null;
}

export interface SearchLocationFilter {
  division_ids?: string[];
  polygon?: object;
  bbox?: [number, number, number, number];
}

export interface SearchRequest {
  time_window?: SearchTimeWindow;
  location?: SearchLocationFilter;
  query?: string;
  strictness?: Strictness;
  infra_types?: string[];
  debris?: DebrisFilter;
  location_kind?: LocationKindFilter;
  damage_class?: DamageClassFilter;
  /** Hard scope to a single building (composed AND with every other filter). */
  building_id?: string | null;
  limit?: number;
}

export interface SearchHit {
  id: string;
  damage_class: AdminReportDamageClass;
  description: string | null;
  description_en: string | null;
  infra_type: string[] | null;
  infra_name: string | null;
  // Citizen-typed directions to the site.
  route_description: string | null;
  debris: boolean | null;
  building_id: string | null;
  building_name: string | null;
  location: { lat: number; lng: number } | null;
  map_point: { lat: number; lng: number } | null;
  // Provenance of `map_point`; the three `location_*` fields are populated
  // only for an `ai_geocode` (approximate) point.
  location_source: LocationSource | null;
  location_radius_m: number | null;
  location_area_only: boolean;
  location_confidence: number | null;
  // null for a description-only report (photo is optional).
  photo_path: string | null;
  created_at: string;
  similarity: number | null;
  /** ≥2σ below the query-similarity mean. False for structured-only searches. */
  is_anomaly: boolean;
}

/** `date` is `YYYY-MM-DD` (UTC). Sparse: days with no reports are omitted. */
export interface DailyBucket {
  date: string;
  complete: number;
  partial: number;
  minimal: number;
}

export interface SearchStats {
  total: number;
  severity: Record<string, number>;
  last_24h: number;
  last_hour: number;
  top_infra: string | null;
  top_infra_count: number;
  debris_yes: number;
  debris_known: number;
  with_building: number;
  with_gps: number;
  /** Positioned only by the AI geocode of the route text; shown as approximate points. */
  with_geocode: number;
  /** No position even after the AI-geocode fallback; never on the map or in a bbox count. */
  unmapped: number;
  // The aggregates below cover the full filtered set, even when `rows` is truncated.
  /** A multi-type report counts in each type, so the sum can exceed `total`. */
  infra_breakdown: Record<string, number>;
  daily: DailyBucket[];
  /** Distinct `client_id`: a lower bound on devices (the id is user-resettable). */
  unique_devices: number;
  /** Buildings with ≥1 match over buildings in scope (the location filter if set, else the crisis). */
  buildings_affected: number;
  buildings_total: number;
}

export interface SearchResponse {
  rows: SearchHit[];
  truncated: boolean;
  total_match_count: number;
  similarity_floor: number | null;
  filter_signature: string;
  /** Same aggregates the chat backend injects into its system prompt. */
  stats: SearchStats | null;
}

export async function searchReports(
  crisisId: string,
  payload: SearchRequest,
  signal?: AbortSignal,
): Promise<SearchResponse> {
  return postJsonAuthed<SearchResponse>(
    `${API_BASE}/admin/reports/search/${crisisId}`,
    payload,
    "search ",
    signal,
  );
}

// Authed JSON POST; throws `Error(errorPrefix + status)` on a non-2xx response.
async function postJsonAuthed<T>(
  url: string,
  body: unknown,
  errorPrefix: string,
  signal?: AbortSignal,
): Promise<T> {
  const res = await authedFetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
    signal,
  });
  return readJsonOrThrow<T>(res, errorPrefix);
}

// --- Summary SSE -------------------------------------------------------

export interface SummaryProseFrame {
  kind: "prose";
  text: string;
}
export interface ExemplarSnapshot {
  id: string;
  damage_class: string | null;
  description: string | null;
  building_name: string | null;
}

export interface SummaryClusterFrame {
  kind: "cluster";
  label: string;
  count: number;
  sentences: string;
  exemplars: ExemplarSnapshot[];
}
/** Leading frame of a clustered summary: `sampled` of `total` matched reports were
 *  summarised; `other_count` fell outside every kept cluster. */
export interface SummaryScopeFrame {
  kind: "scope";
  sampled: number;
  total: number;
  other_count: number;
}
export interface SummaryMetaFrame {
  kind: "meta";
  text: string;
}
export interface SummaryErrorFrame {
  kind: "error";
  message: string;
}
export type SummaryFrame =
  | SummaryProseFrame
  | SummaryScopeFrame
  | SummaryClusterFrame
  | SummaryMetaFrame
  | SummaryErrorFrame;

/** One `SummaryFrame` per `data:` event: prose for small N, scope + cluster + meta frames for large N. */
export async function* streamSummary(
  crisisId: string,
  payload: SearchRequest,
  signal?: AbortSignal,
): AsyncIterableIterator<SummaryFrame> {
  // Raw fetch with the current token: no 401 refresh-and-retry for the
  // long-lived SSE request.
  const token = getAccessToken();
  if (!token) throw new Error("not authenticated");
  const res = await fetch(`${API_BASE}/admin/reports/search/${crisisId}/summary`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      Authorization: `Bearer ${token}`,
      Accept: "text/event-stream",
    },
    body: JSON.stringify(payload),
    signal,
  });
  if (!res.ok || !res.body) {
    throw new Error(`summary ${res.status}`);
  }
  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  while (true) {
    const { value, done } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    // SSE frames are separated by a blank line; one `data:` per frame.
    for (;;) {
      const idx = buffer.indexOf("\n\n");
      if (idx < 0) break;
      const frame = buffer.slice(0, idx);
      buffer = buffer.slice(idx + 2);
      const line = frame.split("\n").find((l) => l.startsWith("data:"));
      if (!line) continue;
      try {
        yield JSON.parse(line.slice(5).trim()) as SummaryFrame;
      } catch {
        // skip malformed frame
      }
    }
  }
}

// --- Chat --------------------------------------------------------------

export interface ChatOpenRequest {
  filter: SearchRequest;
  message: string;
}

export interface ChatTurnRequest {
  session_id: string;
  message: string;
}

/** A K-set report with its map position; returned only on chat open. */
export interface ChatContextReport {
  id: string;
  lat: number;
  lng: number;
  damage_class: AdminReportDamageClass | null;
}

export interface ChatTurnResponse {
  session_id: string;
  text: string;
  cited_report_ids: string[];
  k_size: number | null;
  /** Empty on follow-up turns and in stats-only mode. */
  context_reports: ChatContextReport[];
  filter_signature: string | null;
  /** Matched total at session open, constant across turns; drives the stale-scope banner. */
  opened_total: number | null;
  /** Server clock when the turn was answered. */
  answered_at: string | null;
  /** On open: the stats pinned into the system prompt. */
  stats: SearchStats | null;
}

export async function openChat(
  crisisId: string,
  payload: ChatOpenRequest,
): Promise<ChatTurnResponse> {
  return postJsonAuthed<ChatTurnResponse>(
    `${API_BASE}/admin/reports/search/${crisisId}/chat/open`,
    payload,
    "chat open ",
  );
}

export async function chatTurn(
  crisisId: string,
  payload: ChatTurnRequest,
): Promise<ChatTurnResponse> {
  return postJsonAuthed<ChatTurnResponse>(
    `${API_BASE}/admin/reports/search/${crisisId}/chat/turn`,
    payload,
    "chat turn ",
  );
}
