import type { FormPage, FormPageKind } from "../../../types";

// Labels for built-in form pages, shared by the builder and the crisis
// "Community form" cards so they never drift.
const PAGE_KIND_LABELS: Record<Exclude<FormPageKind, "generic">, string> = {
  photo_and_damage: "Photo & damage",
  location: "Location",
  description: "Description",
  debris: "Debris",
  infra_type: "Building type",
  crisis_nature: "Crisis nature",
  electricity: "Electricity",
  health_services: "Health services",
  pressing_needs: "Pressing needs",
};

export function formPageLabel(page: FormPage): string {
  if (page.kind === "generic") return page.title?.trim() || "Custom question";
  return PAGE_KIND_LABELS[page.kind];
}
