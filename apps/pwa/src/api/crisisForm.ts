import { API_BASE } from "../lib/apiBase";
import type { FormSchema } from "../types";

// The list endpoint omits the schema. Null on non-OK or missing fields; throws on network error.
export async function fetchCrisisForm(
  id: string,
  locale: string,
): Promise<{ form_schema: FormSchema; form_version: number } | null> {
  const res = await fetch(`${API_BASE}/crises/${id}?locale=${encodeURIComponent(locale)}`);
  if (!res.ok) return null;
  const detail = (await res.json()) as {
    form_schema?: FormSchema;
    form_version?: number;
  };
  if (!detail.form_schema || typeof detail.form_version !== "number") return null;
  return { form_schema: detail.form_schema, form_version: detail.form_version };
}
