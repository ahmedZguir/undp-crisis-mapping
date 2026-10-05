// The API base must follow the page's hostname: a cross-host API drops the SameSite=Lax refresh cookie.

import { afterEach, describe, expect, it, vi } from "vitest";
import { isNativePlatform } from "../platform/platformInfo";
import { API_BASE, resolveApiBase } from "./apiBase";

// Web by default; the native tests flip it.
vi.mock("../platform/platformInfo", () => ({
  isNativePlatform: vi.fn(() => false),
}));

const cfg = vi.hoisted(() => ({ nativeBase: "https://crisis.example.org/api/" }));
vi.mock("../config", () => ({
  get NATIVE_API_BASE() {
    return cfg.nativeBase;
  },
}));

describe("API_BASE", () => {
  it("follows window.location.hostname in dev so cookies stay same-site", () => {
    // jsdom's hostname is `localhost` and PROD is false, so this hits the same-host dev branch.
    expect(API_BASE).toMatch(/^http:\/\/localhost:\d+$/);
  });

  it("strips a trailing slash so callers can safely template paths", () => {
    expect(API_BASE.endsWith("/")).toBe(false);
  });
});

// Origin-agnostic web build.
describe("resolveApiBase on web", () => {
  afterEach(() => {
    vi.unstubAllEnvs();
  });

  it("derives `/api` against the live origin for a production build", () => {
    vi.stubEnv("PROD", true);
    expect(resolveApiBase()).toBe(`${window.location.origin}/api`);
  });

  it("honors an explicit relative VITE_API_BASE_URL against the live origin", () => {
    vi.stubEnv("VITE_API_BASE_URL", "/api");
    expect(resolveApiBase()).toBe(`${window.location.origin}/api`);
  });

  it("honors an explicit absolute VITE_API_BASE_URL untouched (minus trailing slash)", () => {
    vi.stubEnv("VITE_API_BASE_URL", "https://api.example.org/");
    expect(resolveApiBase()).toBe("https://api.example.org");
  });
});

// Native: a relative `/api` can't reach the server from the WebView's local origin.
describe("resolveApiBase inside a native shell", () => {
  afterEach(() => {
    vi.unstubAllEnvs();
    vi.mocked(isNativePlatform).mockReturnValue(false);
    cfg.nativeBase = "https://crisis.example.org/api/";
  });

  it("uses NATIVE_API_BASE (absolute) and ignores a production `/api` derivation", () => {
    vi.mocked(isNativePlatform).mockReturnValue(true);
    vi.stubEnv("PROD", true);
    cfg.nativeBase = "https://crisis.example.org/api/";
    // Trailing slash stripped; the relative web `/api` is NOT used.
    expect(resolveApiBase()).toBe("https://crisis.example.org/api");
  });

  it("throws when NATIVE_API_BASE is empty rather than hitting the local origin", () => {
    vi.mocked(isNativePlatform).mockReturnValue(true);
    cfg.nativeBase = "";
    expect(() => resolveApiBase()).toThrow(/NATIVE_API_BASE/);
  });
});
