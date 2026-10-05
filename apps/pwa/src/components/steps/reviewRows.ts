// Review rows: built-in kinds use i18n labels, generic pages use admin labels verbatim.
import type { DamageClass, FormPage, FormState } from "../../types";
import { CRISIS_NATURE_OTHER, CRISIS_NATURE_TYPES, INFRA_TYPE_OPTIONS } from "./catalogues";

interface ReviewRow {
  key: string;
  label: string;
  value: string;
  sourcePageIndex: number;
}

type TFn = (key: string, opts?: Record<string, unknown>) => string;

const INFRA_TYPE_KEYS: Record<string, string> = Object.fromEntries(
  INFRA_TYPE_OPTIONS.map((o) => [o.value, o.labelKey]),
);

const CRISIS_NATURE_KEYS: Record<string, string> = Object.fromEntries(
  CRISIS_NATURE_TYPES.flatMap((type) => type.subs.map((sub) => [sub.label, sub.labelKey])),
);

const DAMAGE_KEYS: Record<DamageClass, string> = {
  minimal: "damageClass.minimal",
  partial: "damageClass.partial",
  complete: "damageClass.complete",
};

const DEBRIS_KEYS: Record<NonNullable<FormState["debris"]>, string> = {
  yes: "stepReview.debrisYes",
  no: "stepReview.debrisNo",
  unknown: "stepReview.debrisUnknown",
};

function fallbackEmpty(t: TFn): string {
  return t("stepReview.empty");
}

export function buildReviewRows(visiblePages: FormPage[], state: FormState, t: TFn): ReviewRow[] {
  const rows: ReviewRow[] = [];

  visiblePages.forEach((page, pageIdx) => {
    switch (page.kind) {
      case "photo_and_damage": {
        // The photo renders in the header; damage is a regular row.
        const d = state.damage_class;
        const value = d && DAMAGE_KEYS[d] ? t(DAMAGE_KEYS[d]) : fallbackEmpty(t);
        rows.push({
          key: "damage_class",
          label: t("stepReview.damage"),
          value,
          sourcePageIndex: pageIdx,
        });
        return;
      }
      case "description":
        rows.push({
          key: "description",
          label: t("stepReview.description"),
          value: state.description || fallbackEmpty(t),
          sourcePageIndex: pageIdx,
        });
        return;
      case "location":
        rows.push({
          key: "infra_name",
          label: t("stepReview.name"),
          value: state.infra_name.trim() || t("stepReview.unnamed"),
          sourcePageIndex: pageIdx,
        });
        rows.push({
          key: "location",
          label: t("stepReview.location"),
          value:
            state.route_description.trim() ||
            (state.latitude !== null && state.longitude !== null
              ? `${state.latitude.toFixed(5)}, ${state.longitude.toFixed(5)}`
              : fallbackEmpty(t)),
          sourcePageIndex: pageIdx,
        });
        return;
      case "debris": {
        const d = state.debris;
        const value = d && DEBRIS_KEYS[d] ? t(DEBRIS_KEYS[d]) : fallbackEmpty(t);
        rows.push({
          key: "debris",
          label: t("stepReview.debris"),
          value,
          sourcePageIndex: pageIdx,
        });
        return;
      }
      case "infra_type": {
        const value =
          state.infra_type.length === 0
            ? fallbackEmpty(t)
            : state.infra_type.map((it) => t(INFRA_TYPE_KEYS[it] ?? it)).join(", ");
        rows.push({
          key: "infra_type",
          label: t("stepReview.type"),
          value,
          sourcePageIndex: pageIdx,
        });
        return;
      }
      case "crisis_nature": {
        let value: string;
        if (!state.crisis_nature) {
          value = fallbackEmpty(t);
        } else if (
          state.crisis_nature === CRISIS_NATURE_OTHER &&
          state.crisis_nature_other.trim()
        ) {
          value = t("stepReview.otherPrefix", { value: state.crisis_nature_other.trim() });
        } else {
          const k = CRISIS_NATURE_KEYS[state.crisis_nature];
          value = k ? t(k) : state.crisis_nature;
        }
        rows.push({
          key: "crisis_nature",
          label: t("stepReview.nature"),
          value,
          sourcePageIndex: pageIdx,
        });
        return;
      }
      case "electricity":
        rows.push({
          key: "electricity",
          label: t("stepElectricity.title", { defaultValue: "Electricity" }),
          value: state.electricity ?? fallbackEmpty(t),
          sourcePageIndex: pageIdx,
        });
        return;
      case "health_services":
        rows.push({
          key: "health_services",
          label: t("stepHealthServices.title", { defaultValue: "Health services" }),
          value: state.health_services ?? fallbackEmpty(t),
          sourcePageIndex: pageIdx,
        });
        return;
      case "pressing_needs":
        rows.push({
          key: "pressing_needs",
          label: t("stepPressingNeeds.title", { defaultValue: "Pressing needs" }),
          value: state.pressing_needs.length ? state.pressing_needs.join(", ") : fallbackEmpty(t),
          sourcePageIndex: pageIdx,
        });
        return;
      case "generic":
        (page.questions ?? []).forEach((q, qIdx) => {
          const raw = state.generic_answers[q.label];
          let value: string;
          if (Array.isArray(raw)) {
            value = raw.length ? raw.join(", ") : fallbackEmpty(t);
          } else if (typeof raw === "string" && raw.trim()) {
            value = raw;
          } else {
            value = fallbackEmpty(t);
          }
          rows.push({
            key: `${pageIdx}:${qIdx}`,
            label: q.label,
            value,
            sourcePageIndex: pageIdx,
          });
        });
        return;
    }
  });

  return rows;
}
