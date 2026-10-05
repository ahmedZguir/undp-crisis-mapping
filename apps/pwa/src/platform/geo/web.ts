import {
  type GeoAdapter,
  type GeoCoords,
  GeoError,
  type GeoOptions,
  type GeoPermission,
} from "./index";

function available(): boolean {
  return typeof navigator !== "undefined" && "geolocation" in navigator;
}

export const webGeo: GeoAdapter = {
  isAvailable: available,
  getCurrentPosition(opts: GeoOptions = {}): Promise<GeoCoords> {
    return new Promise((resolve, reject) => {
      if (!available()) {
        reject(new GeoError("unsupported", "geolocation unavailable"));
        return;
      }
      navigator.geolocation.getCurrentPosition(
        (pos) =>
          resolve({
            latitude: pos.coords.latitude,
            longitude: pos.coords.longitude,
            accuracy: pos.coords.accuracy,
          }),
        (err) => {
          const code =
            err.code === err.PERMISSION_DENIED
              ? "permission_denied"
              : err.code === err.TIMEOUT
                ? "timeout"
                : "position_unavailable";
          reject(new GeoError(code, err.message));
        },
        opts,
      );
    });
  },
  async getPermissionState(): Promise<GeoPermission> {
    try {
      if (typeof navigator !== "undefined" && navigator.permissions?.query) {
        const status = await navigator.permissions.query({
          name: "geolocation" as PermissionName,
        });
        return status.state as GeoPermission;
      }
    } catch {
      // Permissions API unsupported or threw.
    }
    return "unknown";
  },
};
