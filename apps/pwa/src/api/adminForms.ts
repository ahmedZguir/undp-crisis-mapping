// Client for `GET`/`POST /admin/crises/{id}/form`. Uses `API_BASE` so requests hit the API
// host, not the Vite dev server (which would return index.html and trip JSON.parse).
import { API_BASE } from "../lib/apiBase";
import type { FormSchema } from "../types";
import { authedFetch } from "./auth";

const ADMIN_BASE = `${API_BASE}/admin/crises`;

export interface AdminFormResponse {
  version: number;
  schema: FormSchema;
}

export interface AdminFormConflict {
  status: "conflict";
  current_version: number;
  schema: FormSchema;
}

export interface AdminFormValidationError {
  status: "validation";
  errors: Array<{ path: string; message: string }>;
}

export type PublishResult = AdminFormResponse | AdminFormConflict | AdminFormValidationError;

async function asJson<T>(res: Response): Promise<T> {
  return (await res.json()) as T;
}

export async function getForm(crisisId: string): Promise<AdminFormResponse> {
  const res = await authedFetch(`${ADMIN_BASE}/${crisisId}/form`);
  if (!res.ok) throw new Error(`getForm failed: ${res.status}`);
  return asJson<AdminFormResponse>(res);
}

export async function publishForm(
  crisisId: string,
  basedOnVersion: number,
  schema: FormSchema,
): Promise<PublishResult> {
  const res = await authedFetch(`${ADMIN_BASE}/${crisisId}/form`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ based_on_version: basedOnVersion, schema }),
  });
  if (res.status === 409) {
    const body = await asJson<{ current_version: number; schema: FormSchema }>(res);
    return { status: "conflict", ...body };
  }
  if (res.status === 400) {
    const body = await asJson<{ detail: { errors: Array<{ path: string; message: string }> } }>(
      res,
    );
    return { status: "validation", errors: body.detail.errors };
  }
  if (!res.ok) throw new Error(`publishForm failed: ${res.status}`);
  return asJson<AdminFormResponse>(res);
}
