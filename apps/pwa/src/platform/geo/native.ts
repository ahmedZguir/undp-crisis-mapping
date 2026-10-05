// Native geolocation via @capacitor/geolocation (dynamically imported).

import {
  type GeoAdapter,
  type GeoCoords,
  GeoError,
  type GeoOptions,
  type GeoPermission,
} from "./index";

type GeolocationModule = typeof import("@capacitor/geolocation");

async function readOnce(
  Geolocation: GeolocationModule["Geolocation"],
  opts: GeoOptions,
): Promise<GeoCoords> {
  const pos = await Geolocation.getCurrentPosition({
    enableHighAccuracy: opts.enableHighAccuracy ?? false,
    timeout: opts.timeout,
    maximumAge: opts.maximumAge,
  });
  return {
    latitude: pos.coords.latitude,
    longitude: pos.coords.longitude,
    accuracy: pos.coords.accuracy,
  };
}

function classify(message: string): GeoError {
  const code = /denied|permission/i.test(message)
    ? "permission_denied"
    : /time/i.test(message)
      ? "timeout"
      : "position_unavailable";
  return new GeoError(code, message);
}

export const nativeGeo: GeoAdapter = {
  isAvailable: () => true,
  async getCurrentPosition(opts: GeoOptions = {}): Promise<GeoCoords> {
    const { Geolocation } = await import("@capacitor/geolocation");

    // Android needs an explicit runtime request; getCurrentPosition doesn't always trigger it.
    try {
      let status = await Geolocation.checkPermissions();
      if (status.location !== "granted" && status.coarseLocation !== "granted") {
        status = await Geolocation.requestPermissions({ permissions: ["location"] });
      }
      if (status.location !== "granted" && status.coarseLocation !== "granted") {
        throw new GeoError("permission_denied", "location permission not granted");
      }
    } catch (err) {
      if (err instanceof GeoError) {
        console.warn("[geo] permission:", err.code, err.message);
        throw err;
      }
      // checkPermissions failed: let getCurrentPosition surface the real error.
    }

    try {
      return await readOnce(Geolocation, opts);
    } catch (err) {
      const message = err instanceof Error ? err.message : String(err);
      const geoErr = classify(message);
      // GPS cold start can time out indoors; the coarse network fix usually returns fast.
      if (opts.enableHighAccuracy && geoErr.code === "timeout") {
        console.warn("[geo] high-accuracy timed out; retrying coarse");
        try {
          return await readOnce(Geolocation, { ...opts, enableHighAccuracy: false });
        } catch (err2) {
          const msg2 = err2 instanceof Error ? err2.message : String(err2);
          console.warn("[geo] coarse retry failed:", msg2);
          throw classify(msg2);
        }
      }
      console.warn("[geo] getCurrentPosition failed:", geoErr.code, message);
      throw geoErr;
    }
  },
  async getPermissionState(): Promise<GeoPermission> {
    try {
      const { Geolocation } = await import("@capacitor/geolocation");
      const status = await Geolocation.checkPermissions();
      if (status.location === "granted" || status.coarseLocation === "granted") return "granted";
      if (status.location === "denied") return "denied";
      return "prompt";
    } catch {
      return "unknown";
    }
  },
};
