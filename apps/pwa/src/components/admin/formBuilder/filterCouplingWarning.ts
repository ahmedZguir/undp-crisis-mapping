// These built-in pages feed admin filters (heatmap aggregation, map filter
// sidebar); disabling one hides future reports from those views. The warning
// is informational, not a block.
import type { FormPageKind } from "../../../types";

const COUPLED_KINDS: ReadonlySet<FormPageKind> = new Set<FormPageKind>([
  "infra_type",
  "crisis_nature",
]);

export const FILTER_COUPLING_WARNING_COPY =
  "Used in admin filters and heatmap. Disabling means future reports for this crisis won't appear in those views.";

export function filterCouplingWarning(kind: FormPageKind, enabled: boolean): string | null {
  // An enabled page needs no warning.
  if (enabled) return null;
  if (!COUPLED_KINDS.has(kind)) return null;
  return FILTER_COUPLING_WARNING_COPY;
}

export function isFilterCoupled(kind: FormPageKind): boolean {
  return COUPLED_KINDS.has(kind);
}
