// exifr (~31 KB gz) is lazy-loaded to stay out of the entry chunk; lib/warm.ts preloads it.
type ExifrModule = typeof import("exifr");
let exifrPromise: Promise<ExifrModule> | null = null;
function loadExifr(): Promise<ExifrModule> {
  if (!exifrPromise) exifrPromise = import("exifr");
  return exifrPromise;
}

interface PhotoGps {
  latitude: number;
  longitude: number;
  accuracy?: number;
}

interface PhotoCamera {
  make?: string;
  model?: string;
  software?: string;
}

export interface PhotoMetadata {
  gps: PhotoGps | null;
  capturedAt: string | null;
  orientation: number | null;
  width: number | null;
  height: number | null;
  camera: PhotoCamera | null;
  extractedAt: string;
}

interface ExifrParsed {
  latitude?: number;
  longitude?: number;
  GPSHPositioningError?: number;
  DateTimeOriginal?: Date | string;
  CreateDate?: Date | string;
  Orientation?: number;
  ExifImageWidth?: number;
  ExifImageHeight?: number;
  ImageWidth?: number;
  ImageHeight?: number;
  PixelXDimension?: number;
  PixelYDimension?: number;
  Make?: string;
  Model?: string;
  Software?: string;
}

function toIso(value: Date | string | undefined): string | null {
  if (!value) return null;
  const d = value instanceof Date ? value : new Date(value);
  if (Number.isNaN(d.getTime())) return null;
  return d.toISOString();
}

function trimOrUndef(s: string | undefined): string | undefined {
  if (typeof s !== "string") return undefined;
  const t = s.trim();
  return t.length > 0 ? t : undefined;
}

export async function extractPhotoMetadata(file: Blob): Promise<PhotoMetadata> {
  const extractedAt = new Date().toISOString();
  const empty: PhotoMetadata = {
    gps: null,
    capturedAt: null,
    orientation: null,
    width: null,
    height: null,
    camera: null,
    extractedAt,
  };

  let parsed: ExifrParsed | undefined;
  try {
    const exifr = (await loadExifr()).default;
    parsed = (await exifr.parse(file, {
      gps: true,
      tiff: true,
      exif: true,
    })) as ExifrParsed | undefined;
  } catch {
    return empty;
  }

  if (!parsed) return empty;

  const gps =
    typeof parsed.latitude === "number" && typeof parsed.longitude === "number"
      ? {
          latitude: parsed.latitude,
          longitude: parsed.longitude,
          ...(typeof parsed.GPSHPositioningError === "number"
            ? { accuracy: parsed.GPSHPositioningError }
            : {}),
        }
      : null;

  const make = trimOrUndef(parsed.Make);
  const model = trimOrUndef(parsed.Model);
  const software = trimOrUndef(parsed.Software);
  const camera =
    make || model || software
      ? {
          ...(make ? { make } : {}),
          ...(model ? { model } : {}),
          ...(software ? { software } : {}),
        }
      : null;

  return {
    gps,
    capturedAt: toIso(parsed.DateTimeOriginal ?? parsed.CreateDate),
    orientation: typeof parsed.Orientation === "number" ? parsed.Orientation : null,
    width: parsed.ExifImageWidth ?? parsed.PixelXDimension ?? parsed.ImageWidth ?? null,
    height: parsed.ExifImageHeight ?? parsed.PixelYDimension ?? parsed.ImageHeight ?? null,
    camera,
    extractedAt,
  };
}

const EARTH_RADIUS_M = 6371008.8;

export function metersBetween(
  a: { latitude: number; longitude: number },
  b: { latitude: number; longitude: number },
): number {
  const toRad = (deg: number) => (deg * Math.PI) / 180;
  const dLat = toRad(b.latitude - a.latitude);
  const dLng = toRad(b.longitude - a.longitude);
  const lat1 = toRad(a.latitude);
  const lat2 = toRad(b.latitude);
  const h = Math.sin(dLat / 2) ** 2 + Math.cos(lat1) * Math.cos(lat2) * Math.sin(dLng / 2) ** 2;
  return 2 * EARTH_RADIUS_M * Math.asin(Math.min(1, Math.sqrt(h)));
}

export const PHOTO_GPS_DIVERGENCE_M = 50;
