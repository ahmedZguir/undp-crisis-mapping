import { afterEach, describe, expect, it, vi } from "vitest";
import { type GeoAdapter, GeoError } from "./geo";
import { webGeo } from "./geo/web";
import { isNativePlatform, platformName } from "./platformInfo";

function stubGeolocation(
  impl: (success: PositionCallback, error: PositionErrorCallback, opts?: PositionOptions) => void,
) {
  vi.stubGlobal("navigator", { geolocation: { getCurrentPosition: impl } });
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("platformInfo", () => {
  it("reports web in a non-native (jsdom) context", () => {
    expect(isNativePlatform()).toBe(false);
    expect(platformName()).toBe("web");
  });
});

describe("webGeo adapter", () => {
  it("resolves flattened coords from navigator.geolocation", async () => {
    stubGeolocation((success) =>
      success({ coords: { latitude: 1, longitude: 2, accuracy: 3 } } as GeolocationPosition),
    );
    await expect(webGeo.getCurrentPosition()).resolves.toEqual({
      latitude: 1,
      longitude: 2,
      accuracy: 3,
    });
  });

  it("maps PERMISSION_DENIED to a permission_denied GeoError", async () => {
    stubGeolocation((_success, error) =>
      error({
        code: 1,
        PERMISSION_DENIED: 1,
        POSITION_UNAVAILABLE: 2,
        TIMEOUT: 3,
        message: "denied",
      } as GeolocationPositionError),
    );
    await expect(webGeo.getCurrentPosition()).rejects.toMatchObject({
      name: "GeoError",
      code: "permission_denied",
    });
  });

  it("maps a timeout to a timeout GeoError", async () => {
    stubGeolocation((_success, error) =>
      error({
        code: 3,
        PERMISSION_DENIED: 1,
        POSITION_UNAVAILABLE: 2,
        TIMEOUT: 3,
        message: "timed out",
      } as GeolocationPositionError),
    );
    await expect(webGeo.getCurrentPosition()).rejects.toMatchObject({ code: "timeout" });
  });

  it("rejects with unsupported when geolocation is absent", async () => {
    vi.stubGlobal("navigator", {});
    await expect(webGeo.getCurrentPosition()).rejects.toBeInstanceOf(GeoError);
  });
});

describe("adapter contract — fakes swap in behind the interface", () => {
  it("a fake GeoAdapter satisfies the interface and consumers never reach around it", async () => {
    const fake: GeoAdapter = {
      isAvailable: () => true,
      getCurrentPosition: async () => ({ latitude: 10, longitude: 20, accuracy: 5 }),
      getPermissionState: async () => "granted",
    };
    // App code depends only on GeoAdapter, so a fake drops in with no changes.
    async function consume(adapter: GeoAdapter) {
      return adapter.isAvailable() ? adapter.getCurrentPosition() : null;
    }
    await expect(consume(fake)).resolves.toEqual({ latitude: 10, longitude: 20, accuracy: 5 });
  });
});
