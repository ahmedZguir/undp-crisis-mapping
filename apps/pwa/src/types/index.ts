export type { PhotoMetadata } from "../lib/photoMetadata";
import type { PhotoMetadata } from "../lib/photoMetadata";
import type { GeoJSONMultiPolygon, GeoJSONPolygon, PublicVisibility } from "./admin";

export const DAMAGE_CLASSES = ["minimal", "partial", "complete"] as const;
export type DamageClass = (typeof DAMAGE_CLASSES)[number];

export type Debris = "yes" | "no" | "unknown";

export type InfraType =
  | "residential"
  | "commercial"
  | "government"
  | "utility"
  | "transport"
  | "community"
  | "public_spaces"
  | "other";

export type CrisisNatureType = "natural_hazards" | "technological" | "human_made";

// Fields shared verbatim by the in-progress form state and the submit payload.
interface ReportFields {
  infra_type: InfraType[];
  infra_type_other: string;
  infra_name: string;
  description: string;
  crisis_nature: string | null; // sub-option from crisis nature taxonomy
  crisis_nature_type: CrisisNatureType | null;
  crisis_nature_other: string;
  debris: Debris | null;
  electricity: string | null; // one of 6 options
  health_services: string | null; // one of 5 options
  pressing_needs: string[]; // multi-select, max 3
  crisis_id: string;
  building_id?: string;
  latitude: number | null;
  longitude: number | null;
}

export interface FormState extends ReportFields {
  photo: File | null;
  damage_class: DamageClass | null;
  route_description: string;
  photo_metadata: PhotoMetadata | null;
  // Keyed by question label; string[] for multi-select.
  generic_answers: Record<string, string | string[]>;
}

// Locale-resolved form schema from `GET /crises/{id}`. Identity is
// by position; `generic_answers` keys by question label, so labels must be unique.
export type FormQuestionType = "single_select" | "multi_select" | "free_text";

export interface FormOption {
  label: string;
}

export interface FormQuestion {
  type: FormQuestionType;
  label: string;
  required?: boolean;
  placeholder?: string;
  max_select?: number;
  options?: FormOption[];
}

export type FormPageKind =
  | "photo_and_damage"
  | "location"
  | "description"
  | "debris"
  | "infra_type"
  | "crisis_nature"
  | "electricity"
  | "health_services"
  | "pressing_needs"
  | "generic";

export interface FormPage {
  kind: FormPageKind;
  enabled: boolean;
  locked: boolean;
  // Built-in select pages only (absent = required); generic questions carry their own.
  required?: boolean;
  title?: string;
  questions?: FormQuestion[];
}

export interface FormSchema {
  pages: FormPage[];
}

export interface ReportPayload extends ReportFields {
  photo: File | Blob | null;
  damage_class: DamageClass;
  route_description?: string;
  client_submission_id?: string;
  client_id?: string;
  photo_metadata?: PhotoMetadata | null;
  form_version?: number;
  generic_answers?: Record<string, string | string[]>;
}

export interface Crisis {
  id: string;
  name: string;
  pmtiles_url: string | null;
  overture_release_pinned: string | null;
  // WGS84 outline; null on the reserved `Other / Unspecified` row.
  geometry?: GeoJSONPolygon | GeoJSONMultiPolygon | null;
  // Picks BrowseMap's renderer.
  public_visibility: PublicVisibility;
  // Only from `GET /crises/{id}`; the list endpoint omits it.
  form_schema?: FormSchema;
  form_version?: number;
}
