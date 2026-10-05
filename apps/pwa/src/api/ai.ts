import { API_BASE } from "../lib/apiBase";
import { DAMAGE_CLASSES, type DamageClass } from "../types";

// Advisory AI helpers: submission must never depend on them. Every failure returns null and the
// citizen enters the value manually; nothing throws.

const SUGGEST_TIMEOUT_MS = 4000;

async function postAdvisory(
  path: string,
  body: FormData,
  clientId: string,
  timeoutMs: number,
): Promise<unknown> {
  const ctrl = new AbortController();
  const timer = setTimeout(() => ctrl.abort(), timeoutMs);
  try {
    const res = await fetch(`${API_BASE}${path}`, {
      method: "POST",
      headers: { "X-Client-Id": clientId },
      body,
      signal: ctrl.signal,
    });
    if (!res.ok) return null;
    return await res.json();
  } catch {
    return null;
  } finally {
    clearTimeout(timer);
  }
}

export async function suggestDamageClass(
  photo: Blob,
  clientId: string,
): Promise<DamageClass | null> {
  const body = new FormData();
  body.append("photo", photo, "report.jpg");

  const json = (await postAdvisory("/ai/classify-damage", body, clientId, SUGGEST_TIMEOUT_MS)) as {
    damage_class?: unknown;
  } | null;
  const cls = json?.damage_class;
  if ((DAMAGE_CLASSES as readonly unknown[]).includes(cls)) {
    return cls as DamageClass;
  }
  return null;
}

// Headroom over the server's ~15s transcription cap.
const TRANSCRIBE_TIMEOUT_MS = 20000;

export async function transcribeAudio(audio: Blob, clientId: string): Promise<string | null> {
  const body = new FormData();
  // Filename is cosmetic; the server reads the part's Content-Type.
  body.append("audio", audio, "note.webm");

  const json = (await postAdvisory("/ai/transcribe", body, clientId, TRANSCRIBE_TIMEOUT_MS)) as {
    text?: unknown;
  } | null;
  const text = typeof json?.text === "string" ? json.text.trim() : "";
  return text === "" ? null : text;
}
