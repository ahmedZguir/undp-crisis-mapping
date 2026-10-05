// Mirror of `api/crises/default_form.DEFAULT_FORM_SCHEMA` — keep in sync. Fallback when a crisis has no `form_schema`.
import type { FormSchema } from "../types";

export const DEFAULT_FORM_SCHEMA: FormSchema = {
  pages: [
    { kind: "photo_and_damage", enabled: true, locked: true },
    { kind: "location", enabled: true, locked: true },
    { kind: "description", enabled: true, locked: false },
    { kind: "debris", enabled: true, locked: false },
    { kind: "infra_type", enabled: true, locked: false },
    { kind: "crisis_nature", enabled: true, locked: false },
    { kind: "electricity", enabled: false, locked: false },
    { kind: "health_services", enabled: false, locked: false },
    { kind: "pressing_needs", enabled: false, locked: false },
  ],
};

export const DEFAULT_FORM_VERSION = 1;
