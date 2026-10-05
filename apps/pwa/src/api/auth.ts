// Coordinator auth client. The access token lives in module memory
// only; the refresh token is an HttpOnly cookie, so /auth/* fetches need `credentials: "include"`.
// Admin API calls must go through `authedFetch`.

import { API_BASE } from "../lib/apiBase";
import { HttpError } from "./http";

interface TokenResponse {
  access_token: string;
  expires_in: number;
}

let accessToken: string | null = null;
let accessTokenExpiresAt = 0;

export function getAccessToken(): string | null {
  return accessToken;
}

// Display-only (e.g. topbar initials); never act on this client-decoded claim.
export function getAccessEmail(): string | null {
  if (!accessToken) return null;
  try {
    const payload = accessToken.split(".")[1];
    if (!payload) return null;
    const json = JSON.parse(atob(payload.replace(/-/g, "+").replace(/_/g, "/"))) as {
      email?: unknown;
    };
    return typeof json.email === "string" && json.email ? json.email : null;
  } catch {
    return null;
  }
}

function applyToken(body: TokenResponse): void {
  accessToken = body.access_token;
  accessTokenExpiresAt = Date.now() + body.expires_in * 1000;
}

function clearToken(): void {
  accessToken = null;
  accessTokenExpiresAt = 0;
}

export class LoginError extends HttpError {
  constructor(status: number, message: string) {
    super(message, status);
  }
}

export async function login(email: string, password: string): Promise<void> {
  const res = await fetch(`${API_BASE}/auth/login`, {
    method: "POST",
    credentials: "include",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ email, password }),
  });
  if (!res.ok) {
    clearToken();
    let detail = "";
    try {
      const body = (await res.json()) as { detail?: string };
      detail = typeof body?.detail === "string" ? body.detail : "";
    } catch {
      // ignore
    }
    throw new LoginError(res.status, detail || `HTTP ${res.status}`);
  }
  applyToken((await res.json()) as TokenResponse);
}

/**
 * Recover a session from the refresh cookie. The cookie is single-use and rotating: a second POST
 * with the same jti counts as reuse and revokes the whole token family. So overlapping callers share
 * one in-flight promise, and the result stays cached for a short grace window after it resolves.
 */
const REFRESH_RESULT_GRACE_MS = 1000;
let inFlightRefresh: Promise<boolean> | null = null;

export async function refresh(): Promise<boolean> {
  if (inFlightRefresh) return inFlightRefresh;
  inFlightRefresh = (async () => {
    const res = await fetch(`${API_BASE}/auth/refresh`, {
      method: "POST",
      credentials: "include",
    });
    if (!res.ok) {
      clearToken();
      return false;
    }
    applyToken((await res.json()) as TokenResponse);
    return true;
  })();
  // Cleared in a setTimeout (not `finally`) so chained `.then`s see the token before the slot frees.
  const slot = inFlightRefresh;
  void slot.finally(() => {
    setTimeout(() => {
      if (inFlightRefresh === slot) inFlightRefresh = null;
    }, REFRESH_RESULT_GRACE_MS);
  });
  return inFlightRefresh;
}

export async function logout(): Promise<void> {
  try {
    await fetch(`${API_BASE}/auth/logout`, {
      method: "POST",
      credentials: "include",
    });
  } finally {
    clearToken();
  }
}

const REFRESH_LEEWAY_MS = 30_000;

/**
 * Attaches the bearer token and refreshes/retries once on 401. With no token and a failed refresh it
 * synthesizes a 401 rather than send an unauthenticated request (which would cascade refreshes).
 */
export async function authedFetch(
  input: RequestInfo | URL,
  init: RequestInit = {},
): Promise<Response> {
  if (!accessToken || Date.now() > accessTokenExpiresAt - REFRESH_LEEWAY_MS) {
    const ok = await refresh();
    if (!ok && !accessToken) {
      return new Response(JSON.stringify({ detail: "not authenticated" }), {
        status: 401,
        headers: { "Content-Type": "application/json" },
      });
    }
  }
  const first = await sendWithToken(input, init);
  if (first.status !== 401) return first;
  const refreshed = await refresh();
  if (!refreshed) return first;
  return sendWithToken(input, init);
}

async function sendWithToken(input: RequestInfo | URL, init: RequestInit): Promise<Response> {
  const headers = new Headers(init.headers ?? {});
  if (accessToken) headers.set("Authorization", `Bearer ${accessToken}`);
  return fetch(input, {
    ...init,
    headers,
    credentials: init.credentials ?? "include",
  });
}

// Test-only: production must not call this (it bypasses the refresh dedup).
export function __resetAuthForTests(): void {
  clearToken();
  inFlightRefresh = null;
}
