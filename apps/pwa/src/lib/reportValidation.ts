// Minimum-content gate: (photo OR description) AND (location OR route_description) AND damage_class.
// Mirrors the server's `ReportSubmissionService` — keep in sync. Required generic questions are gated
// per page by `GenericPage`.

import type { FormState } from "../types";

export interface ReportContentStatus {
  hasPhotoOrDescription: boolean;
  hasLocationOrRoute: boolean;
  hasDamageClass: boolean;
  submittable: boolean;
}

export function reportContentStatus(s: FormState): ReportContentStatus {
  const hasPhotoOrDescription = s.photo !== null || s.description.trim() !== "";
  const hasLocationOrRoute =
    (s.latitude !== null && s.longitude !== null) ||
    Boolean(s.building_id) ||
    s.route_description.trim() !== "";
  const hasDamageClass = s.damage_class !== null;
  return {
    hasPhotoOrDescription,
    hasLocationOrRoute,
    hasDamageClass,
    submittable: hasPhotoOrDescription && hasLocationOrRoute && hasDamageClass,
  };
}
